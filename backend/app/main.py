"""FastAPI layer — exposes the pipeline over HTTP for the React dashboard.

Single-session, in-memory app state is deliberate: this is a hackathon demo tool for one
presenter driving one dashboard, not a multi-tenant service. The audit log and calibration
history are the two things that persist to SQLite (spec's "doesn't need to be fancy"); everything
else is cheap to recompute from a batch run, consistent with the rest of the system's cost
posture (see BUILD_LOG.md, [[feedback-build-autonomy-and-cost]]).
"""

import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

load_dotenv()  # so LLM_PROVIDER / GROQ_API_KEY in backend/.env reach os.environ before any request

import os

from app.audit.logger import AuditLogger
from app.calibration.calibrator import CalibrationReport
from app.calibration.history import CalibrationHistory
from app.calibration.regret import RegretReport, compute_regret
from app.calibration.risk_coverage import RiskCoverageCurve, risk_coverage_curve
from app.calibration.revocation_drill import RevocationDrillReport, run_revocation_drill
from app.chain.builder import build_all_chains
from app.data_gen.generate import generate, generate_pending_batch
from app.data_gen.schemas import LedgerEntry, Order, Payment, Refund, Settlement, SyntheticBatch
from app.erp.exporters import to_generic_csv, to_tally_xml, to_zoho_books_csv
from app.erp.gstr2b import Gstr2bMatchReport, generate_simulated_gstr2b, match_against_gstr2b, to_gstr2b_format
from app.erp.journal import generate_journal_entries
from app.forecast.backtest import BacktestReport, run_backtest
from app.forecast.blind_backtest import run_blind_backtest
from app.forecast.cash_position import PayrollCoverageResult, WorkingCapitalReport, check_payroll_coverage, compute_working_capital
from app.forecast.calibrated_interval import fit as fit_intervals, reliability_curve
from app.forecast.forecastability import assess_batch
from app.forecast.predictor import SettlementPrediction, predict_pending_batch, predict_settlement
from app.matching.engine import run_matching_engine
from app.narrator.agent import narrate
from app.narrator.tools import build_tool_context
from app.qa.agent import QAAnswer, answer_question
from app.qa.benchmark import build_questions, extract_ids_from_text, score_answer
from app.qa.tools import build_settled_at_index
from app.webhooks.razorpay import ParsedSettlementEvent, WebhookParseError, WebhookSignatureError, parse_settlement_processed_event, verify_razorpay_signature
from app.pipeline import BatchRunResult, _final_decision, run_batch

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

app = FastAPI(title="Settlement Reconciliation Copilot API")

# Local dev origins always allowed; a deployed frontend (e.g. Netlify) adds its real
# origin via ALLOWED_ORIGINS (comma-separated, no trailing slash) rather than editing this file.
_default_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_extra_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

audit_logger = AuditLogger(db_path=DATA_DIR / "audit_log.db")
calibration_history = CalibrationHistory(db_path=DATA_DIR / "calibration_history.db")


@dataclass(frozen=True)
class _RunSnapshot:
    """One run's result/escalations/ground-truth, as a single immutable unit. The three fields are
    always constructed together and never reassigned independently after that -- a plain read of
    `state.latest` (one attribute access, atomic under the GIL by itself, no lock required) always
    hands back a self-consistent triple from exactly one run, never a mix of one run's escalations
    with a DIFFERENT, concurrently-committed run's ground truth. This closes the desync race for
    ANY reader, including a future one that doesn't know to take `_state_lock` -- the discipline of
    "remember to hold the lock" is exactly the kind of convention every one of this project's
    concurrency bugs so far has broken; a structurally atomic commit doesn't depend on remembering
    anything. `escalations_by_id` is still a plain mutable dict *within* the frozen snapshot
    (frozen only blocks reassigning the three fields themselves) -- see api_resolve_escalation for
    why popping from it in place is still correct."""

    result: BatchRunResult | None = None
    ground_truth: dict[str, str] = field(default_factory=dict)
    escalations_by_id: dict[str, dict] = field(default_factory=dict)


class _AppState:
    def __init__(self) -> None:
        self.latest = _RunSnapshot()


state = _AppState()
# Guards the compound read-and-mutate sequence in /api/escalations/resolve (pop one escalation,
# then check it against that SAME snapshot's ground truth and threshold) so it happens as one
# logical step, and the commit in /api/run for the same reason (compute everything first, touch
# `state` only once at the end). The cross-field consistency itself no longer depends on this lock
# -- _RunSnapshot's atomicity handles that structurally -- but the lock still matters for
# "check-then-claim" on a specific escalation: two concurrent resolves of the same transaction_id
# must not both see it as present. Reproduced live before either fix existed: rounds 10-11 found an
# unlocked check-then-delete let two concurrent resolves double-count into calibration_history and
# crash the second with a KeyError, and 32 concurrent /api/run calls (with amplified thread
# scheduling -- a standard technique for exposing a real race, not manufacturing one) desynced the
# old three-separate-writes version of `state` on the first trial. Realistic load (2, or even 8
# concurrent requests, no amplification) never reproduced either bug in dozens of trials, but both
# windows were real, so both are closed regardless of how hard they are to hit by accident.
_state_lock = threading.Lock()


class RunRequest(BaseModel):
    seed: int = 42
    # bounded for symmetry with threshold/provider below -- neither crashes unbounded (a negative
    # or zero value degrades gracefully to an empty batch, verified directly) but round 13 flagged
    # the inconsistency with this project's otherwise-thorough input-bounding discipline, and an
    # uncapped value is a mild availability risk (a fat-fingered huge main_n tying up a worker
    # thread) worth closing cheaply. The frontend's `min` attributes on these fields are unenforced
    # HTML hints, not real validation -- this is the actual boundary.
    main_n: int = Field(120, ge=1, le=2000)
    stress_n: int = Field(40, ge=0, le=2000)
    # ge/le rejects an out-of-range threshold (e.g. a negative value) at request-parsing time, with
    # a clean 422 -- an external audit 2026-08-24 found a negative threshold flipped the
    # calibration report's own gate to "auto_resolve" for categories that had never earned it,
    # since nothing previously checked this was a real probability.
    threshold: float = Field(0.90, ge=0.0, le=1.0)
    # Literal (not str), mirroring agent.py's VALID_PROVIDERS, so an unknown provider string is
    # rejected here with one clear message instead of silently running the whole batch and having
    # every narrated transaction fail-safe individually (each still correct, but far less useful
    # than catching the actual mistake up front) -- see agent.py's narrate() for the matching fix
    # at the function level, for callers other than these two endpoints.
    provider: Literal["mock", "groq", "ollama"] | None = None  # None -> LLM_PROVIDER env var, default "mock"
    reset_history: bool = False
    enable_discovery: bool = False  # opt-in: propose a candidate category for each genuine_error case (never auto-adopted)
    enable_multiway_netting: bool = False  # opt-in: inject multiway_netting_trap cases (default off -- see app/data_gen/generate.py)
    enable_held_out_variants: bool = False  # opt-in: near-miss duplicate_refund/netting_trap cases the exact-match rule can't confirm
    enable_narration_explained: bool = False  # opt-in: inject narration_explained cases (delta explained only by free text)
    # opt-in: inject compound_delta cases AND run the residual architecture (app/resolver/) -- Layer 0
    # over every exception, the model only over what it could not close. Off by default so a run
    # without it behaves exactly as before and every committed evidence file stays valid.
    enable_compound_delta: bool = False
    # opt-in: build the remittance advice from phrasing the keyword baseline's negation cues have
    # never seen, which is the honest test of whether reading generalises or was authored
    held_out_advice_phrasing: bool = False


class ResolveRequest(BaseModel):
    transaction_id: str


class ResolveResponse(BaseModel):
    transaction_id: str
    predicted_category: str
    confirmed_true_label: str
    was_correct: bool
    updated_calibration: CalibrationReport


@app.post("/api/run")
def api_run(req: RunRequest) -> BatchRunResult:
    # Same backstop shape as /api/transactions/evaluate (round 9), applied here too after round 10
    # found this endpoint -- the system's primary, default, most-used one -- had zero exception
    # handling. Unlike the evaluate endpoint, run_batch's own inputs are generator-produced with
    # referential integrity guaranteed by construction, so there's no equivalent KeyError shape to
    # name specifically; this is the broad backstop alone, covering things like a concurrent-access
    # error from the shared SQLite connections (see audit/logger.py and calibration/history.py for
    # the actual concurrency fix -- this catches what that fix doesn't, not a substitute for it).
    try:
        if req.reset_history:
            calibration_history.clear()

        result = run_batch(
            seed=req.seed,
            main_n=req.main_n,
            stress_n=req.stress_n,
            threshold=req.threshold,
            provider=req.provider,
            audit_logger=audit_logger,
            calibration_history=calibration_history,
            enable_discovery=req.enable_discovery,
            enable_multiway_netting=req.enable_multiway_netting,
            enable_held_out_variants=req.enable_held_out_variants,
            enable_narration_explained=req.enable_narration_explained,
            enable_compound_delta=req.enable_compound_delta,
            held_out_advice_phrasing=req.held_out_advice_phrasing,
        )
        escalations_by_id = {e.transaction_id: e.model_dump() for e in result.escalations}

        # Ground truth is deliberately never part of BatchRunResult (the pipeline must not leak it
        # to the dashboard as if it were a known answer). Regenerating with the same seed is a
        # cheap, deterministic, non-LLM call purely to recover the lookup the "resolve an
        # escalation" flow needs, mirroring how a human reviewer would confirm against the real
        # source records.
        main_batch, _ = generate(seed=req.seed, main_n=req.main_n, stress_n=req.stress_n)
        ground_truth = {g.transaction_id: g.true_label for g in main_batch.ground_truth}

        # Commit as one new _RunSnapshot -- see _RunSnapshot's docstring for why this is
        # structurally atomic for any reader, not just ones that remember to take _state_lock.
        # Everything above is pure computation on local variables, never touching `state`, so the
        # lock (kept here for clarity/consistency with the compound sequence in
        # api_resolve_escalation, though the swap alone is already atomic) is only ever held for
        # one cheap assignment -- not for run_batch() itself, which can take anywhere from
        # milliseconds (mock) to minutes (a real provider). Concurrent, unrelated requests are
        # never blocked waiting on a slow batch run.
        with _state_lock:
            state.latest = _RunSnapshot(result=result, ground_truth=ground_truth, escalations_by_id=escalations_by_id)

        return result
    except Exception as e:
        raise HTTPException(422, f"Could not complete this run ({type(e).__name__}: {e}); check the request parameters.")


@app.get("/api/runs/latest")
def api_latest_run() -> BatchRunResult:
    if state.latest.result is None:
        raise HTTPException(404, "no run yet — POST /api/run first")
    return state.latest.result


@app.get("/api/calibration")
def api_calibration(threshold: float = Query(0.90, ge=0.0, le=1.0)) -> CalibrationReport:
    """The live threshold dial: a cheap re-aggregation over the accumulated history, not a
    pipeline re-run."""
    return calibration_history.report(threshold=threshold)


@app.get("/api/risk-coverage")
def api_risk_coverage() -> RiskCoverageCurve:
    """Coverage against selective risk as the autonomy threshold moves.

    The dashboard has shown one pass/fail at 90%, which hides the shape of the trade-off and, on this
    project's own history, hides that a stricter gate can raise risk: the bound rewards sample size,
    so a small perfect category drops out before a large flawed one.
    """
    return risk_coverage_curve(calibration_history.all_decisions())


@app.get("/api/regret")
def api_regret(threshold: float = Query(0.90, ge=0.0, le=1.0)) -> RegretReport:
    """Regret in rupees (upgrade build Phase 4): the realized cost of calibrated autonomy, replayed
    chronologically over the accumulated history -- see app/calibration/regret.py."""
    return compute_regret(calibration_history, threshold=threshold)


class DrillRequest(BaseModel):
    category: Literal["duplicate_refund", "netting_trap"] = "netting_trap"  # genuine_error is in NEVER_AUTO_RESOLVE, never a valid drill target
    threshold: float = Field(0.90, ge=0.0, le=1.0)
    n_qualifying: int = Field(60, ge=1, le=500)  # 60, not 40: the gate now uses an anytime-valid bound
    regression_budget: int = Field(50, ge=0, le=500)


@app.post("/api/drift/drill")
def api_drift_drill(req: DrillRequest) -> RevocationDrillReport:
    """Time-to-revocation drill (upgrade build Phase 5): a demo harness over the real, already-tested
    calibrate()/detect_drift() machinery, run against a fresh isolated history -- never the real app's
    accumulated calibration_history.db. See app/calibration/revocation_drill.py."""
    return run_revocation_drill(
        category=req.category, threshold=req.threshold, n_qualifying=req.n_qualifying, regression_budget=req.regression_budget
    )


@app.post("/api/escalations/resolve")
def api_resolve_escalation(req: ResolveRequest) -> ResolveResponse:
    """The human-feedback loop : confirms one escalated case against its real source
    records and folds the outcome back into the accumulated calibration history."""
    # check-then-delete used to be two separate steps (a .get() here, a del at the bottom) with no
    # lock between them -- a real TOCTOU race under FastAPI's genuinely-concurrent threadpool
    # (round 10 already disproved the "effectively serialized" assumption once, for the SQLite
    # connections). Two concurrent resolves of the SAME escalation both passed the .get() check,
    # both wrote a human-confirmed entry into calibration_history (double-counting one real data
    # point as two independent observations), and the second `del` crashed with an uncaught
    # KeyError -- reproduced live before fixing. Locking just the pop closed that, but a follow-up
    # round found the ground-truth and threshold reads just below were still unlocked -- a
    # concurrent /api/run could commit a DIFFERENT run's ground truth in the gap between them,
    # silently stranding this run's escalation as a permanent "stale run?" 404. Fixed at the root
    # (see _RunSnapshot): `state.latest` is captured ONCE into `snapshot` right here, a single
    # atomic reference read, and every field below comes from that same captured object -- always
    # fully this run's data or fully a newer run's, never a mix, regardless of what /api/run
    # commits concurrently. _state_lock is still held around the pop specifically, for the
    # check-then-claim guarantee on this one transaction_id (a snapshot's escalations_by_id dict
    # is still a plain mutable dict, shared with any other request that grabbed the same snapshot).
    try:
        with _state_lock:
            snapshot = state.latest
            escalation = snapshot.escalations_by_id.pop(req.transaction_id, None)
            if escalation is None:
                raise HTTPException(404, f"{req.transaction_id} is not a pending escalation from the latest run")
            true_label = snapshot.ground_truth.get(req.transaction_id)
            if true_label is None:
                raise HTTPException(404, f"no source record on file for {req.transaction_id} (stale run?)")
            threshold = snapshot.result.threshold if snapshot.result else 0.90

        # confirm_human_resolution now returns the report reflecting this exact confirmation
        # (add_and_report internally) rather than a separate .report() call afterward -- a
        # concurrent reset_history used to be able to run in that gap and make this human's own
        # just-confirmed resolution vanish from their own returned report. See
        # calibration/history.py's add_and_report docstring for the live-reproduced race.
        updated_calibration = calibration_history.confirm_human_resolution(
            transaction_id=req.transaction_id,
            predicted_category=escalation["category"],
            confirmed_true_label=true_label,
            amount=escalation["amount"],
            provider=escalation["provider"],
            threshold=threshold,
        )

        return ResolveResponse(
            transaction_id=req.transaction_id,
            predicted_category=escalation["category"],
            confirmed_true_label=true_label,
            was_correct=escalation["category"] == true_label,
            updated_calibration=updated_calibration,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"Could not resolve this escalation ({type(e).__name__}: {e}).")


@app.get("/api/audit")
def api_audit(run_id: str | None = None) -> list[dict]:
    run_id = run_id or (state.latest.result.run_id if state.latest.result else None)
    if run_id is None:
        raise HTTPException(404, "no run yet")
    return audit_logger.entries_for_run(run_id)


class JournalExportResponse(BaseModel):
    format: Literal["tally", "zoho", "generic"]
    content: str
    entry_count: int
    finalized_count: int
    pending_count: int


@app.get("/api/journal/export")
def api_journal_export(format: Literal["tally", "zoho", "generic"] = "generic") -> JournalExportResponse:
    """ERP journal export (Pillar 3): regenerates the latest run's own chains from its seed —
    same pattern api_run already uses to recover ground truth — rather than storing chains in
    _AppState, keeping the atomicity-critical _RunSnapshot exactly as minimal as it already is.
    Transactions still pending in the escalation queue post with finalized=False (a pending human
    review note, not a missing entry) — the same "post what's known, don't force a false balance"
    principle app/erp/journal.py's suspense-line design already follows."""
    if state.latest.result is None:
        raise HTTPException(404, "no run yet — POST /api/run first")
    snapshot = state.latest
    result = snapshot.result
    try:
        main_batch, _ = generate(seed=result.seed, main_n=result.total_transactions, stress_n=0)
        chains = build_all_chains(main_batch)
        pending_ids = set(snapshot.escalations_by_id.keys())
        finalized_ids = {txn_id for txn_id in chains if txn_id not in pending_ids}
        entries = generate_journal_entries(chains, finalized_ids)

        if format == "tally":
            content = to_tally_xml(entries)
        elif format == "zoho":
            content = to_zoho_books_csv(entries)
        else:
            content = to_generic_csv(entries)

        return JournalExportResponse(
            format=format,
            content=content,
            entry_count=len(entries),
            finalized_count=len(finalized_ids),
            pending_count=len(chains) - len(finalized_ids),
        )
    except Exception as e:
        raise HTTPException(422, f"Could not generate the journal export ({type(e).__name__}: {e}).")


class Gstr2bResponse(BaseModel):
    formatted: dict
    match_report: Gstr2bMatchReport


@app.get("/api/gstr2b")
def api_gstr2b(seed_for_simulation: int | None = None) -> Gstr2bResponse:
    """Tax-line matcher, GSTR-2B-complete (upgrade build Phase 6): regenerates the latest run's own
    chains from its seed (same pattern api_journal_export already uses), reshapes our own books into
    GSTR-2B's own schema, and matches them against a simulated counterpart statement -- see
    app/erp/gstr2b.py's module docstring for why the counterpart is simulated rather than assumed to
    always agree."""
    if state.latest.result is None:
        raise HTTPException(404, "no run yet — POST /api/run first")
    result = state.latest.result
    try:
        main_batch, _ = generate(seed=result.seed, main_n=result.total_transactions, stress_n=0)
        chains = build_all_chains(main_batch)
        formatted = to_gstr2b_format(chains)
        simulated = generate_simulated_gstr2b(chains, seed=seed_for_simulation if seed_for_simulation is not None else result.seed)
        match_report = match_against_gstr2b(chains, simulated)
        return Gstr2bResponse(formatted=formatted, match_report=match_report)
    except Exception as e:
        raise HTTPException(422, f"Could not generate the GSTR-2B match ({type(e).__name__}: {e}).")


class PendingForecastResponse(BaseModel):
    predictions: list[SettlementPrediction]
    working_capital: WorkingCapitalReport


@app.get("/api/forecast/pending")
def api_forecast_pending(n: int = Query(10, ge=1, le=200)) -> PendingForecastResponse:
    """Forward settlement predictor (spec upgrade): a batch of genuinely in-flight transactions
    (captured, no settlement yet — generate_pending_batch(), distinct from every other batch this
    project produces) with a predicted net amount and date interval per transaction, plus a
    working-capital rollup over the same predictions. Tied to the latest run's own seed when one
    exists (so re-running the same seed shows the same pending snapshot), falls back to 42 if
    nothing has run yet — this endpoint doesn't require a prior run."""
    seed = state.latest.result.seed if state.latest.result else 42
    try:
        pending = generate_pending_batch(seed=seed, n=n)
        predictions = predict_pending_batch(pending.orders, pending.payments)
        as_of = max((p.captured_at for p in predictions), default=datetime.now(timezone.utc))
        working_capital = compute_working_capital(predictions, as_of)
        return PendingForecastResponse(predictions=predictions, working_capital=working_capital)
    except Exception as e:
        raise HTTPException(422, f"Could not generate the pending-settlement forecast ({type(e).__name__}: {e}).")


@app.get("/api/forecast/backtest")
def api_forecast_backtest(seed: int | None = Query(None, ge=1), n: int | None = Query(None, ge=1, le=2000)) -> BacktestReport:
    """Backtests the same predictor against a batch's own real settlements (regenerated from its
    seed, the same pattern api_journal_export already uses) — reports MAPE and interval coverage
    honestly, whatever they are, not rounded up.

    Defaults to the latest run when this process has one, and to the dashboard's own defaults when it
    does not. It used to 404 in the second case, which was an incidental dependency: the endpoint
    regenerates the batch from a seed and never needed prior state. That 404 became visible once the
    frontend started shipping a committed sample run — a judge landing on the Evidence page saw a
    panel calling an endpoint that failed, because the FRONTEND had a run and the BACKEND did not.
    """
    result = state.latest.result
    use_seed = seed if seed is not None else (result.seed if result else 42)
    use_n = n if n is not None else (result.total_transactions if result else 120)
    try:
        main_batch, _ = generate(seed=use_seed, main_n=use_n, stress_n=0)
        return run_backtest(main_batch)
    except Exception as e:
        raise HTTPException(422, f"Could not run the settlement forecast backtest ({type(e).__name__}: {e}).")


@app.get("/api/forecast/blind-backtest")
def api_forecast_blind_backtest(seed: int = Query(42, ge=1), n: int = Query(120, ge=1, le=2000)) -> BacktestReport:
    """The same predictor, backtested against a SEPARATE, self-contained batch whose real
    fee/tax/settlement-date were computed against a per-rail schedule drift the predictor never
    sees (app/forecast/blind_backtest.py) — unlike /api/forecast/backtest, which reuses this
    project's own generated batches, whose settlements are computed with the exact schedule the
    predictor itself reads. Reports MAPE and interval coverage honestly; both are real numbers
    that can look very different from the non-blind endpoint's, since they measure a genuinely
    different question (robustness to stale reference data, not correct application of known
    reference data)."""
    try:
        return run_blind_backtest(seed=seed, n=n)
    except Exception as e:
        raise HTTPException(422, f"Could not run the blind settlement forecast backtest ({type(e).__name__}: {e}).")


class PayrollCheckRequest(BaseModel):
    outflow_amount: int = Field(..., gt=0)
    outflow_date: date
    n: int = Field(10, ge=1, le=200)


@app.post("/api/forecast/payroll-check")
def api_forecast_payroll_check(req: PayrollCheckRequest) -> PayrollCoverageResult:
    """Given a scheduled outflow, does the forward-predicted pending cash cover it — conservative
    by construction (check_payroll_coverage only counts a prediction once its late/tolerance-ceiling
    date has passed), and honestly reports a shortfall rather than a false clear."""
    seed = state.latest.result.seed if state.latest.result else 42
    try:
        pending = generate_pending_batch(seed=seed, n=req.n)
        predictions = predict_pending_batch(pending.orders, pending.payments)
        return check_payroll_coverage(predictions, req.outflow_amount, req.outflow_date)
    except Exception as e:
        raise HTTPException(422, f"Could not check payroll coverage ({type(e).__name__}: {e}).")


class ForecastReliabilityReport(BaseModel):
    """What the forecaster refuses to predict, and whether its stated confidence is earned."""

    fit_seed: int
    holdout_seed: int
    n_assessed: int
    n_forecastable: int
    n_refused: int
    refusal_reasons: dict[str, int]
    reliability_curve: list[dict]
    largest_deviation: dict
    mape_on_forecast_set: float | None
    mape_on_everything: float | None


class QAAccuracyReport(BaseModel):
    """Whether the Q&A agent's answers are correct, not merely grounded."""

    provider: str
    phrasing: str
    seed: int
    answers: int
    numeric_correct: int
    numeric_scored: int
    numeric_accuracy: float | None
    mean_citation_jaccard: float | None
    answers_with_fabricated_ids: int
    fabrication_rate: float
    per_question: list[dict]


@app.get("/api/qa/accuracy")
def api_qa_accuracy(
    seed: int = Query(1, ge=1),
    n: int = Query(120, ge=20, le=2000),
    phrasing: Literal["seen", "held_out"] = Query("seen"),
    provider: Literal["mock", "groq", "ollama"] | None = Query(None),
) -> QAAccuracyReport:
    """Scores the Q&A agent against ground truth derived from the batch's own answer key.

    Every other loop in this project reports an accuracy figure; this one reported none. Its tests
    covered routing, grounding and fail-safes, never whether an answer was right. `phrasing` selects
    between questions worded the way the mock's keyword router expects and the same questions worded
    the way it was never built for -- the same seen/held-out split the reading experiment uses, and
    for the same reason: I wrote that router's vocabulary.

    Fabrication is reported separately because it is the failure that matters. An invented
    transaction id is a reference someone will go and look for.
    """
    try:
        batch, _ = generate(seed=seed, main_n=n, stress_n=0)
        chains = build_all_chains(batch)
        context = build_tool_context(batch, chains)
        settled_at = build_settled_at_index(batch)
        all_ids = set(chains)

        rows, numeric_hits, numeric_total, fabricated_answers = [], 0, 0, 0
        citation_scores: list[float] = []

        for spec in build_questions(batch, settled_at):
            question = spec.seen if phrasing == "seen" else spec.held_out
            truth = spec.truth(batch, chains, settled_at)
            result = answer_question(question, context, settled_at, provider=provider)
            cited = list(set(result.cited_transaction_ids) | extract_ids_from_text(result.answer))
            scored = score_answer(result.answer, cited, truth, all_ids)

            if truth.expected_number is not None:
                numeric_total += 1
                numeric_hits += int(scored["numeric_correct"])
            if scored["citation_jaccard"] is not None:
                citation_scores.append(scored["citation_jaccard"])
            if scored["n_fabricated"]:
                fabricated_answers += 1

            rows.append(
                {
                    "kind": spec.kind,
                    "question": question,
                    "answer": (result.answer or "")[:300],
                    "expected_number": truth.expected_number,
                    "numeric_correct": scored["numeric_correct"],
                    "citation_jaccard": scored["citation_jaccard"],
                    "n_fabricated": scored["n_fabricated"],
                    "fabricated_ids": scored["fabricated_ids"][:5],
                }
            )

        return QAAccuracyReport(
            provider=rows and result.provider or (provider or "mock"),
            phrasing=phrasing,
            seed=seed,
            answers=len(rows),
            numeric_correct=numeric_hits,
            numeric_scored=numeric_total,
            numeric_accuracy=round(numeric_hits / numeric_total, 4) if numeric_total else None,
            mean_citation_jaccard=round(sum(citation_scores) / len(citation_scores), 4) if citation_scores else None,
            answers_with_fabricated_ids=fabricated_answers,
            fabrication_rate=round(fabricated_answers / len(rows), 4) if rows else 0.0,
            per_question=rows,
        )
    except Exception as e:
        raise HTTPException(422, f"Could not score the Q&A agent ({type(e).__name__}: {e}).")


@app.get("/api/forecast/reliability")
def api_forecast_reliability(
    fit_seed: int = Query(1, ge=1),
    holdout_seed: int = Query(100, ge=1),
    n: int = Query(600, ge=50, le=5000),
) -> ForecastReliabilityReport:
    """The forecasting analogue of the calibration dial.

    Two things the forecaster never reported. Which predictions it declines to make and why
    (app/forecast/forecastability.py), and whether a stated confidence level is honest -- intervals
    are fitted on one batch and verified on a different one, so the curve measures calibration rather
    than memorisation. The coverage figure this project used to quote was the hit rate of a fixed SLA
    window, not a confidence level with a nominal to check it against.
    """
    try:
        fit_batch, _ = generate(seed=fit_seed, main_n=n, stress_n=0)
        holdout, _ = generate(seed=holdout_seed, main_n=n, stress_n=0)
        model = fit_intervals(fit_batch)
        curve = [dict(p.model_dump(), gap=p.gap) for p in reliability_curve(model, holdout)]
        worst = max(curve, key=lambda c: abs(c["gap"]))

        assessments = assess_batch(holdout.orders, holdout.payments, holdout.refunds)
        accepted = {t for t, a in assessments.items() if a.forecastable}
        reasons: dict[str, int] = {}
        for a in assessments.values():
            for r in a.reasons:
                reasons[r] = reasons.get(r, 0) + 1

        order_by_id = {o.order_id: o for o in holdout.orders}
        payment_by_id = {p.payment_id: p for p in holdout.payments}

        def _mape(only):
            errs = []
            for s2 in holdout.settlements:
                payment = payment_by_id.get(s2.payment_id)
                order = order_by_id.get(payment.order_id) if payment else None
                if order is None or (only is not None and order.order_id not in only):
                    continue
                if s2.settled_amount:
                    pred = predict_settlement(order, payment)
                    errs.append(abs(pred.predicted_net_amount - s2.settled_amount) / abs(s2.settled_amount))
            return round(sum(errs) / len(errs), 6) if errs else None

        return ForecastReliabilityReport(
            fit_seed=fit_seed,
            holdout_seed=holdout_seed,
            n_assessed=len(assessments),
            n_forecastable=len(accepted),
            n_refused=len(assessments) - len(accepted),
            refusal_reasons=reasons,
            reliability_curve=curve,
            largest_deviation=worst,
            mape_on_forecast_set=_mape(accepted),
            mape_on_everything=_mape(None),
        )
    except Exception as e:
        raise HTTPException(422, f"Could not build the forecast reliability report ({type(e).__name__}: {e}).")


class QARequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    provider: Literal["mock", "groq", "ollama"] | None = None  # None -> LLM_PROVIDER env var, default "mock"


@app.post("/api/qa/ask")
def api_qa_ask(req: QARequest) -> QAAnswer:
    """Settlement Q&A agent (spec upgrade): a genuinely separate agentic loop from the narrator
    (app/qa/agent.py) — regenerates the latest run's own chains from its seed, the same pattern
    api_journal_export/api_forecast_backtest already use, and answers a free-text question by
    walking the causal chain and citing the specific transaction(s), with the tool-call trace as
    evidence."""
    if state.latest.result is None:
        raise HTTPException(404, "no run yet — POST /api/run first")
    result = state.latest.result
    try:
        main_batch, _ = generate(seed=result.seed, main_n=result.total_transactions, stress_n=0)
        chains = build_all_chains(main_batch)
        context = build_tool_context(main_batch, chains)
        settled_at_index = build_settled_at_index(main_batch)
        return answer_question(req.question, context, settled_at_index, provider=req.provider)
    except Exception as e:
        raise HTTPException(422, f"Could not answer the question ({type(e).__name__}: {e}).")


class TransactionScenario(BaseModel):
    """A judge-submitted (or hand-crafted) scenario — one or more transactions, evaluated live
    against the real pipeline instead of a pre-generated batch. This is the 'break it' demo path
    : submit a duplicate-refund-shaped or netting-trap-shaped case and watch the system
    correctly escalate instead of guessing. Never scored against ground truth (there isn't any) —
    this is "what would the system do", not a calibration input.

    Note: duplicate-refund and genuine-error detection work on a single submitted transaction
    (they only need that transaction's own records). Netting-trap detection needs a *pair* — submit
    two transactions with offsetting deltas in the same settlement_batch_id to see it caught.
    """

    orders: list[Order]
    payments: list[Payment]
    refunds: list[Refund] = []
    settlements: list[Settlement]
    ledger_entries: list[LedgerEntry]
    provider: Literal["mock", "groq", "ollama"] | None = None  # see RunRequest.provider's comment

    @model_validator(mode="after")
    def _no_duplicate_primary_keys(self) -> "TransactionScenario":
        # build_all_chains (chain/builder.py) builds three dicts keyed by order_id/payment_id --
        # a duplicate key there silently overwrites the earlier record with no error, dropping a
        # submitted transaction with no indication anything was lost. An external audit 2026-08-24
        # reproduced this with two orders sharing an order_id: the API returned 1 result, not 2.
        # Checked here, once, for all four record types that build_all_chains keys by, rather than
        # only the one shape that was actually reproduced.
        for label, ids in (
            ("order_id", [o.order_id for o in self.orders]),
            ("payment_id", [p.payment_id for p in self.payments]),
            ("settlement_id", [s.settlement_id for s in self.settlements]),
            ("ledger_id", [l.ledger_id for l in self.ledger_entries]),
        ):
            seen = set()
            for id_ in ids:
                if id_ in seen:
                    raise ValueError(f"duplicate {label} {id_!r} — every {label} in a submitted scenario must be unique")
                seen.add(id_)
        return self


class EvaluatedTransaction(BaseModel):
    transaction_id: str
    resolution: str
    category: str | None
    confidence: float | None
    reasoning: str | None
    tool_calls: list[dict] = []


class EvaluateResponse(BaseModel):
    results: list[EvaluatedTransaction]


@app.post("/api/transactions/evaluate")
def api_evaluate_transactions(scenario: TransactionScenario) -> EvaluateResponse:
    # This is the one endpoint in the whole system that processes genuinely untrusted,
    # hand-crafted input -- every other pipeline path (run_batch) only ever runs on generator-
    # produced data with referential integrity guaranteed by construction (every order is
    # generated together with a matching payment/settlement/ledger entry). A judge editing or
    # hand-building a scenario for the live "break it" demo has no such guarantee, and a missing or
    # mismatched reference (e.g. a settlement pointing at the wrong payment_id) previously crashed
    # this endpoint with an opaque 500 and no explanation — caught by an external audit 2026-08-24
    # reading main.py directly, reproduced with three separate realistic malformed payloads. Same
    # principle as narrate()'s own backstop: a specific, informative error for the failure mode
    # that's actually been seen (KeyError from build_all_chains's referential lookups), plus a
    # broader backstop for anything else, so this endpoint can never crash the way narrate() used
    # to before rounds 5-8 closed that off.
    try:
        batch = SyntheticBatch(
            orders=scenario.orders,
            payments=scenario.payments,
            refunds=scenario.refunds,
            settlements=scenario.settlements,
            ledger_entries=scenario.ledger_entries,
            ground_truth=[],
        )
        chains = build_all_chains(batch)
        match_results = run_matching_engine(chains)
        context = build_tool_context(batch, chains, audit_logger=audit_logger)
        provider = scenario.provider or os.environ.get("LLM_PROVIDER", "mock")

        threshold = state.latest.result.threshold if state.latest.result else 0.90
        # a judge-submitted transaction goes through the exact same calibration gate as a batch one
        # — accumulated trust decides auto-resolve vs. escalate here too, not a special-cased raw
        # dump of whatever the narrator said.
        auto_resolve_categories = set(calibration_history.report(threshold=threshold).auto_resolve_categories)

        results: list[EvaluatedTransaction] = []
        for txn_id, result in match_results.items():
            if result.resolution == "needs_narration":
                output = narrate(chains[txn_id], context, provider=provider)
                decision = _final_decision(result, output.category, output.provider, auto_resolve_categories)
                results.append(
                    EvaluatedTransaction(
                        transaction_id=txn_id,
                        resolution=decision,
                        category=output.category,
                        confidence=output.confidence,
                        reasoning=output.reasoning,
                        tool_calls=[tc.model_dump() for tc in output.tool_calls],
                    )
                )
            else:
                results.append(
                    EvaluatedTransaction(
                        transaction_id=txn_id,
                        resolution=result.resolution,
                        category=result.category,
                        confidence=result.confidence,
                        reasoning=result.reasoning,
                    )
                )
        return EvaluateResponse(results=results)
    except KeyError as e:
        raise HTTPException(
            422,
            f"Transaction scenario has a broken reference: no matching record for {e}. Every order "
            f"needs a payment (matched by order_id), a settlement (matched by the payment's "
            f"payment_id), and a ledger entry (matched by order_id).",
        )
    except Exception as e:
        raise HTTPException(422, f"Could not evaluate this scenario ({type(e).__name__}: {e}); check the submitted records for consistency.")


class WebhookReceivedResponse(BaseModel):
    verified: bool
    parsed: ParsedSettlementEvent
    note: str


@app.post("/api/webhooks/razorpay")
async def api_razorpay_webhook(request: Request, x_razorpay_signature: str | None = Header(default=None)) -> WebhookReceivedResponse:
    """A real Razorpay webhook receiver -- the gap LIMITATIONS.md names directly: /api/transactions/
    evaluate is a real, tested integration point, but nothing previously verified an actual incoming
    webhook's signature or parsed its real event shape (see app/webhooks/razorpay.py for both,
    verified against Razorpay's own current docs, not guessed).

    Reads the RAW body (never `await request.json()` first -- re-serializing changes byte-for-byte
    content and silently breaks signature verification, exactly the mistake Razorpay's own docs warn
    against). Requires RAZORPAY_WEBHOOK_SECRET in the environment; refuses to skip verification if
    it's unset rather than silently trusting an unverified payload -- a webhook endpoint with no way
    to check who sent it is not a safe default to ship.

    Parses and verifies the real event; does not attempt to reconstruct a full causal chain from a
    settlement-only payload (see the module docstring for why that boundary is real, not a shortcut)
    -- a real merchant integration would take the parsed settlement here and feed it, alongside the
    order/payment/ledger data it already has from its own separate integration, into the existing
    /api/transactions/evaluate pipeline."""
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not webhook_secret:
        raise HTTPException(500, "RAZORPAY_WEBHOOK_SECRET is not configured -- refusing to accept an unverifiable webhook")
    if not x_razorpay_signature:
        raise HTTPException(401, "missing X-Razorpay-Signature header")

    raw_body = await request.body()
    try:
        verify_razorpay_signature(raw_body, x_razorpay_signature, webhook_secret)
    except WebhookSignatureError as e:
        raise HTTPException(401, str(e))

    try:
        payload = json.loads(raw_body)
        parsed = parse_settlement_processed_event(payload)
    except (json.JSONDecodeError, WebhookParseError) as e:
        raise HTTPException(422, f"could not parse webhook payload ({type(e).__name__}: {e})")

    return WebhookReceivedResponse(
        verified=True,
        parsed=parsed,
        note=(
            "Signature verified and event parsed. This settlement leg alone can't be reconciled -- "
            "feed it, alongside this order's already-known payment/ledger data, into "
            "POST /api/transactions/evaluate to run it through the real pipeline."
        ),
    )


@app.api_route("/api/health", methods=["GET", "HEAD"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/sandbox/status")
def api_sandbox_status() -> dict:
    """Live connectivity check against the real Razorpay Test Mode API -- proves
    RAZORPAY_KEY_ID/SECRET are real, working credentials rather than claiming so.
    See app/connectors/razorpay_sandbox.py for exactly what is and isn't real here.

    Not called by the frontend and not on any polling path -- each hit creates a real
    order against the live account (create_test_order's probe), so this is meant for a
    manual/occasional check, not a health-check target or anything hit on a timer."""
    from app.connectors.razorpay_sandbox import sandbox_status

    return sandbox_status()
