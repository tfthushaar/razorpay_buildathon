"""End-to-end batch pipeline (spec §5): generate -> chains -> matching -> narration ->
calibration -> audit log -> baseline comparison -> stress scorecard.

This is what the FastAPI layer calls for a batch run, and what a script/test can call directly
without a server. Ground truth is threaded through only for scoring (calibration input, baseline
comparison, stress scorecard) — it never reaches the matching engine or the narrator.
"""

import os
import time
import uuid

from pydantic import BaseModel, computed_field

from app.audit.logger import AuditEntry, AuditLogger
from app.calibration.calibrator import CalibrationReport, ScoredDecision, calibrate
from app.calibration.history import CalibrationHistory
from app.chain.builder import CausalChain, build_all_chains
from app.data_gen.generate import generate, generate_fee_leak_batch
from app.data_gen.schemas import SyntheticBatch
from app.erp.journal import generate_journal_entries
from app.feeleak.detector import FeeLeakReport, run_fee_leak_detection
from app.matching.baseline import run_naive_baseline
from app.matching.engine import MatchResult, run_matching_engine
from app.matching.escalation import EscalationItem, build_escalation_item, triage
from app.narrator.agent import narrate
from app.narrator.tools import ToolContext, build_tool_context


class StressScorecard(BaseModel):
    total: int
    deterministic_correct: int
    narrated_total: int
    narrated_correctly_handled: int  # correctly auto-resolved OR correctly escalated (any escalation is safe)
    wrongly_auto_resolved: int  # the one number that must be zero for the pitch to hold


class BatchRunResult(BaseModel):
    run_id: str
    seed: int
    threshold: float
    provider: str

    total_transactions: int
    total_amount: int
    amount_reconciled: int  # settled amount for everything NOT escalated
    escalated_count: int

    calibration: CalibrationReport
    escalations: list[EscalationItem]

    baseline_clean_count: int
    baseline_false_negative_timing_lag: int
    baseline_false_positive_rounding: int

    # Three-way decomposition (spec's "real problem, not cherry-picked" §11): naive baseline vs.
    # this project's own deterministic Pass1+2 engine alone (no LLM, no calibration) vs. the full
    # system with the narrator. Isolates what the agentic layer specifically contributes on top of
    # good deterministic engineering, rather than only comparing against a naive strawman. Added
    # 2026-08-24 after an external audit noted this data was already computed internally but never
    # surfaced as its own number.
    deterministic_only_resolved_count: int
    deterministic_only_amount_reconciled: int

    stress: StressScorecard

    # Fee leak detection (Pillar 2) -- a genuinely separate axis of analysis from reconciliation
    # (see app/feeleak/detector.py's module docstring for why): runs against its own dedicated
    # batch of transactions that reconcile perfectly cleanly but were charged fees inconsistent
    # with the merchant's own contract, the real-world blind spot standard reconciliation can't
    # see. Never mixed into total_transactions/amount_reconciled above.
    fee_leak_report: FeeLeakReport

    # Real GST-on-fee separated into its own ITC-eligible ledger line across the whole main batch's
    # journal (app/erp/journal.py) -- distinct from fee_leak_report.total_gst_correction, which is
    # specifically the wrongly-computed-GST correction found in the separate fee-leak sample.
    total_itc_separated: int

    # Throughput (spec's own explicitly-named criterion) — measured, not estimated. Covers the
    # main batch only, not the separately-scored stress batch, so it reflects what a merchant's
    # actual reconciliation batch would cost in wall-clock time.
    elapsed_seconds: float
    narrated_count: int  # of total_transactions, how many actually reached the narrator

    @computed_field  # type: ignore[prop-decorator]
    @property
    def transactions_per_second(self) -> float:
        # float("inf") is not valid JSON and would break the frontend's response.json() parsing --
        # floor elapsed_seconds for this rate calculation only (the reported elapsed_seconds field
        # itself stays exactly what was measured, including a genuine 0.0).
        return self.total_transactions / max(self.elapsed_seconds, 1e-6)


def _final_decision(result: MatchResult, narrator_category: str | None, narrator_provider: str | None, auto_resolve_categories: set[str]) -> str:
    """A category being in `auto_resolve_categories` means the ACCUMULATED HISTORY of real-provider
    decisions for that category cleared the threshold — it says nothing about whether THIS
    transaction's own classification came from a real provider. Without the `narrator_provider !=
    "mock"` check, a category that legitimately earned trust from real evidence would silently
    auto-resolve a *mock-mode* run's guess riding on that trust, even though this specific decision
    was never itself validated. Caught by an external audit 2026-08-24 (round 5) with a live
    reproduction; see BUILD_LOG.md. `genuine_error` never appears in `auto_resolve_categories` in
    the first place (calibrator.py's NEVER_AUTO_RESOLVE), so this is belt-and-suspenders there, but
    load-bearing for duplicate_refund/netting_trap once either has real accumulated evidence."""
    if result.resolution != "needs_narration":
        return result.resolution  # "clean_pass1" | "auto_resolved_deterministic"
    if narrator_category in auto_resolve_categories and narrator_provider != "mock":
        return "auto_resolved_calibrated"
    return "escalated"


def _audit_entry_for(
    run_id: str, result: MatchResult, decision: str, category: str | None, confidence: float | None, reasoning: str | None, tool_calls: list[dict]
) -> AuditEntry:
    chain = result.chain
    return AuditEntry(
        transaction_id=result.transaction_id,
        decision=decision,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        tool_calls=tool_calls,
        order_id=chain.order_id,
        payment_id=chain.payment_id,
        settlement_id=chain.settlement_id,
        ledger_id=chain.ledger_id,
        refund_ids=chain.refund_ids,
        run_id=run_id,
    )


def _process_batch(batch: SyntheticBatch, provider: str | None) -> tuple[dict[str, CausalChain], dict[str, MatchResult], dict, ToolContext]:
    # Not wired to app.matching.merkle_prefilter -- measured, not assumed, and the honest number
    # says it shouldn't be. See BUILD_LOG.md: at 50k records/97% clean, the prefilter's own hashing
    # cost (~260ms) exceeds what it saves by skipping Pass 1/2 for clean transactions (~2ms), since
    # Pass 1/2 were already cheap and every transaction still needs a full CausalChain built
    # regardless (MatchResult.chain is required). Kept as a correct, tested, documented capability
    # (matching/merkle_prefilter.py) for the case where that assumption doesn't hold — ledger and
    # settlement data living in separate services, where avoiding the fetch is the real saving —
    # rather than wired into the default path where it would be a net regression.
    chains = build_all_chains(batch)
    match_results = run_matching_engine(chains)
    context = build_tool_context(batch, chains)
    narration_queue = [txn_id for txn_id, r in match_results.items() if r.resolution == "needs_narration"]
    narrator_outputs = {txn_id: narrate(chains[txn_id], context, provider=provider) for txn_id in narration_queue}
    return chains, match_results, narrator_outputs, context


def _stress_scorecard(
    stress_batch: SyntheticBatch, provider: str | None, auto_resolve_categories: set[str]
) -> StressScorecard:
    chains, match_results, narrator_outputs, _ = _process_batch(stress_batch, provider)
    gt = {g.transaction_id: g.true_label for g in stress_batch.ground_truth}

    deterministic_correct = 0
    narrated_correctly_handled = 0
    wrongly_auto_resolved = 0

    for txn_id, result in match_results.items():
        true_label = gt[txn_id]
        if result.resolution != "needs_narration":
            if result.category == true_label:
                deterministic_correct += 1
            continue
        output = narrator_outputs[txn_id]
        would_auto_resolve = output.category in auto_resolve_categories and output.provider != "mock"
        is_correct = output.category == true_label
        if would_auto_resolve and not is_correct:
            wrongly_auto_resolved += 1
        if is_correct or not would_auto_resolve:
            narrated_correctly_handled += 1

    return StressScorecard(
        total=len(stress_batch.orders),
        deterministic_correct=deterministic_correct,
        narrated_total=len(narrator_outputs),
        narrated_correctly_handled=narrated_correctly_handled,
        wrongly_auto_resolved=wrongly_auto_resolved,
    )


def run_batch(
    seed: int = 42,
    main_n: int = 120,
    stress_n: int = 40,
    threshold: float = 0.90,
    provider: str | None = None,
    audit_logger: AuditLogger | None = None,
    calibration_history: CalibrationHistory | None = None,
) -> BatchRunResult:
    run_id = str(uuid.uuid4())
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    started_at = time.monotonic()
    main_batch, stress_batch = generate(seed=seed, main_n=main_n, stress_n=stress_n)

    chains, match_results, narrator_outputs, _ = _process_batch(main_batch, provider)
    elapsed_seconds = time.monotonic() - started_at
    gt_by_id = {g.transaction_id: g.true_label for g in main_batch.ground_truth}
    baseline_results = run_naive_baseline(chains)

    # deterministic-only tier of the three-way decomposition: what Pass1+2 resolves before the
    # narrator or calibration are ever involved -- computed here, from match_results alone, so it
    # can never be influenced by narrator/calibration behavior even by accident.
    deterministic_only_resolved_count = sum(1 for r in match_results.values() if r.resolution != "needs_narration")
    deterministic_only_amount_reconciled = sum(
        r.chain.actual_settled_amount for r in match_results.values() if r.resolution != "needs_narration"
    )

    scored_decisions = [
        ScoredDecision(
            transaction_id=txn_id,
            predicted_category=output.category,
            true_label=gt_by_id[txn_id],
            amount=chains[txn_id].actual_settled_amount,
            provider=output.provider,
        )
        for txn_id, output in narrator_outputs.items()
    ]
    if calibration_history is not None:
        # accumulate into the running history rather than judging this batch in isolation — see
        # calibration/history.py for why a single batch's per-category N is too small to ever
        # clear a 90% Wilson lower bound on its own. add_and_report (not separate add()+report()
        # calls) so a concurrent reset_history request can't wipe this run's own contribution out
        # from under its own report -- see add_and_report's docstring for the live-reproduced race
        # this closes.
        calibration_report = calibration_history.add_and_report(scored_decisions, threshold=threshold, source="batch")
    else:
        calibration_report = calibrate(scored_decisions, threshold=threshold)
    auto_resolve_categories = set(calibration_report.auto_resolve_categories)

    escalations: list[EscalationItem] = []
    audit_entries: list[AuditEntry] = []
    amount_reconciled = 0
    escalated_count = 0
    total_amount = sum(c.actual_settled_amount for c in chains.values())

    for txn_id, result in match_results.items():
        chain = result.chain
        if result.resolution == "needs_narration":
            output = narrator_outputs[txn_id]
            decision = _final_decision(result, output.category, output.provider, auto_resolve_categories)
            tool_calls = [tc.model_dump() for tc in output.tool_calls]
            audit_entries.append(_audit_entry_for(run_id, result, decision, output.category, output.confidence, output.reasoning, tool_calls))
            if decision == "escalated":
                escalated_count += 1
                escalations.append(
                    build_escalation_item(
                        txn_id, output.category, output.confidence, output.reasoning, chain.actual_settled_amount, output.provider
                    )
                )
            else:
                amount_reconciled += chain.actual_settled_amount
        else:
            decision = result.resolution
            audit_entries.append(_audit_entry_for(run_id, result, decision, result.category, result.confidence, result.reasoning, []))
            amount_reconciled += chain.actual_settled_amount

    if audit_logger is not None:
        audit_logger.log_many(audit_entries)

    # Real ITC-eligible GST across the WHOLE main batch's journal (app/erp/journal.py), not the
    # fee-leak sample's own correction figure -- a genuinely different number: this is "GST on the
    # gateway fee, correctly separated into its own ledger line instead of merged into 'gateway
    # charges'", which is true and useful for every transaction, leaked or not.
    finalized_ids = set(chains.keys()) - {e.transaction_id for e in escalations}
    journal_entries = generate_journal_entries(chains, finalized_ids)
    total_itc_separated = sum(l.debit for e in journal_entries for l in e.lines if l.account == "Input Tax Credit Receivable")

    baseline_clean_count = sum(1 for r in baseline_results.values() if r.clean)
    baseline_false_negative_timing_lag = sum(
        1 for txn_id, label in gt_by_id.items() if label == "timing_lag" and baseline_results[txn_id].clean
    )
    baseline_false_positive_rounding = sum(
        1 for txn_id, label in gt_by_id.items() if label == "currency_rounding" and not baseline_results[txn_id].clean
    )

    stress = _stress_scorecard(stress_batch, provider, auto_resolve_categories)

    # Independent of seed's main/stress streams (generate_fee_leak_batch uses seed+2 internally,
    # see data_gen/generate.py) -- deterministic per seed, same convention as everything else here.
    fee_leak_batch = generate_fee_leak_batch(seed=seed, n=20)
    fee_leak_report = run_fee_leak_detection(fee_leak_batch.orders, fee_leak_batch.payments)

    return BatchRunResult(
        run_id=run_id,
        seed=seed,
        threshold=threshold,
        provider=provider,
        total_transactions=len(chains),
        total_amount=total_amount,
        amount_reconciled=amount_reconciled,
        escalated_count=escalated_count,
        calibration=calibration_report,
        escalations=triage(escalations),
        baseline_clean_count=baseline_clean_count,
        baseline_false_negative_timing_lag=baseline_false_negative_timing_lag,
        baseline_false_positive_rounding=baseline_false_positive_rounding,
        deterministic_only_resolved_count=deterministic_only_resolved_count,
        deterministic_only_amount_reconciled=deterministic_only_amount_reconciled,
        stress=stress,
        fee_leak_report=fee_leak_report,
        total_itc_separated=total_itc_separated,
        elapsed_seconds=elapsed_seconds,
        narrated_count=len(narrator_outputs),
    )
