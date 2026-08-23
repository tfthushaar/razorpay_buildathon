"""Calibration / auto-resolve layer (spec §6.5).

Applies only to narrator-classified decisions — duplicate_refund, netting_trap, genuine_error,
the three categories that ever reach "needs_narration" (spec §6.3). Deterministic Pass 1/Pass 2
resolutions are arithmetic facts, not statistical estimates, so running a confidence interval over
them would be conceptually wrong (and could wrongly gate a provably-correct category on small-N
noise) — they're reported separately as exact, not calibrated. Calibration is reserved for
exactly the cases where "AI Judgment" is actually being exercised.

The threshold is checked against the CI *lower bound*, not the raw accuracy, and the whole
function is cheap enough to re-run on every threshold change (spec's "live dial" — see
docs/track04-settlement-reconciliation-copilot.md §6.5) since it's just a re-aggregation over
already-scored decisions, not a re-run of the pipeline.
"""

from typing import Literal

from pydantic import BaseModel

from app.calibration.wilson import wilson_score_interval

# Escalation *is* the correct resolution for genuine_error by definition (spec §4: "a genuinely
# ambiguous case with no clean explanation... should escalate, not guess") — no accuracy number,
# however high, makes auto-resolving an admittedly-unexplained case the right move.
NEVER_AUTO_RESOLVE = {"genuine_error"}

DEFAULT_THRESHOLD = 0.90


class ScoredDecision(BaseModel):
    transaction_id: str
    predicted_category: str
    true_label: str  # scoring-only; never influences the predicted_category itself
    amount: int  # settlement amount in the smallest currency unit


class CategoryCalibration(BaseModel):
    category: str
    n: int
    correct: int
    accuracy: float
    ci_lower: float
    ci_upper: float
    decision: Literal["auto_resolve", "escalate"]
    reason: str
    amount_total: int
    amount_at_risk: int  # expected wrongly-auto-resolved amount at this threshold; 0 if escalated


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
        n = len(items)
        correct = sum(1 for d in items if d.predicted_category == d.true_label)
        accuracy = correct / n if n else 0.0
        ci_lower, ci_upper = wilson_score_interval(correct, n)
        amount_total = sum(d.amount for d in items)

        if category in NEVER_AUTO_RESOLVE:
            decision: Literal["auto_resolve", "escalate"] = "escalate"
            reason = "escalation is the correct resolution for this category by definition, regardless of measured accuracy"
        elif ci_lower >= threshold:
            decision = "auto_resolve"
            reason = f"95% CI lower bound {ci_lower:.1%} clears the {threshold:.0%} threshold (n={n})"
        else:
            decision = "escalate"
            reason = f"95% CI lower bound {ci_lower:.1%} has not cleared {threshold:.0%} yet (n={n})"

        amount_at_risk = round((1 - accuracy) * amount_total) if decision == "auto_resolve" else 0

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
                amount_at_risk=amount_at_risk,
            )
        )

    return CalibrationReport(threshold=threshold, categories=categories)
