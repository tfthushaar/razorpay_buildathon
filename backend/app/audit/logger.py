"""Audit logger (spec §6.8): every decision, timestamped, with the narrated reason, the
tool-call trace, and links back to the source rows it was based on. SQLite, append-only —
"doesn't need to be fancy" per spec, and this is the primary source material for the
submission's required "what broke and how I recovered" narrative (pulled from BUILD_LOG.md,
which is the human-readable twin of this machine-readable log).
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "audit_log.db"

Decision = str  # "clean_pass1" | "auto_resolved_deterministic" | "auto_resolved_calibrated" | "escalated"


class AuditEntry(BaseModel):
    transaction_id: str
    decision: Decision
    category: str | None
    confidence: float | None
    reasoning: str | None
    tool_calls: list[dict] = []
    order_id: str
    payment_id: str
    settlement_id: str
    ledger_id: str
    refund_ids: list[str] = []
    run_id: str
    timestamp: str = ""

    def stamped(self) -> "AuditEntry":
        return self.model_copy(update={"timestamp": datetime.now(timezone.utc).isoformat()})


class AuditLogger:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI runs sync endpoints in a worker threadpool, so this
        # connection (created once, at import time, in the main thread) gets used from a
        # different thread on every request. A single global connection is fine at this app's
        # scale (one demo session, effectively serialized request handling) — see BUILD_LOG.md.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                category TEXT,
                confidence REAL,
                reasoning TEXT,
                tool_calls_json TEXT,
                order_id TEXT,
                payment_id TEXT,
                settlement_id TEXT,
                ledger_id TEXT,
                refund_ids_json TEXT,
                timestamp TEXT NOT NULL
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_txn ON audit_log(transaction_id)")
        self._conn.commit()

    def log(self, entry: AuditEntry) -> int:
        stamped = entry.stamped()
        cursor = self._conn.execute(
            """INSERT INTO audit_log
               (run_id, transaction_id, decision, category, confidence, reasoning, tool_calls_json,
                order_id, payment_id, settlement_id, ledger_id, refund_ids_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stamped.run_id,
                stamped.transaction_id,
                stamped.decision,
                stamped.category,
                stamped.confidence,
                stamped.reasoning,
                json.dumps(stamped.tool_calls),
                stamped.order_id,
                stamped.payment_id,
                stamped.settlement_id,
                stamped.ledger_id,
                json.dumps(stamped.refund_ids),
                stamped.timestamp,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def log_many(self, entries: list[AuditEntry]) -> None:
        for entry in entries:
            self.log(entry)

    def entries_for_run(self, run_id: str) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM audit_log WHERE run_id = ? ORDER BY id ASC", (run_id,))
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()
