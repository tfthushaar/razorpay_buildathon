"""Shared pytest fixtures.

`isolated_app_state` exists because of a real bug an external audit caught (2026-08-24):
test_api.py originally imported the FastAPI app's live module-level `calibration_history` and
`audit_logger` singletons directly and called `.clear()` on them — the exact same SQLite files a
real dashboard session persists to (`backend/data/*.db`). Running `pytest` (the README's own
documented verification step) was silently wiping any accumulated demo history, which directly
undercuts the "trust accumulates over time" pitch this whole project is built around. This fixture
redirects the app's singletons to temp-file-backed instances for the duration of each test.
"""

import tempfile
from pathlib import Path

import pytest

import app.main as main_module
from app.audit.logger import AuditLogger
from app.calibration.history import CalibrationHistory


@pytest.fixture
def isolated_app_state(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        test_audit_logger = AuditLogger(db_path=Path(tmp) / "audit_log.db")
        test_calibration_history = CalibrationHistory(db_path=Path(tmp) / "calibration_history.db")
        monkeypatch.setattr(main_module, "audit_logger", test_audit_logger)
        monkeypatch.setattr(main_module, "calibration_history", test_calibration_history)
        monkeypatch.setattr(main_module, "state", main_module._AppState())
        yield test_calibration_history, test_audit_logger
        test_audit_logger.close()
        test_calibration_history.close()
