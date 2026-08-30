"""The risk-coverage curve, and the non-obvious thing it exposed.

A single pass/fail at 90% hides the shape of the trade-off. Sweeping the threshold shows it, and on
this project's own committed history it shows something a single number could not: raising the gate
can make the automated set WORSE, because the bound rewards sample size and not only accuracy.
"""

import pytest

from app.calibration.calibrator import ScoredDecision
from app.calibration.risk_coverage import risk_coverage_curve


def _decisions(category: str, n: int, wrong: int = 0, provider: str = "groq", amount: int = 1000_00):
    out = []
    for i in range(n):
        out.append(
            ScoredDecision(
                transaction_id=f"{category}{i}",
                predicted_category=category,
                true_label="genuine_error" if i < wrong else category,
                amount=amount,
                provider=provider,
            )
        )
    return out


# --- the shape of the curve --------------------------------------------------------------------------


def test_coverage_never_increases_as_the_gate_rises():
    curve = risk_coverage_curve(_decisions("netting_trap", 80) + _decisions("duplicate_refund", 40, wrong=2))
    coverages = [p.coverage for p in curve.points]
    assert coverages == sorted(coverages, reverse=True)


def test_a_stricter_gate_can_raise_selective_risk():
    """The finding. A perfect category with few decisions scores a LOWER bound than a nearly-perfect
    category with many, so raising the threshold can drop the perfect one and keep the flawed one.

    Small-but-perfect (30/30) against large-but-flawed (90/91): the bound rewards evidence, so the
    flawed category survives a higher bar and the automated set gets worse.
    """
    decisions = _decisions("duplicate_refund", 30) + _decisions("netting_trap", 91, wrong=1)
    curve = risk_coverage_curve(decisions, thresholds=(0.50, 0.80, 0.86, 0.90, 0.95))
    covered = [p for p in curve.points if p.n_covered > 0]
    assert covered, "fixture covers nothing at any threshold"
    risks = [p.selective_risk for p in covered]
    assert max(risks) > min(risks), "this fixture no longer exercises the effect"


def test_selective_risk_counts_only_the_automated_decisions():
    """The whole point of the measure. Errors in escalated categories are a human's problem, not the
    system's risk, and folding them in would report a number nobody is exposed to."""
    decisions = _decisions("netting_trap", 80) + _decisions("genuine_error", 40, wrong=40)
    curve = risk_coverage_curve(decisions, thresholds=(0.50,))
    point = curve.points[0]
    assert "genuine_error" not in point.auto_categories
    assert point.selective_risk == 0.0


def test_a_never_auto_resolve_category_is_never_covered():
    curve = risk_coverage_curve(_decisions("genuine_error", 200), thresholds=(0.50, 0.90))
    assert all(p.n_covered == 0 for p in curve.points)


# --- the gate's own exclusions still apply ------------------------------------------------------------


def test_mock_decisions_cannot_buy_coverage():
    curve = risk_coverage_curve(_decisions("netting_trap", 200, provider="mock"), thresholds=(0.50,))
    assert curve.points[0].n_total == 0
    assert curve.points[0].coverage == 0.0


def test_money_is_counted_once_per_transaction():
    """A transaction re-scored across runs must not inflate the covered amount, the same bug an
    external review caught in the README's headline figure."""
    decisions = _decisions("netting_trap", 80) * 3
    curve = risk_coverage_curve(decisions, thresholds=(0.50,))
    assert curve.points[0].amount_covered == 80 * 1000_00


def test_an_empty_history_produces_a_flat_curve():
    curve = risk_coverage_curve([])
    assert all(p.coverage == 0.0 and p.n_covered == 0 for p in curve.points)


def test_zero_risk_coverage_reports_the_cleanest_reachable_point():
    curve = risk_coverage_curve(_decisions("netting_trap", 120), thresholds=(0.50, 0.90))
    assert curve.max_coverage_at_zero_risk == pytest.approx(1.0)
