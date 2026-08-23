"""Accumulated calibration history — persists scored decisions across every batch run and every
human-confirmed escalation, so trust earned in one run isn't lost on the next.

This is a mid-build architecture change, not part of the original per-batch design: at the spec's
suggested batch size (50-200 records, §6.1), each narrator category only gets ~3-12 samples in a
single batch, split across duplicate_refund/netting_trap/genuine_error. A Wilson lower bound
mathematically cannot clear a 90% threshold at that N even at 100% point accuracy (needs roughly
N=40 at 100% accuracy — see BUILD_LOG.md 2026-08-23 for the worked numbers from a real run). Reset
per batch, the calibration layer would escalate every narrator-classified transaction forever,
which makes "calibrated autonomy" true but never demonstrable. Trust has to accumulate the way a
real system's would — across batches and across human-confirmed resolutions (spec's feedback
loop, §6.5) — not re-earned from zero every run.
"""

import sqlite3
from pathlib import Path

from app.calibration.calibrator import CalibrationReport, ScoredDecision, calibrate

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "calibration_history.db"


class CalibrationHistory:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: see the identical note in audit/logger.py — FastAPI's sync
        # endpoints run in a worker threadpool, not the thread this singleton was created in.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
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
        self._conn.executemany(
            "INSERT INTO scored_decisions (transaction_id, predicted_category, true_label, amount, provider, source) VALUES (?, ?, ?, ?, ?, ?)",
            [(d.transaction_id, d.predicted_category, d.true_label, d.amount, d.provider, source) for d in decisions],
        )
        self._conn.commit()

    def confirm_human_resolution(
        self, transaction_id: str, predicted_category: str, confirmed_true_label: str, amount: int, provider: str
    ) -> None:
        """The feedback loop entry point (spec §6.5): a human resolving an escalated case is a
        confirmed data point, folded straight back into the accumulated history. `provider` is
        whatever produced the *original* prediction being confirmed — a human confirming a
        mock-derived guess still doesn't make it AI judgment, so it must not silently start
        counting toward the auto-resolve gate just because a human looked at it."""
        self.add(
            [
                ScoredDecision(
                    transaction_id=transaction_id,
                    predicted_category=predicted_category,
                    true_label=confirmed_true_label,
                    amount=amount,
                    provider=provider,
                )
            ],
            source="human_confirmed",
        )

    def all_decisions(self) -> list[ScoredDecision]:
        cursor = self._conn.execute("SELECT transaction_id, predicted_category, true_label, amount, provider FROM scored_decisions")
        return [ScoredDecision(transaction_id=r[0], predicted_category=r[1], true_label=r[2], amount=r[3], provider=r[4]) for r in cursor.fetchall()]

    def report(self, threshold: float = 0.90) -> CalibrationReport:
        return calibrate(self.all_decisions(), threshold=threshold)

    def clear(self) -> None:
        self._conn.execute("DELETE FROM scored_decisions")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
