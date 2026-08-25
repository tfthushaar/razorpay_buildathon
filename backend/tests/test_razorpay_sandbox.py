"""Tests for the real Razorpay Test Mode connector (app/connectors/razorpay_sandbox.py).

These mock httpx.Client so the suite never makes a live network call -- the response
shapes mocked here were captured by hand against the real API (see BUILD_LOG.md), not
guessed from docs. A single manual run against the real account is what verified this
mapping is correct in the first place; the automated suite just guards against
regressions in that mapping.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.razorpay_sandbox import (
    RazorpaySandboxError,
    create_test_order,
    fetch_payments,
    fetch_settlements,
    sandbox_status,
)


def _mock_client(response_map):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    def _get(path, params=None):
        return response_map[("GET", path)]

    def _post(path, json=None):
        return response_map[("POST", path)]

    client.get.side_effect = _get
    client.post.side_effect = _post
    return client


def _resp(status_code, payload):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


def test_create_test_order_maps_a_real_order_response(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    order_body = {
        "id": "order_TESTABC123",
        "amount": 50000,
        "currency": "INR",
        "created_at": 1735000000,
        "notes": {},
    }
    client = _mock_client({("POST", "/orders"): _resp(200, order_body)})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        order, ledger_entry = create_test_order(amount=50000, receipt="r1")

    assert order.order_id == "order_TESTABC123"
    assert order.amount == 50000
    assert order.currency == "INR"
    assert order.created_at == datetime.fromtimestamp(1735000000, tz=timezone.utc)
    assert ledger_entry.order_id == "order_TESTABC123"
    assert ledger_entry.expected_amount == 50000


def test_create_test_order_handles_notes_as_an_empty_list(monkeypatch):
    """The real API returns notes as [] (not {}) when none are set on the order --
    found by running this connector live against the real account; body.get("notes", {})
    alone crashes with AttributeError on the real response shape."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    order_body = {
        "id": "order_TESTABC123",
        "amount": 100,
        "currency": "INR",
        "created_at": 1735000000,
        "notes": [],
    }
    client = _mock_client({("POST", "/orders"): _resp(200, order_body)})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        order, _ = create_test_order(amount=100, receipt="r1")

    assert order.merchant_id == "sandbox"


def test_create_test_order_raises_on_a_non_200(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    client = _mock_client({("POST", "/orders"): _resp(401, {"error": "bad auth"})})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        with pytest.raises(RazorpaySandboxError):
            create_test_order(amount=100, receipt="r1")


def test_missing_credentials_raises_before_any_network_call(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(RazorpaySandboxError):
        create_test_order(amount=100, receipt="r1")


def test_fetch_payments_maps_a_captured_payment(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    payments_body = {
        "items": [
            {
                "id": "pay_TESTXYZ",
                "order_id": "order_TESTABC123",
                "status": "captured",
                "captured": True,
                "amount": 50000,
                "fee": 1180,
                "tax": 180,
                "method": "upi",
                "created_at": 1735000100,
            }
        ]
    }
    client = _mock_client({("GET", "/payments"): _resp(200, payments_body)})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        payments = fetch_payments()

    assert len(payments) == 1
    p = payments[0]
    assert p.payment_id == "pay_TESTXYZ"
    assert p.status == "captured"
    assert p.captured is True
    assert p.captured_amount == 50000
    assert p.fee_amount == 1180
    assert p.tax_amount == 180


def test_fetch_payments_filters_out_created_status_not_a_real_attempt(monkeypatch):
    """Found live against the real account: a "created" payment is a checkout session opened but
    never actually attempted (abandoned, or in progress) -- not a completed attempt with fee/
    settlement data behind it, so it's not reconciliation input. Filtered out, not force-mapped
    into Payment.status (whose Literal doesn't include it, and shouldn't)."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    payments_body = {
        "items": [
            {"id": "pay_ABANDONED", "order_id": "order_X", "status": "created", "captured": False, "amount": 50000, "method": "netbanking", "created_at": 1735000100},
            {"id": "pay_REAL", "order_id": "order_Y", "status": "captured", "captured": True, "amount": 50000, "fee": 100, "tax": 18, "method": "netbanking", "created_at": 1735000200},
        ]
    }
    client = _mock_client({("GET", "/payments"): _resp(200, payments_body)})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        payments = fetch_payments()

    assert len(payments) == 1
    assert payments[0].payment_id == "pay_REAL"


def test_fetch_payments_raises_loudly_on_a_genuinely_unrecognized_status(monkeypatch):
    """An unmapped status should fail loud, not silently guess -- the same discipline the
    narrator's own fail-safes apply to a model's output."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    payments_body = {"items": [{"id": "pay_WEIRD", "order_id": "order_Z", "status": "some_new_status_razorpay_added", "captured": False, "amount": 100, "method": "card", "created_at": 1735000100}]}
    client = _mock_client({("GET", "/payments"): _resp(200, payments_body)})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        with pytest.raises(RazorpaySandboxError):
            fetch_payments()


def test_fetch_payments_empty_account_returns_empty_list_not_an_error(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    client = _mock_client({("GET", "/payments"): _resp(200, {"items": []})})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        assert fetch_payments() == []


def test_fetch_settlements_maps_the_real_response_shape(monkeypatch):
    """Real /v1/settlements items are {id, entity, amount, status, fees, tax, utr,
    created_at} -- no payment_id, no method. This mock deliberately does NOT include
    those two keys, unlike an earlier version of this test that had silently invented
    them; that gap is exactly what let a real correctness bug (fabricating a "upi" rail
    for every settlement regardless of truth) ship undetected. See razorpay_sandbox.py's
    module docstring and fetch_settlements' comment for the real shape and why
    payment_id/rail are explicit placeholders here, not guesses."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    settlements_body = {
        "items": [
            {
                "id": "setl_TESTDEF",
                "entity": "settlement",
                "amount": 48820,
                "status": "processed",
                "fees": 1180,
                "tax": 180,
                "utr": "UTR12345",
                "created_at": 1735003700,
            }
        ]
    }
    client = _mock_client({("GET", "/settlements"): _resp(200, settlements_body)})
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        settlements = fetch_settlements()

    assert len(settlements) == 1
    s = settlements[0]
    assert s.settlement_id == "setl_TESTDEF"
    assert s.settled_amount == 48820
    assert s.utr == "UTR12345"
    assert s.payment_id == ""
    assert s.rail == "upi"


def test_sandbox_status_reports_connected_with_real_counts(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake_secret")
    order_body = {"id": "order_PROBE1", "amount": 100, "currency": "INR", "created_at": 1735000000, "notes": {}}
    client = _mock_client(
        {
            ("POST", "/orders"): _resp(200, order_body),
            ("GET", "/payments"): _resp(200, {"items": []}),
            ("GET", "/settlements"): _resp(200, {"items": []}),
        }
    )
    with patch("app.connectors.razorpay_sandbox._client", return_value=client):
        status = sandbox_status()

    assert status["connected"] is True
    assert status["probe_order_id"] == "order_PROBE1"
    assert status["payments_on_account"] == 0
    assert status["settlements_on_account"] == 0


def test_sandbox_status_reports_disconnected_on_bad_credentials(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    status = sandbox_status()
    assert status["connected"] is False
    assert "error" in status
