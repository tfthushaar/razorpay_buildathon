"""Backtests the forward settlement predictor against a batch's own real ground truth.

Reuses an EXISTING full batch's real settlements -- no new data needed. For each transaction,
predicts using only pre-settlement information (order + payment + fee/SLA schedule, exactly what
`predict_settlement` uses), then reveals the real Settlement already in the batch and scores:

- MAPE (mean absolute percentage error) on predicted vs. actual net settlement amount.
- Interval coverage: did the actual settlement date fall inside the predicted (low, high) window.

Coverage is the honest metric here, not accuracy -- if the predictor claims a window and 90% of
real outcomes should land inside it, publish whatever fraction actually did, not a rounded-up
number. A batch's own adversarial/exception transactions (fee_deduction, netting_trap, duplicate
refunds, genuine timing drift -- see app/data_gen/generate.py) are exactly what should show up as
real MAPE/coverage error here: this predictor has no way to anticipate a refund that hasn't
happened yet at capture time, and it shouldn't pretend to.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from pydantic import BaseModel

from app.data_gen.schemas import SyntheticBatch
from app.forecast.predictor import predict_settlement


class ForwardCurvePoint(BaseModel):
    settlement_date: date
    predicted_amount: int
    actual_amount: int


class BacktestReport(BaseModel):
    n: int
    mape: float  # mean absolute percentage error on net settlement amount, 0.0 if n == 0
    interval_coverage: float  # fraction where the real settlement date fell within the predicted window
    forward_curve: list[ForwardCurvePoint]


def run_backtest(batch: SyntheticBatch) -> BacktestReport:
    payments_by_order_id = {p.order_id: p for p in batch.payments}
    settlements_by_payment_id = {s.payment_id: s for s in batch.settlements}

    ape_values: list[float] = []
    covered = 0
    scored = 0
    predicted_by_date: dict[date, int] = defaultdict(int)
    actual_by_date: dict[date, int] = defaultdict(int)

    for order in batch.orders:
        payment = payments_by_order_id.get(order.order_id)
        if payment is None or not payment.captured:
            continue
        settlement = settlements_by_payment_id.get(payment.payment_id)
        if settlement is None:
            continue

        prediction = predict_settlement(order, payment)
        actual_net = settlement.settled_amount
        scored += 1
        if actual_net != 0:
            ape_values.append(abs(prediction.predicted_net_amount - actual_net) / actual_net)
        if prediction.predicted_date_low <= settlement.settled_at <= prediction.predicted_date_high:
            covered += 1
        predicted_by_date[prediction.predicted_date_low.date()] += prediction.predicted_net_amount
        actual_by_date[settlement.settled_at.date()] += actual_net

    all_dates = sorted(set(predicted_by_date) | set(actual_by_date))
    forward_curve = [
        ForwardCurvePoint(settlement_date=d, predicted_amount=predicted_by_date.get(d, 0), actual_amount=actual_by_date.get(d, 0))
        for d in all_dates
    ]

    return BacktestReport(
        n=scored,
        mape=(sum(ape_values) / len(ape_values)) if ape_values else 0.0,
        interval_coverage=(covered / scored) if scored else 0.0,
        forward_curve=forward_curve,
    )
