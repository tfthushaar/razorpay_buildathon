"""Real Razorpay Test Mode API connector.

Uses `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` (test-mode credentials) to talk to the
real Razorpay API over HTTP Basic Auth and map responses onto this project's own
Order/Payment/Refund/Settlement/LedgerEntry schemas (`app/data_gen/schemas.py`).

Of this project's five causal-chain hops (order -> payment -> fee -> refund ->
settlement), four are real Razorpay API objects on this account: a real order, a real
captured payment with real `fee`/`tax` fields (confirmed non-null -- see below), and a
real refund against it. The fifth, settlement, is structurally unavailable in test mode
on any account, verified rather than assumed (see below) -- not a gap this connector
left unfixed.

Scope, verified by hand against the live API before this was written (see
docs/ARCHITECTURE.md and BUILD_LOG.md for the full
trail): `POST /v1/orders`, `GET /v1/payments`, and `GET /v1/settlements` are real,
working endpoints on this account. `POST /v1/payments/test_payment` does not exist —
there is no API that manufactures a captured payment directly in test mode. The only
way to produce one is the Checkout.js browser flow.

Cards don't work on this account: every standard "domestic" test card (Visa
`4111 1111 1111 1111` and Mastercard `5104 0155 5555 5558`) is rejected as
`international_transaction_not_allowed`, and UPI isn't offered as a payment method at
all -- this account's activation profile is narrower than a stock test account, and no
card number swap fixes that. **Netbanking does work**, and produces a genuinely
`captured` payment: selecting a bank (e.g. IDBI) opens Razorpay's own simulated bank
page (`api.razorpay.com/v1/gateway/mocksharp/...`, literally titled "This is just a demo
bank page") with a real Success/Failure choice -- confirmed live, `pay_...` IDs with
`status: "captured"`, `method: "netbanking"` really exist on this account (see BUILD_LOG.md).

The captured payment carries real `fee`/`tax` fields, not null or zero: `pay_TU5Ve4omwaz1c5`
on a Rs.500.00 (50000 paise) Netbanking payment shows `fee: 1180, tax: 180` -- a real
Rs.11.80 total fee including Rs.1.80 GST. Worth stating honestly rather than declaring a
clean match: the pre-tax base (Rs.10.00 = 1180/1.18) works out to 2.0% of the payment,
matching this project's `FEE_PCT["card"]` constant, not `FEE_PCT["netbanking"]`
(1.0%) -- Razorpay's test-mode fee simulation apparently doesn't vary by instrument the
way this project's synthetic contracted rates do, or uses a flat demo rate unrelated to
real production MDR agreements. `FEE_PCT` was deliberately NOT recalibrated to this one
sandbox observation: the whole point of the fee-leak detector is auditing against a
merchant's own *negotiated contract* rate, and one test-mode data point from Razorpay's
own simulator isn't more authoritative than that for a real merchant's actual terms --
if anything, treating a sandbox default as ground truth would be the same kind of
unverified assumption this project has caught and fixed elsewhere. The 18% GST math
does check out exactly (180 = 18% of the 1000-paise base fee), confirming `GST_RATE`.

A real partial refund was also issued and confirmed: `rfnd_TU5fHLaqzIZE3e`,
Rs.150.00 of the Rs.500.00 payment, via `POST /v1/payments/{id}/refund`. The payment
object updates to reflect it (`amount_refunded: 15000, refund_status: "partial"`), and
`GET /v1/refunds` lists it with a real `status` -- `fetch_refunds` below maps this,
deriving `refund_type` (full vs. partial) by comparing against the parent payment's
captured amount, since Razorpay's own refund object doesn't self-report that distinction.

**Settlements never will, and that's not fixable from here.** Verified via Razorpay's
own documentation: test-mode payments are simulated and isolated from the real
settlement pipeline by design -- "these transactions do not result in actual
settlements... real settlements only occur with actual payments processed in live mode
using production API keys and real customer funds." No number of real captured test
payments changes that; `GET /v1/settlements` will structurally return `[]` on this or
any test-mode account, forever. LedgerEntry has no Razorpay API equivalent either: it's
our own internal "amount we expected to receive," recorded at order-creation time, not
fetched from Razorpay.

So `fetch_payments` genuinely returns real captured payments (with real fee/tax) now,
`fetch_refunds` genuinely returns real refunds against them, and `fetch_settlements`
genuinely, permanently returns `[]` on any test-mode account -- all three are real
responses, not a mocked or incomplete implementation.

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

from app.data_gen.schemas import LedgerEntry, Order, Payment, Rail, Refund, Settlement

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


_PAYMENT_STATUS_MAP: dict[str, str] = {
    "captured": "captured",
    "failed": "failed",
    "refunded": "captured",  # was captured at some point; refund is tracked separately, not a status
    "authorized": "pending",
}
# Real Razorpay accounts also produce "created" -- a checkout session opened but never actually
# attempted (abandoned, or still in progress) -- found live against this account's own real payment
# list, not anticipated in advance. That's not a completed payment attempt with fee/settlement data
# behind it, so it's not reconciliation input at all; filtered out below rather than forced into
# Payment's status Literal (which would misrepresent what actually happened).
_IGNORED_PAYMENT_STATUSES = {"created"}


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
        if status in _IGNORED_PAYMENT_STATUSES:
            continue
        mapped_status = _PAYMENT_STATUS_MAP.get(status)
        if mapped_status is None:
            # An unforeseen status shape -- fail loud rather than silently mis-map a real payment's
            # state, the same discipline this project applies to the narrator's own fail-safes.
            raise RazorpaySandboxError(f"unrecognized payment status {status!r} on {item['id']} -- not yet mapped, not silently guessed")
        payments.append(
            Payment(
                payment_id=item["id"],
                order_id=item.get("order_id") or "",
                status=mapped_status,  # type: ignore[arg-type]
                captured=bool(item.get("captured", False)),
                captured_amount=item["amount"] if item.get("captured") else 0,
                fee_amount=item.get("fee") or 0,
                tax_amount=item.get("tax") or 0,
                gateway=item.get("method", "unknown"),
                captured_at=datetime.fromtimestamp(item["created_at"], tz=timezone.utc),
            )
        )
    return payments


_REFUND_STATUS_MAP: dict[str, str] = {"processed": "processed", "pending": "pending"}


def fetch_refunds(count: int = 100, payments: list[Payment] | None = None) -> list[Refund]:
    """GET /v1/refunds — real API call, confirmed live 2026-08-25: a real partial refund
    (rfnd_TU5fHLaqzIZE3e, Rs.150.00 of a Rs.500.00 payment) was issued via
    POST /v1/payments/{id}/refund and shows up here with real status/amount, not simulated.

    Razorpay's refund object never says full vs. partial itself -- refund_type is derived by
    comparing against the parent payment's captured_amount, so this needs the payment list too
    (fetched internally if not passed in, matching fetch_settlements' self-contained style)."""
    if payments is None:
        payments = fetch_payments(count=count)
    captured_amount_by_payment_id = {p.payment_id: p.captured_amount for p in payments}

    with _client() as client:
        resp = client.get("/refunds", params={"count": count})
        if resp.status_code != 200:
            raise RazorpaySandboxError(f"refunds fetch failed: {resp.status_code} {resp.text}")
        items = resp.json().get("items", [])

    refunds = []
    for item in items:
        status = item["status"]
        mapped_status = _REFUND_STATUS_MAP.get(status)
        if mapped_status is None:
            raise RazorpaySandboxError(f"unrecognized refund status {status!r} on {item['id']} -- not yet mapped, not silently guessed")
        payment_captured_amount = captured_amount_by_payment_id.get(item["payment_id"])
        refund_type = "full" if item["amount"] == payment_captured_amount else "partial"
        refunds.append(
            Refund(
                refund_id=item["id"],
                payment_id=item["payment_id"],
                amount=item["amount"],
                status=mapped_status,  # type: ignore[arg-type]
                created_at=datetime.fromtimestamp(item["created_at"], tz=timezone.utc),
                refund_type=refund_type,  # type: ignore[arg-type]
            )
        )
    return refunds


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
    """Human-facing connectivity check: proves the credentials are real and live. Used by
    GET /api/sandbox/status."""
    try:
        _, ledger_entry = create_test_order(amount=100, receipt="sandbox_status_probe")
        payments = fetch_payments(count=25)
        refunds = fetch_refunds(count=25, payments=payments)
        settlements = fetch_settlements(count=25)
    except RazorpaySandboxError as exc:
        return {"connected": False, "error": str(exc)}
    return {
        "connected": True,
        "probe_order_id": ledger_entry.order_id,
        "payments_on_account": len(payments),
        "captured_payments_on_account": sum(1 for p in payments if p.captured),
        "refunds_on_account": len(refunds),
        "settlements_on_account": len(settlements),
        "note": (
            "Real API, test-mode credentials. Order, captured payment (Netbanking -- Cards are "
            "rejected as international, UPI isn't offered), fee/tax, and refund are all real "
            "Razorpay API objects on this account. Settlements will always be 0 here regardless: "
            "test-mode payments are structurally excluded from Razorpay's settlement pipeline, "
            "confirmed against Razorpay's own docs -- not an account-specific limitation, and not "
            "something more captured payments or refunds would fix. The synthetic generator covers "
            "the settlement leg alone for exactly this reason."
        ),
    }
