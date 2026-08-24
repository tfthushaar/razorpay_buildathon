"""FastAPI layer (spec §7) — exposes the pipeline over HTTP for the React dashboard.

Single-session, in-memory app state is deliberate: this is a hackathon demo tool for one
presenter driving one dashboard, not a multi-tenant service. The audit log and calibration
history are the two things that persist to SQLite (spec's "doesn't need to be fancy"); everything
else is cheap to recompute from a batch run, consistent with the rest of the system's cost
posture (see BUILD_LOG.md, [[feedback-build-autonomy-and-cost]]).
"""

import threading
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

load_dotenv()  # so LLM_PROVIDER / GROQ_API_KEY in backend/.env reach os.environ before any request

import os

from app.audit.logger import AuditLogger
from app.calibration.calibrator import CalibrationReport
from app.calibration.history import CalibrationHistory
from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.data_gen.schemas import LedgerEntry, Order, Payment, Refund, Settlement, SyntheticBatch
from app.matching.engine import run_matching_engine
from app.narrator.agent import narrate
from app.narrator.tools import build_tool_context
from app.pipeline import BatchRunResult, _final_decision, run_batch

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

app = FastAPI(title="Settlement Reconciliation Copilot API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

audit_logger = AuditLogger(db_path=DATA_DIR / "audit_log.db")
calibration_history = CalibrationHistory(db_path=DATA_DIR / "calibration_history.db")


class _AppState:
    latest_result: BatchRunResult | None = None
    latest_ground_truth: dict[str, str] = {}
    latest_escalations_by_id: dict[str, dict] = {}


state = _AppState()
# guards the check-then-claim sequence in /api/escalations/resolve -- see that endpoint's own
# comment for why this exists (a real race, reproduced live, not a theoretical one).
_escalation_lock = threading.Lock()


class RunRequest(BaseModel):
    seed: int = 42
    main_n: int = 120
    stress_n: int = 40
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
        )
        state.latest_result = result
        state.latest_escalations_by_id = {e.transaction_id: e.model_dump() for e in result.escalations}

        # Ground truth is deliberately never part of BatchRunResult (the pipeline must not leak it
        # to the dashboard as if it were a known answer). Regenerating with the same seed is a
        # cheap, deterministic, non-LLM call purely to recover the lookup the "resolve an
        # escalation" flow needs, mirroring how a human reviewer would confirm against the real
        # source records.
        main_batch, _ = generate(seed=req.seed, main_n=req.main_n, stress_n=req.stress_n)
        state.latest_ground_truth = {g.transaction_id: g.true_label for g in main_batch.ground_truth}

        return result
    except Exception as e:
        raise HTTPException(422, f"Could not complete this run ({type(e).__name__}: {e}); check the request parameters.")


@app.get("/api/runs/latest")
def api_latest_run() -> BatchRunResult:
    if state.latest_result is None:
        raise HTTPException(404, "no run yet — POST /api/run first")
    return state.latest_result


@app.get("/api/calibration")
def api_calibration(threshold: float = Query(0.90, ge=0.0, le=1.0)) -> CalibrationReport:
    """The live threshold dial: a cheap re-aggregation over the accumulated history, not a
    pipeline re-run (spec §6.5)."""
    return calibration_history.report(threshold=threshold)


@app.post("/api/escalations/resolve")
def api_resolve_escalation(req: ResolveRequest) -> ResolveResponse:
    """The human-feedback loop (spec §6.5): confirms one escalated case against its real source
    records and folds the outcome back into the accumulated calibration history."""
    # check-then-delete used to be two separate steps (a .get() here, a del at the bottom) with no
    # lock between them -- a real TOCTOU race under FastAPI's genuinely-concurrent threadpool
    # (round 10 already disproved the "effectively serialized" assumption once, for the SQLite
    # connections). Two concurrent resolves of the SAME escalation both passed the .get() check,
    # both wrote a human-confirmed entry into calibration_history (silently double-counting one
    # real data point as two independent observations, corrupting the Wilson interval's sample-
    # independence assumption), and the second `del` then crashed with an uncaught KeyError --
    # reproduced live before fixing. _escalation_lock makes "check and claim" one atomic step via
    # dict.pop(key, None): only the request that actually removes the entry proceeds: the other
    # correctly sees it as already resolved and 404s, same as if it had arrived a moment later.
    with _escalation_lock:
        escalation = state.latest_escalations_by_id.pop(req.transaction_id, None)
    if escalation is None:
        raise HTTPException(404, f"{req.transaction_id} is not a pending escalation from the latest run")
    true_label = state.latest_ground_truth.get(req.transaction_id)
    if true_label is None:
        raise HTTPException(404, f"no source record on file for {req.transaction_id} (stale run?)")

    calibration_history.confirm_human_resolution(
        transaction_id=req.transaction_id,
        predicted_category=escalation["category"],
        confirmed_true_label=true_label,
        amount=escalation["amount"],
        provider=escalation["provider"],
    )

    threshold = state.latest_result.threshold if state.latest_result else 0.90
    return ResolveResponse(
        transaction_id=req.transaction_id,
        predicted_category=escalation["category"],
        confirmed_true_label=true_label,
        was_correct=escalation["category"] == true_label,
        updated_calibration=calibration_history.report(threshold=threshold),
    )


@app.get("/api/audit")
def api_audit(run_id: str | None = None) -> list[dict]:
    run_id = run_id or (state.latest_result.run_id if state.latest_result else None)
    if run_id is None:
        raise HTTPException(404, "no run yet")
    return audit_logger.entries_for_run(run_id)


class TransactionScenario(BaseModel):
    """A judge-submitted (or hand-crafted) scenario — one or more transactions, evaluated live
    against the real pipeline instead of a pre-generated batch. This is the 'break it' demo path
    (spec §6.10): submit a duplicate-refund-shaped or netting-trap-shaped case and watch the system
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
        context = build_tool_context(batch, chains)
        provider = scenario.provider or os.environ.get("LLM_PROVIDER", "mock")

        threshold = state.latest_result.threshold if state.latest_result else 0.90
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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
