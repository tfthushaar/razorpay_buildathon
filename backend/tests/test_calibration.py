"""Regression tests for the calibration / auto-resolve layer (spec §6.5)."""

from app.calibration.calibrator import ScoredDecision, calibrate
from app.calibration.wilson import wilson_score_interval


def test_wilson_interval_with_no_data_is_maximally_uncertain():
    lower, upper = wilson_score_interval(0, 0)
    assert (lower, upper) == (0.0, 1.0)


def test_wilson_interval_penalizes_small_samples():
    """Same 100% point accuracy, but the small-N case must have a much wider (lower) bound —
    this is the whole reason we gate on the interval, not the raw percentage."""
    small_lower, _ = wilson_score_interval(6, 6)
    large_lower, _ = wilson_score_interval(200, 200)
    assert small_lower < large_lower
    assert small_lower < 0.70  # 6/6 alone should not be trusted as "90%+ accurate"
    assert large_lower > 0.95  # 200/200 should be trusted


def test_genuine_error_always_escalates_even_at_100pct_accuracy():
    decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="genuine_error", true_label="genuine_error", amount=100_00)
        for i in range(50)
    ]
    report = calibrate(decisions, threshold=0.90)
    genuine = next(c for c in report.categories if c.category == "genuine_error")
    assert genuine.accuracy == 1.0
    assert genuine.decision == "escalate"
    assert genuine.amount_at_risk == 0


def test_high_accuracy_large_n_category_auto_resolves():
    # 100/102 correct (~98%): large enough N that the Wilson *lower bound* itself clears 90%,
    # not just the point estimate — a smaller N at the same accuracy (see the test above) would
    # correctly fail to clear it, which is exactly the behavior being protected here.
    decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="duplicate_refund", true_label="duplicate_refund", amount=500_00)
        for i in range(100)
    ] + [
        ScoredDecision(transaction_id=f"wrong{i}", predicted_category="duplicate_refund", true_label="netting_trap", amount=500_00)
        for i in range(2)
    ]
    report = calibrate(decisions, threshold=0.90)
    dup = next(c for c in report.categories if c.category == "duplicate_refund")
    assert dup.n == 102
    assert dup.decision == "auto_resolve"
    assert dup.amount_at_risk > 0  # the two wrong cases contribute real rupees-at-risk, not zero


def test_low_accuracy_category_escalates_and_has_no_risk_exposure():
    decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label=("netting_trap" if i < 5 else "genuine_error"), amount=200_00)
        for i in range(10)
    ]
    report = calibrate(decisions, threshold=0.90)
    netting = next(c for c in report.categories if c.category == "netting_trap")
    assert netting.decision == "escalate"
    assert netting.amount_at_risk == 0, "escalated categories should show zero risk exposure — nothing was auto-resolved"


def test_live_threshold_dial_changes_decision_without_new_data():
    """Simulates dragging the dashboard's threshold slider: same scored decisions, different
    threshold, cheap re-aggregation — this is what makes the dial 'live' rather than a re-run."""
    decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="duplicate_refund", true_label=("duplicate_refund" if i < 17 else "netting_trap"), amount=300_00)
        for i in range(20)
    ]
    loose = calibrate(decisions, threshold=0.60)
    strict = calibrate(decisions, threshold=0.95)
    loose_decision = next(c for c in loose.categories if c.category == "duplicate_refund").decision
    strict_decision = next(c for c in strict.categories if c.category == "duplicate_refund").decision
    assert loose_decision == "auto_resolve"
    assert strict_decision == "escalate"


def test_human_feedback_loop_can_flip_a_category_across_threshold():
    """A category that starts below threshold should be able to cross it as more human-confirmed
    resolutions accumulate — the calibration layer re-earning trust live (spec §6.5)."""
    decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00)
        for i in range(4)
    ]
    before = calibrate(decisions, threshold=0.85)
    assert next(c for c in before.categories if c.category == "netting_trap").decision == "escalate"

    # human resolves more escalated netting_trap cases and confirms the model was right each time
    for i in range(4, 30):
        decisions.append(
            ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00)
        )
    after = calibrate(decisions, threshold=0.85)
    assert next(c for c in after.categories if c.category == "netting_trap").decision == "auto_resolve"
