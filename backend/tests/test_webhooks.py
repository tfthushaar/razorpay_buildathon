"""Tests for the real Razorpay webhook receiver (Phase 8): signature verification
(app/webhooks/razorpay.py) and the full HTTP endpoint (app/main.py::api_razorpay_webhook).
Payload shape and signature scheme verified against Razorpay's own current docs before this was
written -- see app/webhooks/razorpay.py's own module docstring."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.webhooks.razorpay import (
    ParsedSettlementEvent,
    WebhookParseError,
    WebhookSignatureError,
    parse_settlement_processed_event,
    verify_razorpay_signature,
)

client = TestClient(app)

REAL_SHAPE_PAYLOAD = {
    "entity": "event",
    "account_id": "acc_BFQ7uqOAsPmPwl",
    "event": "settlement.processed",
    "contains": ["settlement"],
    "payload": {
        "settlement": {
            "entity": {
                "id": "setl_00000000000001",
                "entity": "settlement",
                "amount": 3494979,
                "status": "processed",
                "fees": 118,
                "tax": 18,
                "utr": "1234567890",
                "created_at": 1596536994,
            }
        }
    },
    "created_at": 1596536994,
}


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ---- verify_razorpay_signature ----


def test_verify_razorpay_signature_accepts_a_correctly_signed_body():
    body = json.dumps(REAL_SHAPE_PAYLOAD).encode("utf-8")
    signature = _sign(body, "whsec_test123")
    verify_razorpay_signature(body, signature, "whsec_test123")  # must not raise


def test_verify_razorpay_signature_rejects_a_wrong_signature():
    body = json.dumps(REAL_SHAPE_PAYLOAD).encode("utf-8")
    with pytest.raises(WebhookSignatureError):
        verify_razorpay_signature(body, "0" * 64, "whsec_test123")


def test_verify_razorpay_signature_rejects_a_tampered_body():
    """The whole point of signing: a body that doesn't match what was signed must fail, even if
    the signature itself is well-formed and was valid for the ORIGINAL body."""
    body = json.dumps(REAL_SHAPE_PAYLOAD).encode("utf-8")
    signature = _sign(body, "whsec_test123")
    tampered = json.dumps({**REAL_SHAPE_PAYLOAD, "payload": {"settlement": {"entity": {**REAL_SHAPE_PAYLOAD["payload"]["settlement"]["entity"], "amount": 1}}}}).encode("utf-8")
    with pytest.raises(WebhookSignatureError):
        verify_razorpay_signature(tampered, signature, "whsec_test123")


def test_verify_razorpay_signature_rejects_the_wrong_secret():
    body = json.dumps(REAL_SHAPE_PAYLOAD).encode("utf-8")
    signature = _sign(body, "whsec_test123")
    with pytest.raises(WebhookSignatureError):
        verify_razorpay_signature(body, signature, "whsec_a_different_secret")


# ---- parse_settlement_processed_event ----


def test_parse_settlement_processed_event_reads_the_real_verified_shape():
    parsed = parse_settlement_processed_event(REAL_SHAPE_PAYLOAD)
    assert isinstance(parsed, ParsedSettlementEvent)
    assert parsed.settlement_id == "setl_00000000000001"
    assert parsed.amount == 3494979
    assert parsed.fees == 118
    assert parsed.tax == 18
    assert parsed.utr == "1234567890"
    assert parsed.status == "processed"
    assert parsed.account_id == "acc_BFQ7uqOAsPmPwl"


def test_parse_settlement_processed_event_rejects_the_wrong_event_type():
    wrong_event = {**REAL_SHAPE_PAYLOAD, "event": "payment.captured"}
    with pytest.raises(WebhookParseError):
        parse_settlement_processed_event(wrong_event)


def test_parse_settlement_processed_event_gives_a_specific_error_on_a_missing_field():
    broken = json.loads(json.dumps(REAL_SHAPE_PAYLOAD))
    del broken["payload"]["settlement"]["entity"]["utr"]
    with pytest.raises(WebhookParseError, match="utr"):
        parse_settlement_processed_event(broken)


def test_parse_settlement_processed_event_gives_a_specific_error_on_a_malformed_envelope():
    with pytest.raises(WebhookParseError):
        parse_settlement_processed_event({"event": "settlement.processed"})  # no payload at all


# ---- the real HTTP endpoint ----


def test_webhook_endpoint_rejects_when_secret_is_not_configured(monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    response = client.post("/api/webhooks/razorpay", json=REAL_SHAPE_PAYLOAD, headers={"X-Razorpay-Signature": "irrelevant"})
    assert response.status_code == 500


def test_webhook_endpoint_rejects_a_missing_signature_header(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test123")
    response = client.post("/api/webhooks/razorpay", json=REAL_SHAPE_PAYLOAD)
    assert response.status_code == 401


def test_webhook_endpoint_rejects_a_wrong_signature(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test123")
    response = client.post("/api/webhooks/razorpay", json=REAL_SHAPE_PAYLOAD, headers={"X-Razorpay-Signature": "0" * 64})
    assert response.status_code == 401


def test_webhook_endpoint_accepts_a_correctly_signed_real_shaped_payload(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test123")
    body = json.dumps(REAL_SHAPE_PAYLOAD).encode("utf-8")
    signature = _sign(body, "whsec_test123")
    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is True
    assert data["parsed"]["settlement_id"] == "setl_00000000000001"


def test_webhook_endpoint_rejects_a_malformed_payload_even_with_a_valid_signature(monkeypatch):
    """A well-signed request is still just a well-signed request -- it must not bypass payload
    validation. Signed by the sender, not by correctness."""
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test123")
    bad_payload = {"event": "settlement.processed"}  # missing the whole payload.settlement.entity
    body = json.dumps(bad_payload).encode("utf-8")
    signature = _sign(body, "whsec_test123")
    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
