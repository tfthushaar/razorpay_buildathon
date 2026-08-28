"""Calibration / auto-resolve layer.

Applies only to narrator-classified decisions — duplicate_refund, netting_trap, genuine_error,
the three categories that ever reach "needs_narration" . Deterministic Pass 1/Pass 2
resolutions are arithmetic facts, not statistical estimates, so running a confidence interval over
them would be conceptually wrong (and could wrongly gate a provably-correct category on small-N
noise) — they're reported separately as exact, not calibrated. Calibration is reserved for
exactly the cases where "AI Judgment" is actually being exercised.

The threshold is checked against the CI *lower bound*, not the raw accuracy, and the whole
function is cheap enough to re-run on every threshold change (spec's "live dial" — see
docs/ARCHITECTURE.md) since it's just a re-aggregation over
already-scored decisions, not a re-run of the pipeline.

IMPORTANT — provider-aware gating (added 2026-08-24, after an external audit caught this): only
decisions from a real LLM provider (`provider != "mock"`) count toward accuracy/CI/the auto-resolve
decision. Mock mode is a deterministic rule-based stand-in for zero-cost pipeline testing (see
narrator/agent.py) — it is not AI judgment, and letting it accumulate toward "the AI has proven
itself accurate on this category" would make the auto-resolve gate satisfiable with zero real
model involvement. This was caught empirically: 6 consecutive mock-mode batch runs alone crossed
the 90% Wilson-lower-bound threshold for netting_trap with no LLM ever having been called. Mock
decisions are still recorded and reported (`mock_n`) for transparency, but never gate autonomy.
"""

from typing import Literal

from pydantic import BaseModel

from app.calibration.drift import detect_drift
from app.calibration.wilson import wilson_score_interval

# Escalation *is* the correct resolution for genuine_error by definition ("a genuinely
# ambiguous case with no clean explanation... should escalate, not guess") — no accuracy number,
# however high, makes auto-resolving an admittedly-unexplained case the right move.
NEVER_AUTO_RESOLVE = {"genuine_error"}

DEFAULT_THRESHOLD = 0.90

# generate() is fully deterministic per seed (verified directly: three independent generate(seed=42,
# ...) calls produce the identical 18-transaction narration queue, every time) and CalibrationHistory
# never deduplicates by transaction_id -- an external audit 2026-08-24 (round 13) proved this is a
# real gaming vector, no threading race required: repeatedly clicking "Run batch" on the same
# (default) seed re-observes the SAME small set of cases and inflates `n` with correlated, not
# independent, samples. Found live in this project's own committed evidence:
# docs/evidence/real-ollama-run-2026-08-24.json reports duplicate_refund n=15, but seed=42 only ever
# produces 4 DISTINCT duplicate_refund transactions -- the accumulated 15 could only have come from
# re-scoring those same 4 cases across multiple runs. The Wilson bound alone can't catch this: given
# enough repeated (not independent) trials at a genuinely high per-case accuracy, ci_lower approaches
# the point estimate and would eventually clear 90% even with zero real distinct-case diversity. This
# floor requires genuine variety of evidence (different seeds, different real-world cases) before
# autonomy is granted, on top of the existing statistical-confidence requirement, not instead of it.
MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE = 15


class ScoredDecision(BaseModel):
    transaction_id: str
    predicted_category: str
    true_label: str  # scoring-only; never influences the predicted_category itself
    amount: int  # settlement amount in the smallest currency unit
    provider: str  # "mock" | "groq" | ... -- only non-mock decisions count toward the auto-resolve gate


class CategoryCalibration(BaseModel):
    category: str
    n: int  # real-provider (non-mock) decisions only -- this is what accuracy/CI/decision are based on
    correct: int  # correct among the real-provider decisions counted in n
    accuracy: float
    ci_lower: float
    ci_upper: float
    decision: Literal["auto_resolve", "escalate"]
    reason: str
    amount_total: int  # total amount across ALL decisions (real + mock), for reporting -- NOTE: a
    # category re-scored across many runs (same transactions observed repeatedly) sums the same
    # rupee amount once per observation, not once per distinct transaction. Real, distinct money
    # is distinct_amount_total below; don't headline this field as "money resolved" (a real external
    # audit caught exactly that misuse in this project's own README -- see BUILD_LOG.md 2026-08-25).
    distinct_amount_total: int  # sum of amount across DISTINCT real-provider transaction_ids only
    # (first occurrence each) -- the honest "real money behind these decisions" figure, immune to
    # the same re-scoring inflation amount_total has. This is what a headline claim should quote.
    amount_at_risk: int  # expected wrongly-auto-resolved amount at this threshold; 0 if escalated.
    # Computed off distinct_amount_total, not amount_total -- risk exposure should reflect real
    # distinct money, not an artifact of how many times the same transactions got re-scored.
    mock_n: int  # mock-mode decisions seen for this category -- tracked, never counted toward the gate
    distinct_transaction_count: int  # DISTINCT real-provider transaction_ids behind n -- n itself can
    # over-count the same re-scored case; this is what MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE
    # actually gates on, so the dashboard can show a judge the difference between "n=15 decisions"
    # and "15 different real-world cases" instead of leaving the two indistinguishable.
    ewma_accuracy: float  # recency-weighted accuracy (app/calibration/drift.py) -- reacts to a
    # recent regression far faster than `accuracy` (an all-time aggregate) can; equals `accuracy`
    # itself when there isn't yet enough real-provider evidence for a meaningful drift check.
    drift_alert: bool  # True if ewma_accuracy has fallen below its statistical control limit --
    # an ADDITIONAL gate on top of the Wilson CI and distinct-transaction floor, not instead of them.


class CalibrationReport(BaseModel):
    threshold: float
    categories: list[CategoryCalibration]

    @property
    def total_amount_at_risk(self) -> int:
        return sum(c.amount_at_risk for c in self.categories)

    @property
    def auto_resolve_categories(self) -> list[str]:
        return [c.category for c in self.categories if c.decision == "auto_resolve"]


def calibrate(decisions: list[ScoredDecision], threshold: float = DEFAULT_THRESHOLD) -> CalibrationReport:
    by_category: dict[str, list[ScoredDecision]] = {}
    for d in decisions:
        by_category.setdefault(d.predicted_category, []).append(d)

    categories: list[CategoryCalibration] = []
    for category, items in sorted(by_category.items()):
        real_items = [d for d in items if d.provider != "mock"]
        mock_n = len(items) - len(real_items)
        n = len(real_items)
        correct = sum(1 for d in real_items if d.predicted_category == d.true_label)
        accuracy = correct / n if n else 0.0
        ci_lower, ci_upper = wilson_score_interval(correct, n)
        amount_total = sum(d.amount for d in items)
        distinct_transaction_count = len({d.transaction_id for d in real_items})
        # First-occurrence-per-transaction_id, not a second sum-then-dedupe pass -- a transaction
        # re-scored across multiple runs contributes its amount exactly once here regardless of n.
        seen_transaction_ids: set[str] = set()
        distinct_amount_total = 0
        for d in real_items:
            if d.transaction_id not in seen_transaction_ids:
                seen_transaction_ids.add(d.transaction_id)
                distinct_amount_total += d.amount

        # real_items is chronological (history.py's SELECT is explicitly ORDER BY id ASC) so this
        # outcome sequence genuinely reflects "oldest real-provider decision first" -- EWMA drift
        # detection is meaningless on shuffled input, so that ordering guarantee is load-bearing.
        outcomes = [d.predicted_category == d.true_label for d in real_items]
        drift = detect_drift(outcomes, target=accuracy)

        if category in NEVER_AUTO_RESOLVE:
            decision: Literal["auto_resolve", "escalate"] = "escalate"
            reason = "escalation is the correct resolution for this category by definition, regardless of measured accuracy"
        elif n == 0:
            decision = "escalate"
            reason = f"no real-provider decisions yet for this category (mock_n={mock_n}) — mock-mode never counts toward auto-resolve"
        elif distinct_transaction_count < MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE:
            decision = "escalate"
            reason = (
                f"only {distinct_transaction_count} distinct transaction(s) behind these {n} real-provider "
                f"decisions — the same case(s) re-scored across multiple runs don't count as new evidence; "
                f"needs at least {MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE} distinct cases before "
                f"auto-resolve is considered, regardless of the CI"
            )
        elif drift.breached:
            decision = "escalate"
            reason = (
                f"recent-decision accuracy (EWMA {drift.ewma:.1%}) has fallen below its statistical "
                f"control limit ({drift.lower_control_limit:.1%}) even though the all-time aggregate "
                f"({accuracy:.1%}) still looks fine — this category may be genuinely regressing right "
                f"now, not just historically noisy; escalating until recent decisions recover"
            )
        elif ci_lower >= threshold:
            decision = "auto_resolve"
            reason = (
                f"95% CI lower bound {ci_lower:.1%} clears the {threshold:.0%} threshold "
                f"(n={n} real-provider decisions across {distinct_transaction_count} distinct transactions)"
            )
        else:
            decision = "escalate"
            reason = (
                f"95% CI lower bound {ci_lower:.1%} has not cleared {threshold:.0%} yet "
                f"(n={n} real-provider decisions across {distinct_transaction_count} distinct transactions)"
            )

        amount_at_risk = round((1 - accuracy) * distinct_amount_total) if decision == "auto_resolve" else 0

        categories.append(
            CategoryCalibration(
                category=category,
                n=n,
                correct=correct,
                accuracy=accuracy,
                ci_lower=ci_lower,
                ci_upper=ci_upper,
                decision=decision,
                reason=reason,
                amount_total=amount_total,
                distinct_amount_total=distinct_amount_total,
                amount_at_risk=amount_at_risk,
                mock_n=mock_n,
                distinct_transaction_count=distinct_transaction_count,
                ewma_accuracy=drift.ewma,
                drift_alert=drift.breached,
            )
        )

    return CalibrationReport(threshold=threshold, categories=categories)
