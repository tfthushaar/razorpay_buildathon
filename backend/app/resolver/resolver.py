"""Layer 0 proper: run the deterministic resolver, and classify what it could not finish.

    RESOLVED          exactly one arithmetically valid explanation (or a delta of zero). The rule
                      wins outright. The model never sees this case at all.
    UNDER_DETERMINED  two or more arithmetically valid explanations. Every one of them sums to the
                      observed delta within tolerance and cites real objects; arithmetic has no
                      remaining basis for choosing between them.
    UNMATCHED         zero valid explanations. The delta is real and the resolver's whole candidate
                      pool cannot account for it.

The consequence is structural rather than argued: a case the rule solved is *taken* by the rule and
never reaches Layer 2, so no accuracy figure this project reports on the residual can contain a case
a rule could have handled. That is the property the whole architecture exists to buy, and it is worth
stating exactly what it does and doesn't prove -- it does not prove the model is good, it proves the
measurement is clean. Whether the model is any good is a separate empirical question, answered
against the 1/k chance line below.

`chance_baseline` is why UNDER_DETERMINED is the interesting half of the residual. With k valid
decompositions and no basis to prefer any, blind choice is exactly 1/k -- a real, computed, arguable-
with-nobody baseline rather than a strawman comparator. Reporting the model against 1/k is a much
harder test than reporting it against a naive rule, and it is the number this project leads with.
"""

from typing import Literal

from pydantic import BaseModel

from app.chain.builder import CausalChain
from app.resolver.causes import Decomposition
from app.resolver.enumerate import build_candidate_pool, enumerate_decompositions

ResolverStatus = Literal["RESOLVED", "UNDER_DETERMINED", "UNMATCHED"]

# Real settlement arithmetic carries rounding: percentage withholdings are rounded to the paise at
# several independent steps. A reconciler that demands exact zero rejects genuinely-correct
# explanations, so the default tolerance is non-zero -- but deliberately tight, 10 paise, a tenth of
# a rupee. It is NOT set wide to manufacture ambiguity, and the measurement says it doesn't need to
# be: at tolerance 0 against a generator with zero rounding noise, 45 of 60 compound cases are still
# under-determined (median 3.5 valid decompositions). Compositionality is what makes this problem
# under-determined; the tolerance only amplifies it. docs/RESULTS.md publishes the whole curve rather
# than the one row that flatters the architecture.
DEFAULT_TOLERANCE_PAISE = 10


class ResolverOutput(BaseModel):
    transaction_id: str
    status: ResolverStatus
    observed_delta: int
    tolerance: int
    decompositions: list[Decomposition]
    candidate_pool_size: int
    truncated: bool = False

    @property
    def ambiguity(self) -> int:
        """k: how many arithmetically valid explanations Layer 0 found."""
        return len(self.decompositions)

    @property
    def chance_baseline(self) -> float:
        """Probability that blind choice among Layer 0's valid decompositions is correct.

        Defined only where choosing is actually required; 1.0 for RESOLVED (nothing to choose) and
        0.0 for UNMATCHED (nothing to choose *from*, so no amount of guessing succeeds)."""
        if self.status == "RESOLVED":
            return 1.0
        if self.status == "UNMATCHED" or not self.decompositions:
            return 0.0
        return 1.0 / len(self.decompositions)

    @property
    def is_residual(self) -> bool:
        return self.status != "RESOLVED"


def rank_decompositions(decompositions: list[Decomposition], limit: int = 40) -> list[Decomposition]:
    """Layer 0's valid answers ordered by parsimony (fewest components first), capped at `limit`.

    Used to decide WHICH answers are worth showing when k runs large -- the measured median is ~23
    but the tail reaches the hundreds. Parsimony is a real prior over explanations and is computed
    purely from the candidates, so windowing on it consults no property of the true answer.

    This is deliberately NOT the order anything is presented in; see `present_options`.
    """
    ordered = sorted(
        decompositions,
        key=lambda d: (len(d.components), tuple(sorted((c.cause, c.amount) for c in d.components))),
    )
    return ordered[:limit]


def present_options(decompositions: list[Decomposition], transaction_id: str, limit: int = 40) -> list[Decomposition]:
    """The window above, deterministically shuffled before anyone sees it.

    The shuffle is not cosmetic, and it was added after the measurement caught the problem it fixes.
    Presenting in parsimony order leaks the answer through POSITION: the true decomposition really is
    usually among the most parsimonious, so on a live 10-case run the true answer sat at position 1
    in 5 of them. Any chooser -- a model with a mild first-option bias, or a rule whose tie-break
    happens to be parsimony -- would then score well for a reason that has nothing to do with reading
    the evidence, and the whole comparison would be measuring position rather than judgment.

    "Pick the most parsimonious option" is a legitimate and, as it turns out, strong heuristic. It
    belongs in the results table as its OWN baseline column, which is where it now is -- not smuggled
    into everyone else's score through the presentation order.

    Seeded on the transaction id so a case presents identically on every run and across providers.
    """
    import random

    windowed = rank_decompositions(decompositions, limit=limit)
    shuffled = list(windowed)
    random.Random(f"present:{transaction_id}").shuffle(shuffled)
    return shuffled


def most_parsimonious(decompositions: list[Decomposition]) -> Decomposition | None:
    """The simplest-explanation baseline, as its own column. Occam's razor is the oldest
    reconciliation heuristic there is and it costs nothing; if it beats everything else, that is the
    finding and it gets reported as the finding."""
    ranked = rank_decompositions(decompositions, limit=1)
    return ranked[0] if ranked else None


def resolve(
    chain: CausalChain,
    context,
    tolerance: int = DEFAULT_TOLERANCE_PAISE,
    max_components: int = 4,
    include_netting: bool = True,
) -> ResolverOutput:
    delta = chain.settlement_delta
    pool = build_candidate_pool(chain, context, include_netting=include_netting)

    if delta == 0:
        return ResolverOutput(
            transaction_id=chain.transaction_id,
            status="RESOLVED",
            observed_delta=0,
            tolerance=tolerance,
            decompositions=[Decomposition(components=[], observed_delta=0)],
            candidate_pool_size=len(pool),
        )

    decompositions, truncated = enumerate_decompositions(
        delta, pool, tolerance=tolerance, max_components=max_components
    )

    if not decompositions:
        status: ResolverStatus = "UNMATCHED"
    elif len(decompositions) == 1:
        status = "RESOLVED"
    else:
        status = "UNDER_DETERMINED"

    return ResolverOutput(
        transaction_id=chain.transaction_id,
        status=status,
        observed_delta=delta,
        tolerance=tolerance,
        decompositions=decompositions,
        candidate_pool_size=len(pool),
        truncated=truncated,
    )
