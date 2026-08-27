"""Tests for regret in rupees (app/calibration/regret.py) -- the REALIZED cost of calibrated
autonomy (only decisions actually auto-resolved while their category was already qualified, that
were actually wrong), not calibrator.py's forward-looking amount_at_risk estimate. Same
direct-construction style as test_calibration.py, using a temp CalibrationHistory the same way
test_pipeline.py's accumulated-history tests do."""

import tempfile
from pathlib import Path

from app.calibration.calibrator import MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE, ScoredDecision
from app.calibration.history import CalibrationHistory
from app.calibration.regret import compute_regret


def test_no_history_means_zero_regret_and_zero_hours():
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        report = compute_regret(history)
        history.close()
    assert report.realized_regret_amount == 0
    assert report.realized_regret_transaction_count == 0
    assert report.auto_resolved_transaction_count == 0
    assert report.estimated_analyst_hours_saved == 0.0


def test_decisions_before_a_category_qualifies_are_never_regret():
    """The floor is MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE distinct real cases -- every decision
    up to and including the one that first crosses it was escalated for real (a human saw it), so
    even a wrong one among them can't be a realized autonomy loss."""
    n = MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE - 1
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=1000_00, provider="groq") for i in range(n)]
            + [ScoredDecision(transaction_id="wrong1", predicted_category="netting_trap", true_label="genuine_error", amount=5000_00, provider="groq")]
        )
        report = compute_regret(history)
        history.close()

    assert report.realized_regret_amount == 0
    assert report.realized_regret_transaction_count == 0


def test_a_wrong_decision_after_qualifying_is_realized_regret():
    n = MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE + 20
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=1000_00, provider="groq") for i in range(n)]
            + [ScoredDecision(transaction_id="wrong_after", predicted_category="netting_trap", true_label="genuine_error", amount=7500_00, provider="groq")]
        )
        report = compute_regret(history)
        history.close()

    assert report.realized_regret_amount == 7500_00
    assert report.realized_regret_transaction_count == 1
    assert report.auto_resolved_transaction_count >= 1


def test_mock_decisions_are_never_counted_as_auto_resolved_or_regret():
    """pipeline.py's _final_decision requires provider != 'mock' even when a category qualifies --
    a mock decision is never actually auto-resolved, so it can never be realized regret either."""
    n = MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE + 20
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=1000_00, provider="groq") for i in range(n)]
            + [ScoredDecision(transaction_id="wrong_mock", predicted_category="netting_trap", true_label="genuine_error", amount=9999_00, provider="mock")]
        )
        report = compute_regret(history)
        history.close()

    assert report.realized_regret_amount == 0


def test_a_transaction_rescored_across_multiple_batches_is_not_double_counted():
    """Same discipline as calibrator.py's distinct_amount_total -- a case re-scored across multiple
    runs must contribute to realized regret at most once, not once per re-scoring."""
    n = MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE + 20
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=1000_00, provider="groq") for i in range(n)]
        )
        for _ in range(3):
            history.add([ScoredDecision(transaction_id="repeated_wrong", predicted_category="netting_trap", true_label="genuine_error", amount=2000_00, provider="groq")])
        report = compute_regret(history)
        history.close()

    assert report.realized_regret_transaction_count == 1
    assert report.realized_regret_amount == 2000_00


def test_genuine_error_never_contributes_to_regret():
    """genuine_error is in NEVER_AUTO_RESOLVE by construction -- it can never qualify, so it can
    never generate realized regret regardless of volume or accuracy."""
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [ScoredDecision(transaction_id=f"t{i}", predicted_category="genuine_error", true_label="genuine_error", amount=1000_00, provider="groq") for i in range(50)]
        )
        report = compute_regret(history)
        history.close()

    assert report.realized_regret_amount == 0
    assert report.auto_resolved_transaction_count == 0


def test_estimated_hours_saved_is_a_disclosed_assumption_not_a_measured_fact():
    n = MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE + 5
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [ScoredDecision(transaction_id=f"t{i}", predicted_category="duplicate_refund", true_label="duplicate_refund", amount=1000_00, provider="groq") for i in range(n)]
        )
        report = compute_regret(history)
        history.close()

    assert report.minutes_per_manual_review_assumption > 0
    expected_hours = round(report.auto_resolved_transaction_count * report.minutes_per_manual_review_assumption / 60, 2)
    assert report.estimated_analyst_hours_saved == expected_hours
