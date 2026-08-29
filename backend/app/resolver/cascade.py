"""Cascade routing: each tier handles only what the tier below it couldn't, and reports its own cost.

The residual architecture already establishes that a case reaching a model is one the deterministic
layer could not close. This extends the same principle downward through the models themselves: try
the cheapest thing that might work, escalate only what it can't do, and publish cost and latency per
*resolved* transaction at each tier rather than per call. Cost per call flatters an expensive tier
that rarely fires; cost per resolution is what an operations budget actually experiences.

    TIER 0  the keyword rule          ~0ms, free
    TIER 1  qwen2.5:7b-instruct       fast, weaker reader
    TIER 2  qwen2.5:14b-instruct      slower, better reader
    TIER 3  escalate to a human

The routing question this exists to answer is when to hand a case up, and the honest answer this
project's own measurements give is that it is NOT "when the tier is unsure". Self-reported confidence
was uninformative in every earlier measurement here -- high on wrong answers as readily as right ones
-- so a cascade keyed on it would escalate near-randomly. What is available instead is genuinely
informative and costs nothing to compute:

  the verifier      a tier whose decomposition fails arithmetic or citation grounding has demonstrably
                    not solved the case, whatever it claims about itself
  the tie count     when the advice leaves the rule tied across many equally-agreeing options
                    (`keyword_ties`), the rule is guessing, and it says so rather than hiding it
  the ambiguity k   a case with 40 valid explanations is a different problem from one with 2

Tier 0 escalates on the second of those, which is the one measurement that made a cascade worth
building at all: the rule is not uniformly weak, it is *specifically* weak where the advice does not
discriminate, and that is detectable in advance without asking a model anything.
"""

import time

from pydantic import BaseModel, computed_field

from app.chain.builder import CausalChain
from app.narrator.attribution import OPTION_WINDOW, attribute
from app.narrator.tools import ToolContext
from app.resolver.enumerate import build_candidate_pool
from app.resolver.keyword_baseline import best_decomposition_by_advice
from app.resolver.resolver import DEFAULT_TOLERANCE_PAISE, ResolverOutput, present_options
from app.resolver.verifier import verify_decomposition

# Above this many equally-advice-agreeing options, tier 0 is choosing by parsimony alone rather than
# by anything it read, so it hands the case up instead of reporting a guess as an answer. 1 means "the
# advice picked a unique winner"; anything higher means it did not discriminate.
TIE_ESCALATION_THRESHOLD = 1


class TierResult(BaseModel):
    tier: str
    resolved_here: bool
    components: list[dict] = []
    seconds: float = 0.0
    reason: str = ""


class CascadeResult(BaseModel):
    transaction_id: str
    ambiguity: int
    tiers_tried: list[TierResult]
    final_tier: str
    components: list[dict] = []
    escalated_to_human: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_seconds(self) -> float:
        return round(sum(t.seconds for t in self.tiers_tried), 4)


def _dump(components) -> list[dict]:
    return [{"cause": c.cause, "amount": c.amount, "evidence_ref": c.evidence_ref} for c in components]


def route(
    chain: CausalChain,
    context: ToolContext,
    resolver_output: ResolverOutput,
    model_tiers: tuple[str, ...] = ("qwen2.5:7b-instruct", "qwen2.5:14b-instruct"),
    provider: str = "ollama_reader",
    tolerance: int = DEFAULT_TOLERANCE_PAISE,
    tie_threshold: int = TIE_ESCALATION_THRESHOLD,
) -> CascadeResult:
    tiers: list[TierResult] = []
    options = present_options(resolver_output.decompositions, chain.transaction_id, limit=OPTION_WINDOW)

    # --- tier 0: the free rule -------------------------------------------------------------------
    t0 = time.monotonic()
    best, tied = best_decomposition_by_advice(options, chain.bank_narration)
    elapsed = time.monotonic() - t0
    if best is not None and tied <= tie_threshold:
        verification = verify_decomposition(chain, context, list(best.components), tolerance=tolerance)
        if verification.passed:
            tiers.append(
                TierResult(
                    tier="keyword_rule",
                    resolved_here=True,
                    components=_dump(best.components),
                    seconds=elapsed,
                    reason=f"the advice picked a unique winner among {resolver_output.ambiguity} valid explanations",
                )
            )
            return CascadeResult(
                transaction_id=chain.transaction_id,
                ambiguity=resolver_output.ambiguity,
                tiers_tried=tiers,
                final_tier="keyword_rule",
                components=_dump(best.components),
                escalated_to_human=False,
            )
    tiers.append(
        TierResult(
            tier="keyword_rule",
            resolved_here=False,
            seconds=elapsed,
            reason=(
                f"the advice left {tied} options equally agreeing, so any answer here is a parsimony guess"
                if best is not None
                else "no valid explanation to choose from"
            ),
        )
    )

    # --- tiers 1..n: models, cheapest first --------------------------------------------------------
    pool = build_candidate_pool(chain, context)
    for model in model_tiers:
        t0 = time.monotonic()
        result = attribute(chain, context, resolver_output, pool, provider=provider, tolerance=tolerance, model=model)
        elapsed = time.monotonic() - t0
        if result.verified and result.components:
            tiers.append(
                TierResult(tier=model, resolved_here=True, components=_dump(result.components), seconds=elapsed, reason="verified")
            )
            return CascadeResult(
                transaction_id=chain.transaction_id,
                ambiguity=resolver_output.ambiguity,
                tiers_tried=tiers,
                final_tier=model,
                components=_dump(result.components),
                escalated_to_human=False,
            )
        tiers.append(
            TierResult(tier=model, resolved_here=False, seconds=elapsed, reason=result.last_failure[:200] or "did not verify")
        )

    return CascadeResult(
        transaction_id=chain.transaction_id,
        ambiguity=resolver_output.ambiguity,
        tiers_tried=tiers,
        final_tier="human",
        components=[],
        escalated_to_human=True,
    )
