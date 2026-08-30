"""Tests for the time-to-revocation drill (app/calibration/revocation_drill.py) -- a demo harness
around the real calibrate()/detect_drift()/CalibrationHistory machinery, not new statistics. Every
number here is empirically observed by actually running the drill (see BUILD_LOG.md), not invented
round targets."""

from app.calibration.calibrator import NEVER_AUTO_RESOLVE
from app.calibration.revocation_drill import run_revocation_drill


def test_genuine_error_can_never_be_drilled_since_it_can_never_qualify():
    assert "genuine_error" in NEVER_AUTO_RESOLVE  # the fixture assumption this test depends on
    report = run_revocation_drill(category="genuine_error")
    assert report.revoked is False
    assert report.decisions_survived is None
    assert "NEVER_AUTO_RESOLVE" in report.revocation_reason


def test_qualifying_phase_alone_actually_reaches_auto_resolve():
    """A drill whose own seeding never qualifies would silently report revoked=False for the wrong
    reason (never having anything to revoke) -- this proves the default n_qualifying=40 really does
    clear both the distinct-transaction floor and the Wilson bound before any regression starts."""
    report = run_revocation_drill(category="netting_trap", regression_budget=0)
    # regression_budget=0 means no wrong decisions are ever injected -- if qualifying alone hadn't
    # cleared auto_resolve, revoked would be False with a "did not qualify" reason instead of the
    # "not revoked within 0 regression decisions" one this asserts.
    assert report.revoked is False
    assert "not revoked within 0" in report.revocation_reason


def test_a_deliberately_wrong_decision_after_qualifying_gets_revoked():
    report = run_revocation_drill(category="netting_trap")
    assert report.revoked is True
    assert report.decisions_survived is not None and report.decisions_survived >= 1
    assert report.amount_survived == report.decisions_survived * 50_000
    assert "control limit" in report.revocation_reason or "distinct transaction" in report.revocation_reason


def test_drill_runs_against_an_isolated_history_not_the_real_app_database():
    """Confirms this can be called repeatedly (as an API endpoint would be, on every request)
    without accumulating state across calls -- each call gets a fresh isolated history."""
    first = run_revocation_drill(category="netting_trap")
    second = run_revocation_drill(category="netting_trap")
    assert first.decisions_survived == second.decisions_survived
    assert first.amount_survived == second.amount_survived


def test_different_qualifying_categories_can_each_be_drilled():
    for category in ("netting_trap", "duplicate_refund"):
        report = run_revocation_drill(category=category)
        assert report.category == category
        assert report.qualifying_decision_count == 60
        assert report.revoked is True
