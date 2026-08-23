"""FastAPI layer (spec §7) — exposes the pipeline over HTTP for the React dashboard.

Single-session, in-memory app state is deliberate: this is a hackathon demo tool for one
presenter driving one dashboard, not a multi-tenant service. The audit log and calibration
history are the two things that persist to SQLite (spec's "doesn't need to be fancy"); everything
else is cheap to recompute from a batch run, consistent with the rest of the system's cost
posture (see BUILD_LOG.md, [[feedback-build-autonomy-and-cost]]).
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()  # so LLM_PROVIDER / GROQ_API_KEY in backend/.env reach os.environ before any request

from app.audit.logger import AuditLogger
from app.calibration.calibrator import CalibrationReport
from app.calibration.history import CalibrationHistory
from app.data_gen.generate import generate
from app.pipeline import BatchRunResult, run_batch

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


class RunRequest(BaseModel):
    seed: int = 42
    main_n: int = 120
    stress_n: int = 40
    threshold: float = 0.90
    provider: str | None = None  # None -> LLM_PROVIDER env var, default "mock"
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

    # Ground truth is deliberately never part of BatchRunResult (the pipeline must not leak it to
    # the dashboard as if it were a known answer). Regenerating with the same seed is a cheap,
    # deterministic, non-LLM call purely to recover the lookup the "resolve an escalation" flow
    # needs, mirroring how a human reviewer would confirm against the real source records.
    main_batch, _ = generate(seed=req.seed, main_n=req.main_n, stress_n=req.stress_n)
    state.latest_ground_truth = {g.transaction_id: g.true_label for g in main_batch.ground_truth}

    return result


@app.get("/api/runs/latest")
def api_latest_run() -> BatchRunResult:
    if state.latest_result is None:
        raise HTTPException(404, "no run yet — POST /api/run first")
    return state.latest_result


@app.get("/api/calibration")
def api_calibration(threshold: float = 0.90) -> CalibrationReport:
    """The live threshold dial: a cheap re-aggregation over the accumulated history, not a
    pipeline re-run (spec §6.5)."""
    return calibration_history.report(threshold=threshold)


@app.post("/api/escalations/resolve")
def api_resolve_escalation(req: ResolveRequest) -> ResolveResponse:
    """The human-feedback loop (spec §6.5): confirms one escalated case against its real source
    records and folds the outcome back into the accumulated calibration history."""
    escalation = state.latest_escalations_by_id.get(req.transaction_id)
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
    )
    del state.latest_escalations_by_id[req.transaction_id]

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


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
