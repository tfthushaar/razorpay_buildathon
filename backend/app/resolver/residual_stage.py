"""The residual stage as the pipeline runs it: Layer 0 over everything, the model over what is left.

Kept in its own module rather than inlined into app/pipeline.py because the honest accounting here is
the whole point, and it deserves to be readable in one place. Three numbers come out of a run and
each one is load-bearing:

  layer0_resolved     transactions Layer 0 explained on its own, with exactly one valid decomposition.
                      No model was called. This is the number that makes every other number on this
                      page defensible: a case a rule could solve was taken by the rule, so it cannot
                      be sitting inside a model's accuracy figure inflating it.

  under_determined    Layer 0 found k >= 2 valid explanations. Blind choice scores exactly 1/k, which
                      is reported alongside, so the model is measured against a computed floor rather
                      than a rhetorical one.

  unmatched           Layer 0 found nothing. The harder half, scored separately -- blending it into
                      one accuracy number would let a good result on one hide a bad one on the other.

Gated behind the same opt-in flag that generates compound cases, so a run without it behaves exactly
as this pipeline did before and every already-committed evidence file stays valid.
"""

from pydantic import BaseModel, computed_field

from app.chain.builder import CausalChain
from app.narrator.attribution import attribute
from app.narrator.tools import ToolContext
from app.resolver.enumerate import build_candidate_pool
from app.resolver.keyword_baseline import best_decomposition_by_advice
from app.resolver.resolver import DEFAULT_TOLERANCE_PAISE, most_parsimonious, present_options, resolve


class ResidualComponent(BaseModel):
    cause: str
    amount: int
    evidence_ref: str
    why: str = ""


class ResidualCase(BaseModel):
    transaction_id: str
    status: str
    observed_delta: int
    ambiguity: int
    chance_baseline: float
    candidate_pool_size: int
    reached_model: bool
    provider: str = ""
    verified: bool = False
    verify_rounds: int = 0
    components: list[ResidualComponent] = []
    reasoning: str = ""
    # what the two zero-cost rules would have answered on the identical option list -- carried per
    # case so a results table can be rebuilt from a run without re-running any of it
    parsimony_choice: list[ResidualComponent] = []
    keyword_choice: list[ResidualComponent] = []
    keyword_ties: int = 0


class ResidualReport(BaseModel):
    tolerance: int
    # transactions the matching engine already closed before this stage ran at all. Carried here so
    # the funnel reads correctly end to end: the deterministic share of a batch is overwhelmingly
    # this number, and quoting Layer 0's decomposition resolver in isolation would understate the
    # deterministic layer while overstating how much work reaches a model.
    closed_before_stage: int = 0
    layer0_resolved: int
    under_determined: int
    unmatched: int
    model_calls: int
    model_verified: int
    cases: list[ResidualCase] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return self.layer0_resolved + self.under_determined + self.unmatched

    @computed_field  # type: ignore[prop-decorator]
    @property
    def deterministic_share(self) -> float:
        """Fraction of the WHOLE batch closed without any model call -- the matching engine's own
        work plus whatever Layer 0's decomposition resolver could finish on top of it."""
        denominator = self.closed_before_stage + self.total
        return (self.closed_before_stage + self.layer0_resolved) / denominator if denominator else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def layer0_share_of_exceptions(self) -> float:
        """Of the exceptions that actually reached this stage, the fraction Layer 0 closed alone.

        Measured at 0 on compound settlement data, which is the expected and honest result rather
        than a defect: a delta produced by several compounding causes is under-determined almost by
        construction (see app/resolver/__init__.py), so the decomposition resolver's job here is to
        establish WHICH cases are unanswerable by arithmetic and how unanswerable each one is, not to
        close them. The cases arithmetic can close were closed upstream, by the matching engine."""
        return self.layer0_resolved / self.total if self.total else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mean_chance_baseline(self) -> float:
        under = [c for c in self.cases if c.status == "UNDER_DETERMINED"]
        return sum(c.chance_baseline for c in under) / len(under) if under else 0.0


def _as_components(decomposition, why: str = "") -> list[ResidualComponent]:
    if decomposition is None:
        return []
    return [ResidualComponent(cause=c.cause, amount=c.amount, evidence_ref=c.evidence_ref, why=why or c.detail) for c in decomposition.components]


def run_residual_stage(
    chains: dict[str, CausalChain],
    context: ToolContext,
    transaction_ids: list[str],
    provider: str | None = None,
    tolerance: int = DEFAULT_TOLERANCE_PAISE,
    model: str | None = None,
    closed_before_stage: int = 0,
) -> ResidualReport:
    layer0_resolved = under_determined = unmatched = model_calls = model_verified = 0
    cases: list[ResidualCase] = []

    for txn_id in transaction_ids:
        chain = chains[txn_id]
        out = resolve(chain, context, tolerance=tolerance)

        case = ResidualCase(
            transaction_id=txn_id,
            status=out.status,
            observed_delta=out.observed_delta,
            ambiguity=out.ambiguity,
            chance_baseline=out.chance_baseline,
            candidate_pool_size=out.candidate_pool_size,
            reached_model=False,
        )

        if out.status == "RESOLVED":
            # the whole promise of the architecture, executed: one valid explanation, so the
            # deterministic layer keeps the case and no model is called for it
            layer0_resolved += 1
            case.components = _as_components(out.decompositions[0] if out.decompositions else None)
            case.verified = True
            cases.append(case)
            continue

        if out.status == "UNDER_DETERMINED":
            under_determined += 1
            options = present_options(out.decompositions, txn_id, limit=40)
            best, tied = best_decomposition_by_advice(options, chain.bank_narration)
            case.keyword_choice = _as_components(best)
            case.keyword_ties = tied
            case.parsimony_choice = _as_components(most_parsimonious(out.decompositions))
        else:
            unmatched += 1

        pool = build_candidate_pool(chain, context)
        result = attribute(chain, context, out, pool, provider=provider, tolerance=tolerance, model=model)
        model_calls += 1
        model_verified += result.verified
        case.reached_model = True
        case.provider = result.provider
        case.verified = result.verified
        case.verify_rounds = result.verify_rounds_used
        case.reasoning = result.reasoning
        case.components = [ResidualComponent(cause=c.cause, amount=c.amount, evidence_ref=c.evidence_ref, why=c.detail) for c in result.components]
        cases.append(case)

    return ResidualReport(
        tolerance=tolerance,
        closed_before_stage=closed_before_stage,
        layer0_resolved=layer0_resolved,
        under_determined=under_determined,
        unmatched=unmatched,
        model_calls=model_calls,
        model_verified=model_verified,
        cases=cases,
    )
