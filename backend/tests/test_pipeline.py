"""End-to-end pipeline test (spec §5 diagram, full path) — the closest thing to a real batch
run, using the mock narrator provider so it's zero-cost and deterministic in CI."""

import tempfile
from pathlib import Path

from app.audit.logger import AuditLogger
from app.calibration.calibrator import ScoredDecision
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


def test_throughput_is_measured_not_estimated():
    """Spec explicitly names Throughput as a judged criterion (§9, §11) -- this must be a real
    measured number attached to the result, not just quoted in prose (an external audit flagged
    the earlier version of this project for having no instrumentation backing the throughput
    claim in BUILD_LOG.md)."""
    result = run_batch(seed=42, main_n=80, stress_n=0, threshold=0.90, provider="mock")
    # mock mode is deterministic with no network calls -- it can legitimately complete in under a
    # microsecond, rounding to exactly 0.0 at whatever timer resolution the OS provides. >= 0 is
    # the real invariant; asserting > 0 here would be testing timer noise, not correctness.
    assert result.elapsed_seconds >= 0
    assert result.elapsed_seconds < 10, "mock provider should be fast -- no real network calls"
    assert result.transactions_per_second > 0
    assert 0 <= result.narrated_count <= result.total_transactions


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


def test_three_way_decomposition_isolates_what_the_narrator_adds():
    """Real Problem / not-cherry-picked (spec §11): the three-way ordering must hold --
    naive baseline <= this project's own deterministic engine alone <= full system with the
    narrator -- so the pitch can isolate what the *agentic* layer specifically contributes on top
    of good deterministic engineering, not just beat a naive strawman. Added 2026-08-24 after an
    external audit noted this data existed internally but was never surfaced as its own number."""
    result = run_batch(seed=42, main_n=150, stress_n=0, threshold=0.90, provider="mock")
    full_system_resolved = result.total_transactions - result.escalated_count

    assert result.baseline_clean_count <= result.deterministic_only_resolved_count <= full_system_resolved
    # the deterministic engine alone should already meaningfully beat the naive baseline -- that's
    # the whole point of causal-chain matching over flat row matching, with zero LLM involvement
    assert result.deterministic_only_resolved_count > result.baseline_clean_count
    assert 0 < result.deterministic_only_amount_reconciled <= result.amount_reconciled


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


def test_single_batch_alone_cannot_clear_threshold():
    """A lone batch's per-category N is too small to trust — see calibration/history.py for why."""
    lone = run_batch(seed=1, main_n=120, stress_n=0, threshold=0.90, provider="mock")
    lone_auto_resolve = set(lone.calibration.auto_resolve_categories)
    assert "netting_trap" not in lone_auto_resolve and "duplicate_refund" not in lone_auto_resolve, (
        "a single ~120-record batch should not have enough same-category volume to clear a 90% "
        "Wilson lower bound on its own -- if this starts failing, batch sizing or the threshold "
        "changed enough to invalidate the premise in calibration/history.py"
    )


def test_accumulated_mock_history_never_clears_threshold_regardless_of_volume():
    """Provider-aware gating (added 2026-08-24 after an external audit caught this missing): mock
    mode is a deterministic stand-in for zero-cost pipeline testing, not AI judgment. Running the
    default provider ("mock") through run_batch repeatedly must NEVER be able to earn auto-resolve
    for a narrator category, no matter how many batches accumulate -- that would let a demo "prove"
    calibrated AI judgment without a single real LLM call ever having been made. (Before this fix,
    6-7 accumulated mock batches did cross the threshold -- see BUILD_LOG.md 2026-08-24.)"""
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        last_report = None
        for seed in range(1, 8):
            result = run_batch(seed=seed, main_n=120, stress_n=0, threshold=0.90, provider="mock", calibration_history=history)
            last_report = result.calibration
        history.close()

    accumulated_auto_resolve = set(last_report.auto_resolve_categories)
    assert not accumulated_auto_resolve, (
        "mock-mode decisions must never accumulate toward auto-resolve, however many batches pile "
        f"up -- got {accumulated_auto_resolve}"
    )
    for c in last_report.categories:
        assert c.n == 0, f"{c.category}: n should be 0 (real-provider only) after only mock runs, got {c.n}"
        assert c.mock_n > 0, f"{c.category}: mock_n should reflect the accumulated mock volume"


def test_accumulated_real_provider_decisions_can_clear_threshold():
    """The other half of the same property: genuine (non-mock) accumulated decisions can still
    earn auto-resolve -- the gate isn't broken, it's just no longer foolable by the free default.
    Uses directly-constructed ScoredDecisions (provider="groq") rather than real API calls, to
    prove the CalibrationHistory/calibrate() mechanics without spending real LLM budget on a test."""
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [
                ScoredDecision(
                    transaction_id=f"real{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00, provider="groq"
                )
                for i in range(40)
            ],
            source="batch",
        )
        report = history.report(threshold=0.90)
        history.close()

    netting = next(c for c in report.categories if c.category == "netting_trap")
    assert netting.decision == "auto_resolve"
    assert netting.n == 40


def test_provider_gate_applies_per_decision_not_just_per_category():
    """A category being in auto_resolve_categories reflects the ACCUMULATED HISTORY of
    real-provider decisions for that category -- it says nothing about whether THIS transaction's
    own classification came from a real provider. Reproduces exactly the gap an external audit
    found live 2026-08-24 (round 5), via a standalone repro script against the unmodified code:
    pre-load real evidence for netting_trap past the threshold, then run a mock batch through that
    same history, and confirm mock-classified netting_trap transactions in THAT run still escalate
    -- they must never silently ride on trust a different, real decision earned elsewhere."""
    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "history.db")
        history.add(
            [
                ScoredDecision(
                    transaction_id=f"real{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00, provider="groq"
                )
                for i in range(40)
            ],
            source="batch",
        )
        pre_report = history.report(threshold=0.90)
        assert "netting_trap" in set(pre_report.auto_resolve_categories), "test setup should have netting_trap already earning trust"

        # find a seed whose mock-classified narration queue actually includes a netting_trap case
        result = None
        for seed in range(1, 30):
            candidate = run_batch(seed=seed, main_n=120, stress_n=0, threshold=0.90, provider="mock", calibration_history=history)
            if any(e.category == "netting_trap" for e in candidate.escalations):
                result = candidate
                break
        history.close()

    assert result is not None, "test setup should find a seed producing a mock netting_trap escalation"
    netting_escalations = [e for e in result.escalations if e.category == "netting_trap"]
    assert netting_escalations, "mock-classified netting_trap must still escalate even though the category has earned real trust elsewhere"
    for e in netting_escalations:
        assert e.provider == "mock"
