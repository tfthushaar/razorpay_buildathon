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
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.data_gen.fee_schedule import BASE_SLA_DAYS, SLA_TOLERANCE_DAYS, fee_and_tax
from app.data_gen.schemas import Order, Payment, Rail

if TYPE_CHECKING:  # import only for the annotation; calibrated_interval imports nothing from here
    from app.forecast.calibrated_interval import CalibratedIntervalModel


class SettlementPrediction(BaseModel):
    transaction_id: str  # == order_id
    rail: Rail
    captured_amount: int
    predicted_fee: int
    predicted_tax: int
    predicted_net_amount: int
    captured_at: datetime
    predicted_date_low: datetime
    predicted_date_high: datetime
    interval_source: str = "sla_window"  # "sla_window" | "calibrated"
    interval_confidence: float | None = None  # only set when interval_source == "calibrated"


def predict_settlement(
    order: Order,
    payment: Payment,
    interval_model: "CalibratedIntervalModel | None" = None,
    confidence: float = 0.90,
) -> SettlementPrediction:
    """Predicts net settlement amount and a (low, high) date interval for a captured payment that
    hasn't settled yet. Uses only information available before settlement: the captured amount and
    the rail's own fee schedule -- never a Settlement record, which by definition doesn't exist yet
    for this to be a prediction rather than a lookup.

    THE DATE INTERVAL COMES FROM ONE OF TWO PLACES, and they mean different things.

    Without `interval_model` the interval is the rail's SLA tolerance window: nominal SLA to
    tolerance ceiling. That is a policy boundary, not a prediction. It carries no confidence level,
    and asking what fraction of settlements land inside it gives the hit rate of a fixed window --
    a real number, but not the number a "90% interval" claims. It is the default because it needs
    no history, so a merchant with no settled batch yet still gets a window.

    With `interval_model` the interval is the empirical quantile of that rail's own observed
    settlement lag at the requested confidence, fitted on the merchant's history. That one does
    claim a confidence level, and app/forecast/calibrated_interval.py::reliability_curve measures
    out-of-sample whether it earns it.

    The default is None so every backtest and evidence file produced before the calibrated model
    existed still describes what the code does.
    """
    fee, tax = fee_and_tax(order.rail, payment.captured_amount)
    net = payment.captured_amount - fee - tax
    if interval_model is not None:
        low, high = interval_model.interval(order.rail, payment.captured_at, confidence)
        source, stated = "calibrated", confidence
    else:
        low = payment.captured_at + timedelta(days=BASE_SLA_DAYS[order.rail])
        high = payment.captured_at + timedelta(days=SLA_TOLERANCE_DAYS[order.rail])
        source, stated = "sla_window", None
    return SettlementPrediction(
        transaction_id=order.order_id,
        rail=order.rail,
        captured_amount=payment.captured_amount,
        predicted_fee=fee,
        predicted_tax=tax,
        predicted_net_amount=net,
        captured_at=payment.captured_at,
        predicted_date_low=low,
        predicted_date_high=high,
        interval_source=source,
        interval_confidence=stated,
    )


def predict_pending_batch(
    orders: list[Order],
    payments: list[Payment],
    interval_model: "CalibratedIntervalModel | None" = None,
    confidence: float = 0.90,
) -> list[SettlementPrediction]:
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
        predictions.append(predict_settlement(order, payment, interval_model, confidence))
    return predictions
