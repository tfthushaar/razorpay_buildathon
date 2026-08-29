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

import statistics
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
    """Amount accuracy is reported five ways, because one number was misleading.

    `mape` alone was the headline until the error distribution was actually looked at: on a 2,000
    transaction batch, 83% of it came from five rows. Median APE is 0.000000. A settlement that
    lands near zero -- an unexplained shortfall -- puts a near-zero denominator under a full-size
    numerator, and one such row moves the mean more than the other 1,794 combined.

    So the mean is kept, since it is the standard measure and hiding it would be worse, and it is
    published beside the four numbers that say what it is made of. `exact_rate` is the one a finance
    team would actually ask for: the fraction predicted to the paise.
    """

    n: int
    exact_rate: float  # fraction predicted to the exact paise
    median_ape: float  # robust to the near-zero-denominator tail
    mape: float  # mean absolute percentage error; tail-dominated, read beside p95 and worst
    p95_ape: float
    worst_ape: float
    n_undefined_ape: int  # settlements at or below zero, where a percentage error has no meaning
    interval_coverage: float  # fraction where the real settlement date fell within the predicted window
    forward_curve: list[ForwardCurvePoint]


def ape_panel(ape_values: list[float], n_undefined: int) -> dict:
    """The five amount-accuracy numbers, in one place.

    `run_backtest` and scripts/generate_forecast_evidence.py both scored amount error, with
    different denominator rules, and reported different figures for the same batch. One definition
    now, used by both: a settlement at or below zero has no percentage error and is counted rather
    than divided by.
    """
    ordered = sorted(ape_values)
    if not ordered:
        return {"median_ape": 0.0, "mape": 0.0, "p95_ape": 0.0, "worst_ape": 0.0, "n_undefined_ape": n_undefined}
    return {
        "median_ape": statistics.median(ordered),
        "mape": sum(ordered) / len(ordered),
        "p95_ape": ordered[min(int(0.95 * len(ordered)), len(ordered) - 1)],
        "worst_ape": ordered[-1],
        "n_undefined_ape": n_undefined,
    }


def run_backtest(batch: SyntheticBatch, interval_model=None, confidence: float = 0.90) -> BacktestReport:
    payments_by_order_id = {p.order_id: p for p in batch.payments}
    settlements_by_payment_id = {s.payment_id: s for s in batch.settlements}

    ape_values: list[float] = []
    covered = 0
    scored = 0
    exact = 0
    undefined_ape = 0
    predicted_by_date: dict[date, int] = defaultdict(int)
    actual_by_date: dict[date, int] = defaultdict(int)

    for order in batch.orders:
        payment = payments_by_order_id.get(order.order_id)
        if payment is None or not payment.captured:
            continue
        settlement = settlements_by_payment_id.get(payment.payment_id)
        if settlement is None:
            continue

        prediction = predict_settlement(order, payment, interval_model, confidence)
        actual_net = settlement.settled_amount
        scored += 1
        if actual_net > 0:
            ape_values.append(abs(prediction.predicted_net_amount - actual_net) / actual_net)
        else:
            # A settlement at or below zero has no meaningful percentage error. The old guard was
            # `!= 0`, which let negative actuals through and divided by them: the term came out
            # NEGATIVE and pulled the mean of an ABSOLUTE percentage error down. Six rows per 2,000
            # batch hit this, all of them over-netted settlements, and each one made the error look
            # smaller than it was.
            undefined_ape += 1
        if abs(prediction.predicted_net_amount - actual_net) == 0:
            exact += 1
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
        exact_rate=(exact / scored) if scored else 0.0,
        **ape_panel(ape_values, undefined_ape),
        interval_coverage=(covered / scored) if scored else 0.0,
        forward_curve=forward_curve,
    )
