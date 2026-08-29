"""Which forecasts this system refuses to make, and why.

The reconciler was rebuilt around a single idea: the deterministic layer takes what it can, and what
it cannot close is reported honestly rather than guessed at. The forecaster never had that. It
predicted every pending payment with identical confidence, including ones where its own arithmetic
does not apply.

`predict_settlement` computes net as `captured - fee(rail, captured) - tax`, and the date window as
the rail's SLA tolerance. Both are exact when the transaction is ordinary and both are simply wrong
when it is not:

    partial_capture       captured != ordered, so the fee base the schedule assumes is not the
                          amount that was actually charged
    refund_in_flight      a refund exists before settlement, so net is not captured - fee - tax
    not_captured          nothing has been captured, so there is no basis for either number
    non_positive_net      fee and tax exceed the capture; the arithmetic produced nonsense
    sla_already_breached  the tolerance ceiling is already in the past as of the forecast date, so
                          the date window is known to be wrong before it is even issued

Every one of these is decidable from Order, Payment and Refund alone. None of them consults a
Settlement, which by definition does not exist yet for a forward prediction. That is the same
no-peeking discipline the resolver holds to, and it is what makes a refusal honest rather than
hindsight.

A refusal mechanism is worthless unless refusing improves what remains, so
`scripts/generate_forecast_evidence.py` measures accuracy on the forecast set AND on the refused set
separately. If they are not different, this module is decoration and the evidence says so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.data_gen.fee_schedule import SLA_TOLERANCE_DAYS, fee_and_tax
from app.data_gen.schemas import Order, Payment, Refund

ForecastStatus = Literal["FORECASTABLE", "REFUSED"]

REFUSAL_REASONS: dict[str, str] = {
    "partial_capture": "captured amount differs from the order amount, so the contracted fee base does not apply",
    "refund_in_flight": "a refund exists before settlement, so net is not captured minus fee and tax",
    "not_captured": "the payment is not captured, so there is no basis for an amount or a date",
    "non_positive_net": "fee and tax exceed the captured amount, so the predicted net is not a real figure",
    "sla_already_breached": "the SLA tolerance ceiling is already past, so the date window is known wrong before issue",
}


class ForecastAssessment(BaseModel):
    transaction_id: str
    status: ForecastStatus
    reasons: list[str] = []

    @property
    def forecastable(self) -> bool:
        return self.status == "FORECASTABLE"

    def explain(self) -> str:
        if self.forecastable:
            return "the contracted fee schedule and SLA window both apply cleanly"
        return "; ".join(REFUSAL_REASONS[r] for r in self.reasons)


def assess(
    order: Order,
    payment: Payment,
    refunds: list[Refund] | None = None,
    as_of: datetime | None = None,
) -> ForecastAssessment:
    """Decide whether the schedule genuinely applies to this payment.

    `as_of` is the moment the forecast is being issued. It only ever moves a transaction from
    forecastable to refused, never the other way, so a later forecast is never more confident than an
    earlier one about the same payment.
    """
    reasons: list[str] = []

    if not payment.captured or payment.status != "captured":
        reasons.append("not_captured")

    if payment.captured_amount != order.amount:
        reasons.append("partial_capture")

    if any(r.payment_id == payment.payment_id for r in (refunds or [])):
        reasons.append("refund_in_flight")

    fee, tax = fee_and_tax(order.rail, payment.captured_amount)
    if payment.captured_amount - fee - tax <= 0:
        reasons.append("non_positive_net")

    if as_of is not None:
        from datetime import timedelta

        ceiling = payment.captured_at + timedelta(days=SLA_TOLERANCE_DAYS[order.rail])
        if as_of > ceiling:
            reasons.append("sla_already_breached")

    return ForecastAssessment(
        transaction_id=order.order_id,
        status="REFUSED" if reasons else "FORECASTABLE",
        reasons=reasons,
    )


def assess_batch(
    orders: list[Order],
    payments: list[Payment],
    refunds: list[Refund] | None = None,
    as_of: datetime | None = None,
) -> dict[str, ForecastAssessment]:
    by_id = {o.order_id: o for o in orders}
    out: dict[str, ForecastAssessment] = {}
    for payment in payments:
        order = by_id.get(payment.order_id)
        if order is None:
            continue
        out[order.order_id] = assess(order, payment, refunds, as_of)
    return out
