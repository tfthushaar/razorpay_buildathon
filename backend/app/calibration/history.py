"""Accumulated calibration history — persists scored decisions across every batch run and every
human-confirmed escalation, so trust earned in one run isn't lost on the next.

This is a mid-build architecture change, not part of the original per-batch design: at the spec's
suggested batch size (50-200 records), each narrator category only gets ~3-12 samples in a
single batch, split across duplicate_refund/netting_trap/genuine_error. A Wilson lower bound
mathematically cannot clear a 90% threshold at that N even at 100% point accuracy (needs roughly
N=40 at 100% accuracy — see BUILD_LOG.md 2026-08-23 for the worked numbers from a real run). Reset
per batch, the calibration layer would escalate every narrator-classified transaction forever,
which makes "calibrated autonomy" true but never demonstrable. Trust has to accumulate the way a
real system's would — across batches and across human-confirmed resolutions (spec's feedback
loop) — not re-earned from zero every run.
"""

import sqlite3
import threading
from pathlib import Path

from app.calibration.calibrator import CalibrationReport, ScoredDecision, calibrate

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "calibration_history.db"


class CalibrationHistory:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: see the identical note in audit/logger.py — FastAPI's sync
        # endpoints run in a worker threadpool, not the thread this singleton was created in. That
        # note used to claim a single connection was fine because request handling was "effectively
        # serialized" -- an external audit 2026-08-24 disproved this empirically (concurrent
        # /api/run calls crashed the shared connection). self._lock serializes access explicitly.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS scored_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT NOT NULL,
                predicted_category TEXT NOT NULL,
                true_label TEXT NOT NULL,
                amount INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'mock',
                source TEXT NOT NULL DEFAULT 'batch'
            )"""
        )
        self._conn.commit()

    def add(self, decisions: list[ScoredDecision], source: str = "batch") -> None:
        if not decisions:
            return
        with self._lock:
            self._insert_locked(decisions, source)

    def _insert_locked(self, decisions: list[ScoredDecision], source: str) -> None:
        # caller must already hold self._lock -- factored out so add_and_report can share it
        # without a second (re-entrant, and therefore self-deadlocking) acquisition.
        self._conn.executemany(
            "INSERT INTO scored_decisions (transaction_id, predicted_category, true_label, amount, provider, source) VALUES (?, ?, ?, ?, ?, ?)",
            [(d.transaction_id, d.predicted_category, d.true_label, d.amount, d.provider, source) for d in decisions],
        )
        self._conn.commit()

    def add_and_report(self, decisions: list[ScoredDecision], threshold: float, source: str = "batch") -> CalibrationReport:
        """Insert this call's own decisions and read back the report in one lock acquisition, so
        no other thread's `clear()` (the "reset calibration history" checkbox, wired to
        `/api/run`) can run in the gap between them. Before this existed, `add()` and `report()`
        were two independent, individually lock-safe calls -- an external audit 2026-08-24 proved
        that gap live: request A added 9 decisions, request B's concurrent `reset_history` cleared
        the table and added its own 22, and A's own `report()` came back reflecting B's data, with
        A's own just-persisted decisions gone -- not delayed, permanently gone, no error, silently
        corrupting the exact ledger the "trust accumulates over time" pitch depends on. This
        doesn't prevent a concurrent `clear()` from wiping history around this call (that's the
        reset checkbox doing what it's supposed to do); it guarantees THIS call's own report always
        reflects THIS call's own contribution, not a different request's."""
        with self._lock:
            if decisions:
                self._insert_locked(decisions, source)
            # ORDER BY id: without it, SQLite's row order for a plain sequential scan isn't
            # guaranteed by the SQL standard, even though it happens to come back in insertion
            # order in practice for a table with no index affecting this query. Explicit ordering
            # matters now that calibrate() needs a genuinely chronological sequence per category
            # for EWMA drift detection (app/calibration/drift.py) -- relying on unspecified behavior
            # for something an actual decision now depends on would be exactly the kind of
            # "verify, don't assume" gap this project's own discipline exists to catch.
            cursor = self._conn.execute("SELECT transaction_id, predicted_category, true_label, amount, provider FROM scored_decisions ORDER BY id ASC")
            all_decisions = [ScoredDecision(transaction_id=r[0], predicted_category=r[1], true_label=r[2], amount=r[3], provider=r[4]) for r in cursor.fetchall()]
        return calibrate(all_decisions, threshold=threshold)

    def confirm_human_resolution(
        self, transaction_id: str, predicted_category: str, confirmed_true_label: str, amount: int, provider: str, threshold: float
    ) -> CalibrationReport:
        """The feedback loop entry point : a human resolving an escalated case is a
        confirmed data point, folded straight back into the accumulated history. `provider` is
        whatever produced the *original* prediction being confirmed — a human confirming a
        mock-derived guess still doesn't make it AI judgment, so it must not silently start
        counting toward the auto-resolve gate just because a human looked at it. Returns the
        report reflecting THIS confirmation via add_and_report, not a separate add()+report() pair
        — the same live-reproduced race add_and_report's own docstring describes applies here too:
        a concurrent reset_history could otherwise make a human's own just-confirmed resolution
        vanish from their own returned report."""
        return self.add_and_report(
            [
                ScoredDecision(
                    transaction_id=transaction_id,
                    predicted_category=predicted_category,
                    true_label=confirmed_true_label,
                    amount=amount,
                    provider=provider,
                )
            ],
            threshold=threshold,
            source="human_confirmed",
        )

    def all_decisions(self) -> list[ScoredDecision]:
        with self._lock:
            # ORDER BY id: without it, SQLite's row order for a plain sequential scan isn't
            # guaranteed by the SQL standard, even though it happens to come back in insertion
            # order in practice for a table with no index affecting this query. Explicit ordering
            # matters now that calibrate() needs a genuinely chronological sequence per category
            # for EWMA drift detection (app/calibration/drift.py) -- relying on unspecified behavior
            # for something an actual decision now depends on would be exactly the kind of
            # "verify, don't assume" gap this project's own discipline exists to catch.
            cursor = self._conn.execute("SELECT transaction_id, predicted_category, true_label, amount, provider FROM scored_decisions ORDER BY id ASC")
            return [ScoredDecision(transaction_id=r[0], predicted_category=r[1], true_label=r[2], amount=r[3], provider=r[4]) for r in cursor.fetchall()]

    def report(self, threshold: float = 0.90) -> CalibrationReport:
        return calibrate(self.all_decisions(), threshold=threshold)

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scored_decisions")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
