"""Real Razorpay webhook signature verification and `settlement.processed` event parsing --
closes the gap LIMITATIONS.md names directly: `POST /api/transactions/evaluate` is a real, tested
integration point a webhook consumer would call, but nothing previously verified an actual incoming
Razorpay webhook's signature or parsed its real event shape.

Payload shape and signature scheme verified against Razorpay's own current docs before writing this
(razorpay.com/docs/webhooks/settlements/, razorpay.com/docs/webhooks/validate-test/), not guessed:
- Envelope: {"entity": "event", "account_id": ..., "event": "settlement.processed",
  "contains": ["settlement"], "payload": {"settlement": {"entity": {...}}}, "created_at": ...}
- Settlement entity: id, entity="settlement", amount, status, fees, tax, utr, created_at -- amount/
  fees/tax in the smallest currency unit (paise for INR), matching this project's own convention.
- Signature: HMAC-SHA256 of the RAW request body (never the parsed/re-serialized one -- Razorpay's
  docs explicitly warn re-serializing changes byte-for-byte content and breaks the signature) using
  the webhook secret, hex-encoded, sent as the X-Razorpay-Signature header.

What this module does NOT do, honestly: reconstruct a full causal chain from a settlement webhook
alone. A real settlement webhook only ever carries the settlement leg -- the order/payment/ledger
data it must reconcile against already lives in the merchant's own system, populated through a
separate integration (order creation, payment capture callbacks), not from this one event. Parsing
and verifying the real event is this module's job; wiring a parsed settlement into a full
TransactionScenario for /api/transactions/evaluate is the merchant integration's own responsibility,
the same as for any real webhook consumer -- not something a settlement-only payload can supply on
its own.
"""

from __future__ import annotations

import hashlib
import hmac

from pydantic import BaseModel


class WebhookSignatureError(Exception):
    """Raised when a webhook's signature doesn't match -- distinguished from a parse error so a
    caller can return the correct HTTP status (401, not 422) for each."""


def verify_razorpay_signature(raw_body: bytes, signature: str, webhook_secret: str) -> None:
    """Razorpay's own documented scheme: HMAC-SHA256 of the RAW request body (bytes, not a
    re-serialized dict -- re-serializing can reorder keys or change whitespace, producing a
    different signature for logically-identical content) using the webhook secret configured in
    the merchant dashboard. `hmac.compare_digest`, not `==` -- a naive equality check here is a
    real timing-attack surface on a security boundary, not a hypothetical one. Raises
    WebhookSignatureError rather than returning a bool, so a caller can't accidentally forget to
    check a return value on a security-critical path."""
    expected = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookSignatureError("X-Razorpay-Signature does not match the computed HMAC-SHA256 digest")


class ParsedSettlementEvent(BaseModel):
    event: str
    account_id: str | None
    settlement_id: str
    amount: int
    fees: int
    tax: int
    utr: str
    status: str
    created_at: int


class WebhookParseError(Exception):
    """Raised when the payload doesn't match the expected settlement.processed envelope -- a
    malformed or unexpected event shape is a real, disclosed failure mode for any webhook receiver,
    not something to silently ignore or crash on with an unhandled KeyError."""


def parse_settlement_processed_event(payload: dict) -> ParsedSettlementEvent:
    """Parses the exact envelope Razorpay's own docs specify for settlement.processed. Raises
    WebhookParseError with a specific, actionable message on any missing/malformed field, rather
    than letting a bare KeyError/TypeError propagate -- the same discipline this project's other
    external-input boundary (POST /api/transactions/evaluate) already applies."""
    if payload.get("event") != "settlement.processed":
        raise WebhookParseError(f"expected event=settlement.processed, got {payload.get('event')!r}")
    try:
        entity = payload["payload"]["settlement"]["entity"]
        return ParsedSettlementEvent(
            event=payload["event"],
            account_id=payload.get("account_id"),
            settlement_id=entity["id"],
            amount=entity["amount"],
            fees=entity["fees"],
            tax=entity["tax"],
            utr=entity["utr"],
            status=entity["status"],
            created_at=entity["created_at"],
        )
    except KeyError as e:
        raise WebhookParseError(f"malformed settlement.processed payload: missing {e}")
