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


def test_concurrent_resolve_of_the_same_escalation_only_counts_once(isolated_app_state):
    """The sequential double-resolve test above only proves the *second, later* attempt 404s --
    it doesn't touch the actual bug: the endpoint used to check-then-delete as two separate steps
    (a .get(), then a del at the bottom) with no lock between them. Two genuinely concurrent
    resolves of the SAME escalation both passed the .get() check before either reached the del,
    both wrote a human-confirmed entry into calibration_history (silently double-counting one real
    data point as two independent observations), and the second del then crashed with an uncaught
    KeyError. Reproduced live before fixing this. Fires 5 concurrent resolves at the same
    transaction_id and requires exactly one to succeed, the rest to 404 cleanly (not crash), and
    exactly one calibration_history entry to result -- not zero, not five."""
    test_calibration_history, _ = isolated_app_state
    from concurrent.futures import ThreadPoolExecutor

    run_resp = client.post("/api/run", json={"seed": 7, "main_n": 150, "stress_n": 0, "threshold": 0.90, "provider": "mock"})
    result = run_resp.json()
    assert result["escalations"], "seed=7/n=150 should produce at least one escalation to resolve"
    txn_id = result["escalations"][0]["transaction_id"]

    # the batch run itself already recorded one scored_decisions row for this transaction (mock
    # narration is stored too, just tagged provider="mock" so it never counts toward the gate) --
    # so the meaningful check is the DELTA a resolve adds, not absolute presence/absence.
    before_count = len([d for d in test_calibration_history.all_decisions() if d.transaction_id == txn_id])

    def resolve_once(_):
        return client.post("/api/escalations/resolve", json={"transaction_id": txn_id})

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(resolve_once, range(5)))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 404, 404, 404, 404], f"expected exactly one winner and four clean 404s, got: {statuses}"

    after_count = len([d for d in test_calibration_history.all_decisions() if d.transaction_id == txn_id])
    assert after_count - before_count == 1, f"expected exactly one new calibration_history entry from the resolve, got {after_count - before_count}"


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


def test_run_endpoint_rejects_an_unknown_provider(isolated_app_state):
    """An external audit (2026-08-24, round 10) found this endpoint -- the system's primary,
    default, most-used one, not a secondary demo path -- crashed with a bare HTTP 500 on an
    invalid `provider` string. README.md itself documents `provider` as a normal per-request
    field, so a typo needs no adversarial intent. Root cause was narrate()'s own validity check
    sitting outside its try block, upstream of the round-8 backstop. Fixed at both layers: this
    test covers the request-validation layer (Literal on RunRequest.provider), which should catch
    the mistake immediately with one clear message rather than silently running a whole batch
    where every narrated transaction fails safe individually."""
    resp = client.post("/api/run", json={"seed": 1, "main_n": 10, "stress_n": 0, "provider": "gpt4-turbo-not-a-real-provider"})
    assert resp.status_code == 422, f"expected a clean 422, got {resp.status_code}: {resp.text}"


def test_run_endpoint_rejects_an_out_of_range_threshold(isolated_app_state):
    """Round 10 found a negative threshold flipped the calibration report's own gate to
    "auto_resolve" for categories that had never earned it -- nothing previously checked this was
    a real probability in [0.0, 1.0]."""
    resp = client.post("/api/run", json={"seed": 1, "main_n": 10, "stress_n": 0, "threshold": -0.5, "provider": "mock"})
    assert resp.status_code == 422, f"expected a clean 422, got {resp.status_code}: {resp.text}"


def test_calibration_endpoint_rejects_an_out_of_range_threshold(isolated_app_state):
    resp = client.get("/api/calibration", params={"threshold": 1.5})
    assert resp.status_code == 422, f"expected a clean 422, got {resp.status_code}: {resp.text}"


def test_evaluate_endpoint_rejects_duplicate_order_ids(isolated_app_state):
    """build_all_chains (chain/builder.py) keys three internal dicts by order_id/payment_id -- a
    duplicate silently overwrites the earlier record with no error. Round 10 reproduced this live:
    two orders sharing an order_id returned 1 result instead of 2, with no indication one was
    dropped. Submits the same order_id twice (with otherwise-valid, distinct payment/settlement/
    ledger records) and checks the API catches it before ever reaching build_all_chains, rather
    than silently losing one transaction's worth of data."""
    order = {
        "order_id": "demo_order_dup",
        "merchant_id": "merchant_demo",
        "amount": 500000,
        "currency": "INR",
        "created_at": "2026-01-01T10:00:00",
        "rail": "upi",
    }
    scenario = {
        "orders": [order, dict(order)],
        "payments": [
            {
                "payment_id": "demo_pay_dup_1",
                "order_id": "demo_order_dup",
                "status": "captured",
                "captured": True,
                "captured_amount": 500000,
                "fee_amount": 1500,
                "tax_amount": 270,
                "gateway": "HDFC",
                "captured_at": "2026-01-01T10:05:00",
            },
            {
                "payment_id": "demo_pay_dup_2",
                "order_id": "demo_order_dup",
                "status": "captured",
                "captured": True,
                "captured_amount": 500000,
                "fee_amount": 1500,
                "tax_amount": 270,
                "gateway": "HDFC",
                "captured_at": "2026-01-01T10:05:00",
            },
        ],
        "refunds": [],
        "settlements": [
            {
                "settlement_id": "demo_stl_dup_1",
                "payment_id": "demo_pay_dup_1",
                "settled_amount": 500000 - 1500 - 270,
                "settlement_batch_id": "demo_batch_dup",
                "utr": "123456789015",
                "rail": "upi",
                "settled_at": "2026-01-02T11:00:00",
                "sla_days": 1,
            },
            {
                "settlement_id": "demo_stl_dup_2",
                "payment_id": "demo_pay_dup_2",
                "settled_amount": 500000 - 1500 - 270,
                "settlement_batch_id": "demo_batch_dup",
                "utr": "123456789016",
                "rail": "upi",
                "settled_at": "2026-01-02T11:00:00",
                "sla_days": 1,
            },
        ],
        "ledger_entries": [
            {
                "ledger_id": "demo_ldg_dup_1",
                "order_id": "demo_order_dup",
                "expected_amount": 500000 - 1500 - 270,
                "recorded_at": "2026-01-01T10:10:00",
            }
        ],
        "provider": "mock",
    }

    resp = client.post("/api/transactions/evaluate", json=scenario)
    assert resp.status_code == 422, f"expected a clean 422, got {resp.status_code}: {resp.text}"
    # a pydantic model_validator's ValueError surfaces as FastAPI's standard validation-error shape
    # -- detail is a list of error objects, not a plain string like the manually-raised
    # HTTPExceptions elsewhere in this file.
    detail_text = str(resp.json()["detail"])
    assert "order_id" in detail_text
    assert "demo_order_dup" in detail_text


def test_concurrent_run_requests_do_not_crash_the_shared_connections(isolated_app_state):
    """Round 10 fired concurrent /api/run requests at a live server and got 7 of 8 HTTP 500s from
    the shared SQLite connections (sqlite3.InterfaceError / SystemError) -- check_same_thread=False
    only disables Python's thread-affinity check, it does not make a single connection safe for
    genuinely concurrent use, and FastAPI's sync-endpoint threadpool really does run requests in
    parallel rather than serializing them the way an earlier code comment assumed. This fires 8
    concurrent requests against the real TestClient (which dispatches through the same
    run_in_threadpool machinery a real server does) and requires every one to succeed."""
    from concurrent.futures import ThreadPoolExecutor

    def run_once(i: int):
        return client.post("/api/run", json={"seed": i, "main_n": 20, "stress_n": 0, "threshold": 0.90, "provider": "mock"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(run_once, range(8)))

    statuses = [r.status_code for r in responses]
    assert all(s == 200 for s in statuses), f"expected all 8 concurrent runs to succeed, got statuses: {statuses}"
