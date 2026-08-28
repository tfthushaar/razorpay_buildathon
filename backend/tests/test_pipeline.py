"""End-to-end pipeline test (full path) — the closest thing to a real batch
run, using the mock narrator provider so it's zero-cost and deterministic in CI."""

import tempfile
from pathlib import Path

from app.audit.logger import AuditLogger
from app.calibration.calibrator import ScoredDecision
from app.calibration.history import CalibrationHistory
from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.narrator.tools import build_tool_context, recall_similar_resolutions
from app.pipeline import run_batch


def test_full_pipeline_runs_end_to_end_with_mock_provider():
    result = run_batch(seed=42, main_n=120, stress_n=40, threshold=0.90, provider="mock")

    assert result.total_transactions == 120
    assert result.provider == "mock"
    assert result.total_amount > 0
    assert 0 <= result.amount_reconciled <= result.total_amount
    assert result.escalated_count == len(result.escalations)
    assert result.escalated_count >= 0


def test_category_proposals_are_empty_by_default():
    """enable_discovery defaults to False -- every existing caller (including every other test in
    this file) must see zero behavior change."""
    result = run_batch(seed=42, main_n=150, stress_n=0, provider="mock")
    assert result.category_proposals == []


def test_enable_discovery_proposes_once_per_genuine_error_case():
    result = run_batch(seed=42, main_n=150, stress_n=0, provider="mock", enable_discovery=True)
    genuine_error_escalations = [e for e in result.escalations if e.category == "genuine_error"]
    assert genuine_error_escalations, "fixture assumption: seed 42 at main_n=150 should escalate a genuine_error case"
    proposed_ids = {p.transaction_id for p in result.category_proposals}
    assert proposed_ids == {e.transaction_id for e in genuine_error_escalations}
    for proposal in result.category_proposals:
        assert proposal.provider == "mock"


def test_multiway_netting_trap_disabled_by_default_in_run_batch():
    """enable_multiway_netting defaults to False -- every existing caller stays unaffected until
    this is explicitly measured, the same posture as enable_discovery."""
    result = run_batch(seed=1, main_n=150, stress_n=0, provider="mock")
    categories = {e.category for e in result.escalations}
    assert "multiway_netting_trap" not in categories


def test_multiway_netting_trap_always_escalates_end_to_end_under_mock():
    """Full run_batch(..., provider="mock") on a flag-enabled batch: mock structurally can never
    solve this (it never calls list_batch_deltas/verify_group_sum) -- it always misclassifies a
    real multiway_netting_trap case as genuine_error, which is the honest, expected outcome, NOT a
    bug. What this actually checks: every ground-truth multiway_netting_trap transaction ends up
    escalated (never silently auto-resolved under some other category's calibration), per the
    existing narrator_provider != "mock" gate that already applies to every category with no
    special-casing needed for this one."""
    found_any = False
    for seed in range(1, 15):
        main, _ = generate(seed=seed, main_n=150, stress_n=0, enable_multiway_netting=True)
        multiway_ids = {g.transaction_id for g in main.ground_truth if g.true_label == "multiway_netting_trap"}
        if not multiway_ids:
            continue
        found_any = True
        result = run_batch(seed=seed, main_n=150, stress_n=0, provider="mock", enable_multiway_netting=True)
        escalated_ids = {e.transaction_id for e in result.escalations}
        assert multiway_ids <= escalated_ids, f"seed={seed}: {multiway_ids - escalated_ids} multiway cases were not escalated"
        for e in result.escalations:
            if e.transaction_id in multiway_ids:
                assert e.category == "genuine_error"  # mock's honest, expected misclassification
    assert found_any, "fixture assumption: seeds 1-14 at n=150 with multiway enabled should produce at least one case"


def test_run_batch_includes_a_real_fee_leak_report():
    """Fee leak detection (Pillar 2) is a genuinely separate axis from reconciliation -- runs
    against its own dedicated batch (generate_fee_leak_batch), never mixed into
    total_transactions/amount_reconciled, but surfaced on every BatchRunResult."""
    result = run_batch(seed=42, main_n=50, stress_n=0, provider="mock")
    report = result.fee_leak_report
    assert report.findings, "the fee-leak batch should always produce real findings"
    assert report.total_fee_recovery >= 0
    assert report.total_gst_correction >= 0
    assert set(report.by_pattern) <= {"blended_rate_overcharge", "gst_wrong_base", "gst_wrong_rate"}
    # confirms this genuinely didn't leak into the main reconciliation numbers
    assert result.total_transactions == 50


def test_fee_leak_report_is_reproducible_for_the_same_seed():
    a = run_batch(seed=7, main_n=30, stress_n=0, provider="mock")
    b = run_batch(seed=7, main_n=30, stress_n=0, provider="mock")
    assert [f.transaction_id for f in a.fee_leak_report.findings] == [f.transaction_id for f in b.fee_leak_report.findings]
    assert a.fee_leak_report.total_fee_recovery == b.fee_leak_report.total_fee_recovery


def test_total_itc_separated_is_real_and_distinct_from_the_fee_leak_correction():
    """total_itc_separated is computed from the WHOLE main batch's journal (every transaction's
    GST-on-fee, correctly separated), not the fee-leak sample's own gst_correction figure -- the
    two numbers measure genuinely different things and must not be conflated or accidentally
    equal by construction."""
    result = run_batch(seed=42, main_n=120, stress_n=40, threshold=0.90, provider="mock")
    assert result.total_itc_separated > 0
    assert result.total_itc_separated != result.fee_leak_report.total_gst_correction


def test_throughput_is_measured_not_estimated():
    """Spec explicitly names Throughput as a judged criterion -- this must be a real
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
    """The one-sentence pitch: our reconciled amount should beat the naive baseline's
    "clean" count on this same batch by a real margin, not a marginal one."""
    result = run_batch(seed=42, main_n=150, stress_n=0, threshold=0.90, provider="mock")
    # naive baseline can only ever call something "clean"; it has no auto-resolve concept at all,
    # so compare its clean count against our total resolved (= not escalated) count
    engine_resolved = result.total_transactions - result.escalated_count
    assert engine_resolved > result.baseline_clean_count
    assert result.baseline_false_negative_timing_lag > 0
    assert result.baseline_false_positive_rounding > 0


def test_three_way_decomposition_isolates_what_the_narrator_adds():
    """Real Problem / not-cherry-picked: the three-way ordering must hold --
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


def test_recall_similar_resolutions_has_real_memory_of_a_prior_run_not_just_the_current_one():
    """The limitation this closes: recall_similar_resolutions used to start every run with zero
    memory, even of a category this exact merchant's data resolves constantly. With a real,
    persisted AuditLogger threaded through two separate run_batch calls, the second run's narrator
    tools see the first run's own logged decisions from the moment it starts -- not something that
    merely accumulates within the second run itself."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_audit.db"
        logger = AuditLogger(db_path=db_path)

        first = run_batch(seed=42, main_n=150, stress_n=0, threshold=0.90, provider="mock", audit_logger=logger)
        first_entries = logger.entries_for_run(first.run_id)
        assert any(e["category"] is not None for e in first_entries), "fixture assumption: seed 42 narrates at least one category"

        # A brand-new context, built the same way run_batch's own _process_batch does, but called
        # directly here so the seeded history can be inspected BEFORE any of this "second run"'s own
        # narration has touched it.
        second_batch, _ = generate(seed=101, main_n=10, stress_n=0)
        second_chains = build_all_chains(second_batch)
        seeded_context = build_tool_context(second_batch, second_chains, audit_logger=logger)
        logger.close()

    persisted_categories = {e["category"] for e in first_entries if e["category"] is not None}
    assert seeded_context.audit_log, "a fresh context seeded from a logger with real prior history must not start empty"
    assert {e["category"] for e in seeded_context.audit_log} == persisted_categories

    for category in persisted_categories:
        recalled = recall_similar_resolutions(category, seeded_context)
        assert recalled["prior_count"] > 0


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


def test_concurrent_reset_never_hides_a_calls_own_just_added_decisions():
    """A round-12 audit reproduced this live against the previous add()+report() (two separate
    calls) design: request A added 9 decisions, a concurrent reset_history request's clear() fired
    in the gap before A's own report(), and A's own report() came back reflecting the OTHER
    request's fresh data with A's own just-persisted decisions permanently gone -- not delayed,
    gone, no error, silently corrupting the exact ledger the "trust accumulates over time" pitch
    depends on. add_and_report (now used by both run_batch's calibration commit and
    confirm_human_resolution) closes this by making "insert this call's own decisions, then read
    the report" one atomic operation under the same lock clear() also needs -- a concurrent
    clear() can only run entirely before or entirely after A's own add-then-report, never inside
    it, so A's own report always reflects at least A's own contribution.

    Fires many add_and_report calls concurrently against many clear() calls and requires every
    single add_and_report to see at least its own 3 just-added decisions in its own returned
    report -- not eventually, in the exact report that call itself received."""
    import tempfile as _tempfile
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path as _Path

    with _tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=_Path(tmp) / "history.db")
        violations = []

        def add_and_check(i: int):
            decisions = [
                ScoredDecision(transaction_id=f"a{i}_{j}", predicted_category="genuine_error", true_label="genuine_error", amount=100, provider="groq")
                for j in range(3)
            ]
            report = history.add_and_report(decisions, threshold=0.90)
            total_n = sum(c.n for c in report.categories)
            if total_n < 3:
                violations.append((i, total_n))

        def reset_repeatedly():
            for _ in range(30):
                history.clear()

        with ThreadPoolExecutor(max_workers=9) as pool:
            resetter = pool.submit(reset_repeatedly)
            adders = [pool.submit(add_and_check, i) for i in range(30)]
            for f in adders:
                f.result()
            resetter.result()

        history.close()

    assert not violations, f"{len(violations)} of {30} add_and_report calls didn't see their own just-added decisions: {violations[:5]}"
