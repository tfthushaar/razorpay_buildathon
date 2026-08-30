"""What the autonomy threshold buys and what it costs, as one curve instead of one number.

Escalating a case a machine is not confident about is selective prediction: a system that may abstain
rather than answer, and is scored on the answers it does give. The field reports it as a
risk-coverage curve, and that is a better description of this project's gate than the single
pass/fail at 90% the dashboard has been showing.

    coverage        the share of decisions the system closes without a human
    selective risk  the error rate among ONLY those decisions

The pair matters because either alone is trivial to make look good. A gate at 100% automates nothing
and has no risk. A gate at 0% automates everything at the model's raw error rate. The interesting
question is the shape between them, and specifically where the curve turns: the coverage at which
risk starts climbing is the coverage this system should actually run at.

Two things stop this being a coverage-vs-accuracy plot with a new name.

Coverage is counted per CATEGORY, because that is how the gate works: a category clears the bar or it
does not, and every decision in it follows. So the curve is a step function with as many steps as
there are categories, not a smooth trade-off, and reporting it smooth would imply a dial that does
not exist.

The bound is the anytime-valid one from `confidence_sequence.py`, not Wilson, for the same reason the
gate uses it: the threshold is re-checked as evidence accumulates.

Method from El-Yaniv and Wiener's selective-prediction framework. See docs/CREDITS.md.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.calibration.calibrator import NEVER_AUTO_RESOLVE, ScoredDecision
from app.calibration.confidence_sequence import lower_bound_from_outcomes


class RiskCoveragePoint(BaseModel):
    threshold: float
    coverage: float  # share of real-provider decisions inside auto-resolving categories
    selective_risk: float  # error rate among those decisions only
    n_covered: int
    n_total: int
    auto_categories: list[str]
    amount_covered: int  # distinct money inside the covered categories


class RiskCoverageCurve(BaseModel):
    points: list[RiskCoveragePoint]

    @property
    def max_coverage_at_zero_risk(self) -> float:
        """The most this system can automate without a single error on the automated set.

        The honest headline for a selective predictor, and the number a finance team would ask for
        before anything about accuracy.
        """
        clean = [p.coverage for p in self.points if p.selective_risk == 0.0]
        return max(clean) if clean else 0.0


def risk_coverage_curve(
    decisions: list[ScoredDecision],
    thresholds: tuple[float, ...] = (0.50, 0.70, 0.80, 0.85, 0.86, 0.87, 0.88, 0.89, 0.90, 0.95),
) -> RiskCoverageCurve:
    """Sweep the autonomy threshold and report coverage and risk at each setting.

    Mock decisions are excluded exactly as the gate excludes them: a provider that cannot be wrong in
    an interesting way should not be able to buy coverage.
    """
    real = [d for d in decisions if d.provider != "mock"]
    by_category: dict[str, list[ScoredDecision]] = {}
    for d in real:
        by_category.setdefault(d.predicted_category, []).append(d)

    # One bound per category, computed once: the threshold sweep only changes what it is compared to.
    bounds: dict[str, float] = {}
    for category, items in by_category.items():
        outcomes = [d.predicted_category == d.true_label for d in items]
        bounds[category] = lower_bound_from_outcomes(outcomes)

    n_total = len(real)
    points: list[RiskCoveragePoint] = []
    for threshold in thresholds:
        covered_categories = sorted(
            c for c, bound in bounds.items() if c not in NEVER_AUTO_RESOLVE and bound >= threshold
        )
        covered = [d for c in covered_categories for d in by_category[c]]
        errors = sum(1 for d in covered if d.predicted_category != d.true_label)

        seen: set[str] = set()
        amount = 0
        for d in covered:
            if d.transaction_id not in seen:
                seen.add(d.transaction_id)
                amount += d.amount

        points.append(
            RiskCoveragePoint(
                threshold=threshold,
                coverage=round(len(covered) / n_total, 4) if n_total else 0.0,
                selective_risk=round(errors / len(covered), 4) if covered else 0.0,
                n_covered=len(covered),
                n_total=n_total,
                auto_categories=covered_categories,
                amount_covered=amount,
            )
        )
    return RiskCoverageCurve(points=points)
