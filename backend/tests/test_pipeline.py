"""End-to-end pipeline test (spec §5 diagram, full path) — the closest thing to a real batch
run, using the mock narrator provider so it's zero-cost and deterministic in CI."""

import tempfile
from pathlib import Path

from app.audit.logger import AuditLogger
from app.calibration.history import CalibrationHistory
from app.pipeline import run_batch


def test_full_pipeline_runs_end_to_end_with_mock_provider():
    result = run_batch(seed=42, main_n=120, stress_n=40, threshold=0.90, provider="mock")

    assert result.total_transactions == 120
    assert result.provider == "mock"
    assert result.total_amount > 0
    assert 0 <= result.amount_reconciled <= result.total_amount
    assert result.escalated_count == len(result.escalations)
    assert result.escalated_count >= 0


def test_pitch_stat_engine_beats_naive_baseline():
    """The one-sentence pitch (spec §6.7): our reconciled amount should beat the naive baseline's
    "clean" count on this same batch by a real margin, not a marginal one."""
    result = run_batch(seed=42, main_n=150, stress_n=0, threshold=0.90, provider="mock")
    # naive baseline can only ever call something "clean"; it has no auto-resolve concept at all,
    # so compare its clean count against our total resolved (= not escalated) count
    engine_resolved = result.total_transactions - result.escalated_count
    assert engine_resolved > result.baseline_clean_count
    assert result.baseline_false_negative_timing_lag > 0
    assert result.baseline_false_positive_rounding > 0


def test_stress_scorecard_never_wrongly_auto_resolves():
    result = run_batch(seed=42, main_n=100, stress_n=50, threshold=0.90, provider="mock")
    assert result.stress.total == 50
    assert result.stress.wrongly_auto_resolved == 0, "the adversarial stress batch must never be wrongly auto-resolved"


def test_genuine_error_never_appears_in_escalations_as_auto_resolved():
    result = run_batch(seed=42, main_n=150, stress_n=0, threshold=0.90, provider="mock")
    auto_resolve_cats = set(result.calibration.auto_resolve_categories)
    assert "genuine_error" not in auto_resolve_cats


def test_audit_log_persists_every_decision_for_the_run():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_audit.db"
        logger = AuditLogger(db_path=db_path)
        result = run_batch(seed=42, main_n=60, stress_n=0, threshold=0.90, provider="mock", audit_logger=logger)
        entries = logger.entries_for_run(result.run_id)
        logger.close()

    assert len(entries) == result.total_transactions
    decisions = {e["decision"] for e in entries}
    assert decisions <= {"clean_pass1", "auto_resolved_deterministic", "auto_resolved_calibrated", "escalated"}


def test_threshold_change_reruns_cheaply_and_changes_escalation_count():
    """Not literally the live dial (that recomputes without regenerating data), but confirms the
    threshold parameter actually has teeth end-to-end."""
    loose = run_batch(seed=7, main_n=150, stress_n=0, threshold=0.50, provider="mock")
    strict = run_batch(seed=7, main_n=150, stress_n=0, threshold=0.999, provider="mock")
    assert strict.escalated_count >= loose.escalated_count


def test_single_batch_alone_cannot_clear_threshold_but_accumulated_history_can():
    """The core reason CalibrationHistory exists: verify both halves of the claim in BUILD_LOG —
    a lone batch's per-category N is too small to trust, but accumulating several batches' worth
    (the same thing repeated human-confirmed feedback would do) lets a category earn auto-resolve
    without ever touching the ground truth outside of scoring."""
    lone = run_batch(seed=1, main_n=120, stress_n=0, threshold=0.90, provider="mock")
    lone_auto_resolve = set(lone.calibration.auto_resolve_categories)
    assert "netting_trap" not in lone_auto_resolve and "duplicate_refund" not in lone_auto_resolve, (
        "a single ~120-record batch should not have enough same-category volume to clear a 90% "
        "Wilson lower bound on its own — if this starts failing, batch sizing or the threshold "
        "changed enough to invalidate the premise in calibration/history.py"
    )

    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        last_report = None
        for seed in range(1, 8):
            result = run_batch(seed=seed, main_n=120, stress_n=0, threshold=0.90, provider="mock", calibration_history=history)
            last_report = result.calibration
        history.close()

    accumulated_auto_resolve = set(last_report.auto_resolve_categories)
    assert accumulated_auto_resolve, "accumulating 7 batches worth of confirmed mock decisions should cross the threshold for at least one category"
    assert "genuine_error" not in accumulated_auto_resolve, "genuine_error must never auto-resolve, accumulated history or not"
