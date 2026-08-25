"""Real Razorpay Test Mode API connector.

Uses `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` (test-mode credentials) to talk to the
real Razorpay API over HTTP Basic Auth and map responses onto this project's own
Order/Payment/Settlement/LedgerEntry schemas (`app/data_gen/schemas.py`).

Scope, verified by hand against the live API before this was written (see
docs/track04-settlement-reconciliation-copilot.md §12 and BUILD_LOG.md for the full
trail): `POST /v1/orders`, `GET /v1/payments`, and `GET /v1/settlements` are real,
working endpoints on this account. `POST /v1/payments/test_payment` does not exist —
there is no API that manufactures a captured payment directly in test mode. The only
way to produce one is the Checkout.js browser flow, which on this account rejects
every standard "domestic" test card (Visa `4111 1111 1111 1111` and Mastercard
`5104 0155 5555 5558`) as `international_transaction_not_allowed` and does not even
offer UPI as a payment method — this account's activation profile is narrower than
a stock test account, and no card number swap fixes that. LedgerEntry has no Razorpay
API equivalent at all: it is our own internal "amount we expected to receive," which a
real merchant would record at order-creation time, not fetch from Razorpay.

So `fetch_payments` / `fetch_settlements` against a fresh account genuinely return
empty lists — that's a real authenticated response, not a mocked one. This connector
proves the wiring is real; it does not claim to have captured a real payment.

`GET /v1/settlements`'s real response also has no per-settlement `payment_id` or
`method`/rail field at all (verified against Razorpay's own docs: it's
`{id, entity, amount, status, fees, tax, utr, created_at}`) — real payment-level
linkage needs the recon endpoint (`GET /v1/settlements/recon/combined?year=&month=&day=`),
not built here. `fetch_settlements` sets those two required-but-unavailable fields to
an explicit placeholder rather than guessing from a field that doesn't exist.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from app.data_gen.schemas import LedgerEntry, Order, Payment, Rail, Settlement

API_BASE = "https://api.razorpay.com/v1"


class RazorpaySandboxError(RuntimeError):
    pass


def _auth() -> tuple[str, str]:
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise RazorpaySandboxError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set in the environment")
    return key_id, key_secret


def _client() -> httpx.Client:
    return httpx.Client(base_url=API_BASE, auth=_auth(), timeout=15.0)


def create_test_order(amount: int, receipt: str, rail: Rail = "upi", currency: str = "INR") -> tuple[Order, LedgerEntry]:
    """Create a real order via POST /v1/orders and the internal ledger entry a merchant
    would record for it at the same moment (Razorpay has no API for that second part —
    it is booked locally, same as `_build_order_and_payment` does for synthetic data)."""
    with _client() as client:
        resp = client.post("/orders", json={"amount": amount, "currency": currency, "receipt": receipt})
        if resp.status_code != 200:
            raise RazorpaySandboxError(f"order creation failed: {resp.status_code} {resp.text}")
        body = resp.json()

    now = datetime.now(timezone.utc)
    notes = body.get("notes") or {}
    merchant_id = notes.get("merchant_id", "sandbox") if isinstance(notes, dict) else "sandbox"
    order = Order(
        order_id=body["id"],
        merchant_id=merchant_id,
        amount=body["amount"],
        currency=body["currency"],
        created_at=datetime.fromtimestamp(body["created_at"], tz=timezone.utc),
        rail=rail,
    )
    ledger_entry = LedgerEntry(
        ledger_id=f"ldg_sandbox_{order.order_id}",
        order_id=order.order_id,
        expected_amount=order.amount,
        recorded_at=now,
    )
    return order, ledger_entry


def fetch_payments(count: int = 100) -> list[Payment]:
    """GET /v1/payments — real API call. Returns [] on a fresh/unfunded test account;
    that emptiness is itself the real response, not a placeholder."""
    with _client() as client:
        resp = client.get("/payments", params={"count": count})
        if resp.status_code != 200:
            raise RazorpaySandboxError(f"payments fetch failed: {resp.status_code} {resp.text}")
        items = resp.json().get("items", [])

    payments = []
    for item in items:
        status = item["status"]
        payments.append(
            Payment(
                payment_id=item["id"],
                order_id=item.get("order_id") or "",
                status="captured" if status == "captured" else status,  # type: ignore[arg-type]
                captured=bool(item.get("captured", False)),
                captured_amount=item["amount"] if item.get("captured") else 0,
                fee_amount=item.get("fee") or 0,
                tax_amount=item.get("tax") or 0,
                gateway=item.get("method", "unknown"),
                captured_at=datetime.fromtimestamp(item["created_at"], tz=timezone.utc),
            )
        )
    return payments


def fetch_settlements(count: int = 100) -> list[Settlement]:
    """GET /v1/settlements — real API call. Returns [] until a real captured payment
    exists on this account and Razorpay has run a settlement cycle against it."""
    with _client() as client:
        resp = client.get("/settlements", params={"count": count})
        if resp.status_code != 200:
            raise RazorpaySandboxError(f"settlements fetch failed: {resp.status_code} {resp.text}")
        items = resp.json().get("items", [])

    # The real /v1/settlements response is {id, entity, amount, status, fees, tax, utr,
    # created_at} -- no payment_id, no per-settlement method/rail. Verified against
    # Razorpay's own docs, not assumed. Getting payment-level linkage (which payment, on
    # which rail, settled here) needs the recon endpoint instead
    # (GET /v1/settlements/recon/combined?year=&month=&day=), not built here. payment_id
    # and rail are required fields on our own Settlement schema, so they're set to an
    # explicit placeholder below rather than silently guessed from a field that doesn't exist.
    settlements = []
    for item in items:
        settlements.append(
            Settlement(
                settlement_id=item["id"],
                payment_id="",
                settled_amount=item["amount"],
                settlement_batch_id=item["id"],
                utr=item.get("utr", ""),
                rail="upi",
                settled_at=datetime.fromtimestamp(item["created_at"], tz=timezone.utc),
                sla_days=0,
            )
        )
    return settlements


def sandbox_status() -> dict:
    """Human-facing connectivity check: proves the credentials are real and live without
    requiring a captured payment to exist. Used by GET /api/sandbox/status."""
    try:
        _, ledger_entry = create_test_order(amount=100, receipt="sandbox_status_probe")
        payments = fetch_payments(count=1)
        settlements = fetch_settlements(count=1)
    except RazorpaySandboxError as exc:
        return {"connected": False, "error": str(exc)}
    return {
        "connected": True,
        "probe_order_id": ledger_entry.order_id,
        "payments_on_account": len(payments),
        "settlements_on_account": len(settlements),
        "note": (
            "Real API, test-mode credentials. This account's Checkout activation profile "
            "rejects standard domestic test cards as international and does not offer UPI, "
            "so no captured payment has been produced yet -- payments/settlements counts "
            "above are real, live responses, not placeholders."
        ),
    }
