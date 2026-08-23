"""API-level smoke tests for the FastAPI layer (spec §7). Uses the mock provider throughout —
zero cost, deterministic. These exercise the same module-level app state a real dashboard session
would, including the live threshold dial and the human-feedback resolve flow end-to-end over HTTP.
"""

from fastapi.testclient import TestClient

from app.main import app, calibration_history

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_run_then_fetch_latest():
    calibration_history.clear()
    run_resp = client.post("/api/run", json={"seed": 42, "main_n": 100, "stress_n": 30, "threshold": 0.90, "provider": "mock"})
    assert run_resp.status_code == 200
    result = run_resp.json()
    assert result["total_transactions"] == 100
    assert result["provider"] == "mock"

    latest_resp = client.get("/api/runs/latest")
    assert latest_resp.status_code == 200
    assert latest_resp.json()["run_id"] == result["run_id"]


def test_calibration_dial_recomputes_without_rerunning_pipeline():
    calibration_history.clear()
    client.post("/api/run", json={"seed": 42, "main_n": 120, "stress_n": 0, "threshold": 0.90, "provider": "mock"})

    loose = client.get("/api/calibration", params={"threshold": 0.3})
    strict = client.get("/api/calibration", params={"threshold": 0.999})
    assert loose.status_code == 200 and strict.status_code == 200
    loose_auto = {c["category"] for c in loose.json()["categories"] if c["decision"] == "auto_resolve"}
    strict_auto = {c["category"] for c in strict.json()["categories"] if c["decision"] == "auto_resolve"}
    assert strict_auto <= loose_auto


def test_resolve_escalation_feeds_back_into_history():
    calibration_history.clear()
    run_resp = client.post("/api/run", json={"seed": 7, "main_n": 150, "stress_n": 0, "threshold": 0.90, "provider": "mock"})
    result = run_resp.json()
    assert result["escalations"], "seed=7/n=150 should produce at least one escalation to resolve"

    txn_id = result["escalations"][0]["transaction_id"]
    predicted = result["escalations"][0]["category"]

    resolve_resp = client.post("/api/escalations/resolve", json={"transaction_id": txn_id})
    assert resolve_resp.status_code == 200
    body = resolve_resp.json()
    assert body["transaction_id"] == txn_id
    assert body["predicted_category"] == predicted
    assert isinstance(body["was_correct"], bool)
    assert "updated_calibration" in body

    # resolving the same one twice should now 404 -- it's no longer a pending escalation
    second_attempt = client.post("/api/escalations/resolve", json={"transaction_id": txn_id})
    assert second_attempt.status_code == 404


def test_audit_endpoint_returns_entries_for_the_latest_run():
    calibration_history.clear()
    run_resp = client.post("/api/run", json={"seed": 3, "main_n": 60, "stress_n": 0, "threshold": 0.90, "provider": "mock"})
    run_id = run_resp.json()["run_id"]

    audit_resp = client.get("/api/audit")
    assert audit_resp.status_code == 200
    entries = audit_resp.json()
    assert len(entries) == 60
    assert all(e["run_id"] == run_id for e in entries)
