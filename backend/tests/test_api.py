"""API-level smoke tests for the FastAPI layer (spec §7). Uses the mock provider throughout —
zero cost, deterministic. These exercise the same module-level app state a real dashboard session
would, including the live threshold dial and the human-feedback resolve flow end-to-end over HTTP.

Every test that touches state uses the `isolated_app_state` fixture (see conftest.py) rather than
the app's live singletons — see that fixture's docstring for why this matters.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_resolving_many_mock_escalations_over_http_cannot_graduate_a_category(isolated_app_state):
    """Permanent regression guard for the exact adversarial scenario a round-2 external audit
    constructed ad hoc to stress-test the provider-aware calibration fix (2026-08-24): repeatedly
    running mock batches and resolving every escalation via the real /api/escalations/resolve HTTP
    path -- always confirming the model "correct", the best case for an attacker -- must never let
    a category earn auto_resolve. The audit found this held (522 resolutions, still all escalate)
    but noted no committed test exercised it via HTTP at volume; this is that test, kept smaller
    for CI speed while covering the same property."""
    for seed in range(1, 6):
        run_resp = client.post("/api/run", json={"seed": seed, "main_n": 100, "stress_n": 0, "threshold": 0.90, "provider": "mock"})
        for escalation in run_resp.json()["escalations"]:
            client.post("/api/escalations/resolve", json={"transaction_id": escalation["transaction_id"]})

    final = client.get("/api/calibration", params={"threshold": 0.01}).json()
    for c in final["categories"]:
        assert c["decision"] == "escalate", f"{c['category']} auto-resolved after {c['n']} 'confirmed' mock-derived resolutions"
        assert c["n"] == 0
        assert c["mock_n"] > 0


def test_run_then_fetch_latest(isolated_app_state):
    run_resp = client.post("/api/run", json={"seed": 42, "main_n": 100, "stress_n": 30, "threshold": 0.90, "provider": "mock"})
    assert run_resp.status_code == 200
    result = run_resp.json()
    assert result["total_transactions"] == 100
    assert result["provider"] == "mock"

    latest_resp = client.get("/api/runs/latest")
    assert latest_resp.status_code == 200
    assert latest_resp.json()["run_id"] == result["run_id"]


def test_calibration_dial_recomputes_without_rerunning_pipeline(isolated_app_state):
    client.post("/api/run", json={"seed": 42, "main_n": 120, "stress_n": 0, "threshold": 0.90, "provider": "mock"})

    loose = client.get("/api/calibration", params={"threshold": 0.3})
    strict = client.get("/api/calibration", params={"threshold": 0.999})
    assert loose.status_code == 200 and strict.status_code == 200
    loose_auto = {c["category"] for c in loose.json()["categories"] if c["decision"] == "auto_resolve"}
    strict_auto = {c["category"] for c in strict.json()["categories"] if c["decision"] == "auto_resolve"}
    assert strict_auto <= loose_auto


def test_calibration_dial_still_gates_mock_decisions_over_http(isolated_app_state):
    """The provider-aware fix (calibrator.py) exercised end-to-end over the real API: even at a
    permissive threshold, mock-mode batch runs must never show auto_resolve for a narrator
    category, since mock decisions carry provider="mock" and never count toward the gate."""
    client.post("/api/run", json={"seed": 42, "main_n": 120, "stress_n": 0, "threshold": 0.90, "provider": "mock"})
    resp = client.get("/api/calibration", params={"threshold": 0.01})
    assert resp.status_code == 200
    for c in resp.json()["categories"]:
        assert c["decision"] == "escalate", f"{c['category']} auto-resolved from mock-only data even at threshold=0.01"
        assert c["n"] == 0
        assert c["mock_n"] > 0


def test_resolve_escalation_feeds_back_into_history(isolated_app_state):
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


def test_evaluate_endpoint_catches_a_hand_crafted_duplicate_refund(isolated_app_state):
    """The 'break it' live path (spec §6.10): a judge-submitted scenario, not a pre-generated
    batch. Hand-builds a transaction where a refund is on record once but deducted twice from
    settlement, and checks the API correctly flags it as duplicate_refund via the real
    check_batch_anomalies tool, not a canned answer."""
    scenario = {
        "orders": [
            {
                "order_id": "demo_order_1",
                "merchant_id": "merchant_demo",
                "amount": 500000,
                "currency": "INR",
                "created_at": "2026-01-01T10:00:00",
                "rail": "upi",
            }
        ],
        "payments": [
            {
                "payment_id": "demo_pay_1",
                "order_id": "demo_order_1",
                "status": "captured",
                "captured": True,
                "captured_amount": 500000,
                "fee_amount": 1500,
                "tax_amount": 270,
                "gateway": "HDFC",
                "captured_at": "2026-01-01T10:05:00",
            }
        ],
        "refunds": [
            {
                "refund_id": "demo_refund_1",
                "payment_id": "demo_pay_1",
                "amount": 100000,
                "status": "processed",
                "created_at": "2026-01-02T10:00:00",
                "refund_type": "partial",
            }
        ],
        "settlements": [
            {
                "settlement_id": "demo_stl_1",
                "payment_id": "demo_pay_1",
                "settled_amount": 500000 - 1500 - 270 - 2 * 100000,
                "settlement_batch_id": "demo_batch_1",
                "utr": "123456789012",
                "rail": "upi",
                "settled_at": "2026-01-02T11:00:00",
                "sla_days": 1,
            }
        ],
        "ledger_entries": [
            {
                "ledger_id": "demo_ldg_1",
                "order_id": "demo_order_1",
                "expected_amount": 500000 - 1500 - 270 - 100000,
                "recorded_at": "2026-01-01T10:10:00",
            }
        ],
        "provider": "mock",
    }

    resp = client.post("/api/transactions/evaluate", json=scenario)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["category"] == "duplicate_refund"
    # zero accumulated calibration history -> nothing has earned auto-resolve yet -> must escalate,
    # same gate a batch-derived transaction would go through
    assert results[0]["resolution"] == "escalated"
    assert results[0]["tool_calls"], "should have a real tool-call trace, not a canned answer"


def test_evaluate_endpoint_returns_a_clean_422_on_a_broken_reference(isolated_app_state):
    """An external audit (2026-08-24) found this endpoint crashed with an opaque 500 -- no
    category, no reasoning, nothing like the honest fail-safe messages the narrator produces -- on
    a plausible judge-submitted scenario with a missing or mismatched reference (build_all_chains's
    unguarded payments_by_order/settlements_by_payment/ledger_by_order lookups). This is the exact
    live "break it" pitch-video endpoint, so a judge's own typo or incomplete edit is precisely the
    input it should be hardened against. Reproduces the simplest orphaned-record case: an order
    with a payment and settlement but no ledger entry at all."""
    scenario = {
        "orders": [
            {
                "order_id": "demo_order_orphan",
                "merchant_id": "merchant_demo",
                "amount": 500000,
                "currency": "INR",
                "created_at": "2026-01-01T10:00:00",
                "rail": "upi",
            }
        ],
        "payments": [
            {
                "payment_id": "demo_pay_orphan",
                "order_id": "demo_order_orphan",
                "status": "captured",
                "captured": True,
                "captured_amount": 500000,
                "fee_amount": 1500,
                "tax_amount": 270,
                "gateway": "HDFC",
                "captured_at": "2026-01-01T10:05:00",
            }
        ],
        "refunds": [],
        "settlements": [
            {
                "settlement_id": "demo_stl_orphan",
                "payment_id": "demo_pay_orphan",
                "settled_amount": 500000 - 1500 - 270,
                "settlement_batch_id": "demo_batch_orphan",
                "utr": "123456789013",
                "rail": "upi",
                "settled_at": "2026-01-02T11:00:00",
                "sla_days": 1,
            }
        ],
        "ledger_entries": [],  # deliberately missing -- ledger_by_order[order.order_id] should not crash the endpoint
        "provider": "mock",
    }

    resp = client.post("/api/transactions/evaluate", json=scenario)
    assert resp.status_code == 422, f"expected a clean 422, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert "demo_order_orphan" in detail, "the error should name the specific broken reference, not just say something failed"


def test_evaluate_endpoint_returns_a_clean_422_on_an_unexpected_processing_error(isolated_app_state):
    """Same principle as narrate()'s own orchestration-level backstop (round 8): a specific
    exception type is handled with a good message, but this endpoint must never crash on a
    genuinely unforeseen failure either. Mocks run_matching_engine itself to raise a plain
    RuntimeError with no special meaning, proving the broader backstop isn't tied to the one
    KeyError shape already found."""
    scenario = {
        "orders": [
            {
                "order_id": "demo_order_1",
                "merchant_id": "merchant_demo",
                "amount": 500000,
                "currency": "INR",
                "created_at": "2026-01-01T10:00:00",
                "rail": "upi",
            }
        ],
        "payments": [
            {
                "payment_id": "demo_pay_1",
                "order_id": "demo_order_1",
                "status": "captured",
                "captured": True,
                "captured_amount": 500000,
                "fee_amount": 1500,
                "tax_amount": 270,
                "gateway": "HDFC",
                "captured_at": "2026-01-01T10:05:00",
            }
        ],
        "refunds": [],
        "settlements": [
            {
                "settlement_id": "demo_stl_1",
                "payment_id": "demo_pay_1",
                "settled_amount": 500000 - 1500 - 270,
                "settlement_batch_id": "demo_batch_1",
                "utr": "123456789014",
                "rail": "upi",
                "settled_at": "2026-01-02T11:00:00",
                "sla_days": 1,
            }
        ],
        "ledger_entries": [
            {
                "ledger_id": "demo_ldg_1",
                "order_id": "demo_order_1",
                "expected_amount": 500000 - 1500 - 270,
                "recorded_at": "2026-01-01T10:10:00",
            }
        ],
        "provider": "mock",
    }

    with patch("app.main.run_matching_engine") as mock_engine:
        mock_engine.side_effect = RuntimeError("a totally unforeseen failure mode")
        resp = client.post("/api/transactions/evaluate", json=scenario)

    assert resp.status_code == 422, f"expected a clean 422, got {resp.status_code}: {resp.text}"
    assert "unforeseen failure mode" in resp.json()["detail"]


def test_audit_endpoint_returns_entries_for_the_latest_run(isolated_app_state):
    run_resp = client.post("/api/run", json={"seed": 3, "main_n": 60, "stress_n": 0, "threshold": 0.90, "provider": "mock"})
    run_id = run_resp.json()["run_id"]

    audit_resp = client.get("/api/audit")
    assert audit_resp.status_code == 200
    entries = audit_resp.json()
    assert len(entries) == 60
    assert all(e["run_id"] == run_id for e in entries)
