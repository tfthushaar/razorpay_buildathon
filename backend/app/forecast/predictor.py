"""Forward settlement prediction -- given a captured payment that hasn't settled yet, predict
when and how much it will net.

Deliberately NOT built on `CausalChain`/`build_all_chains()` (app/chain/builder.py): those do
strict, required dict lookups for Settlement and LedgerEntry and raise KeyError if either is
missing -- there is no partial-chain support, by design, since every other part of this project
assumes a transaction is already fully resolved. A genuinely forward-looking prediction has to work
from Order + Payment alone, before a Settlement exists at all.

Reuses the exact same fee/SLA constants the rest of this project already uses and has already
tested (`app/data_gen/fee_schedule.py`, `app/narrator/tools.py::check_sla_window`) rather than
inventing a second set of assumptions -- the predicted interval is the real SLA tolerance window,
not a fabricated symmetric one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from app.data_gen.fee_schedule import BASE_SLA_DAYS, SLA_TOLERANCE_DAYS, fee_and_tax
from app.data_gen.schemas import Order, Payment, Rail


class SettlementPrediction(BaseModel):
    transaction_id: str  # == order_id
    rail: Rail
    captured_amount: int
    predicted_fee: int
    predicted_tax: int
    predicted_net_amount: int
    captured_at: datetime
    predicted_date_low: datetime  # nominal SLA -- the earliest a settlement would be expected
    predicted_date_high: datetime  # tolerance ceiling -- the latest before it's genuinely late


def predict_settlement(order: Order, payment: Payment) -> SettlementPrediction:
    """Predicts net settlement amount and a genuine (low, high) date interval for a captured
    payment that hasn't settled yet. Uses only information that would actually be available
    before settlement: the captured amount and the rail's own fee schedule / SLA window --
    never a Settlement record, which by definition doesn't exist yet for this to be a real
    prediction rather than a lookup."""
    fee, tax = fee_and_tax(order.rail, payment.captured_amount)
    net = payment.captured_amount - fee - tax
    nominal_days = BASE_SLA_DAYS[order.rail]
    tolerance_days = SLA_TOLERANCE_DAYS[order.rail]
    return SettlementPrediction(
        transaction_id=order.order_id,
        rail=order.rail,
        captured_amount=payment.captured_amount,
        predicted_fee=fee,
        predicted_tax=tax,
        predicted_net_amount=net,
        captured_at=payment.captured_at,
        predicted_date_low=payment.captured_at + timedelta(days=nominal_days),
        predicted_date_high=payment.captured_at + timedelta(days=tolerance_days),
    )


def predict_pending_batch(orders: list[Order], payments: list[Payment]) -> list[SettlementPrediction]:
    """Predicts every payment in a batch of still-in-flight (captured, not yet settled)
    transactions. Order is matched to payment by order_id -- payments without a matching order
    (shouldn't happen in generated data, but a real integration could see it) are skipped rather
    than crashing the whole batch, the same fail-safe-not-fail-loud posture used elsewhere for
    genuinely optional data, distinct from the fail-loud posture used when a REQUIRED field is
    missing (e.g. the Razorpay connector's unrecognized payment status)."""
    orders_by_id = {o.order_id: o for o in orders}
    predictions = []
    for payment in payments:
        order = orders_by_id.get(payment.order_id)
        if order is None:
            continue
        predictions.append(predict_settlement(order, payment))
    return predictions
