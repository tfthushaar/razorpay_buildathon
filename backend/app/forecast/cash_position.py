"""Working capital view and payroll/shortfall alerting -- both computed directly from the
forward settlement predictor's output (app/forecast/predictor.py), no new prediction logic here.

"Money in transit" is a real balance-sheet item: captured but not yet settled. This is the
vocabulary a finance team actually uses, not a reconciliation-tool abstraction.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.forecast.predictor import SettlementPrediction

AGE_BUCKETS = [(0, 1, "0-1 days"), (1, 3, "1-3 days"), (3, 7, "3-7 days"), (7, None, "7+ days")]


class AgedBucket(BaseModel):
    label: str
    amount: int
    count: int


class WorkingCapitalReport(BaseModel):
    as_of: datetime
    total_unsettled_net: int  # predicted net amount across all pending transactions
    total_unsettled_gross: int  # captured amount before fee/tax, for reference
    by_rail: dict[str, int]  # predicted net amount, keyed by rail
    at_sla_risk_amount: int  # predicted net amount already past its tolerance ceiling as of `as_of`
    aged_buckets: list[AgedBucket]  # by days-since-capture


def compute_working_capital(predictions: list[SettlementPrediction], as_of: datetime) -> WorkingCapitalReport:
    by_rail: dict[str, int] = {}
    at_sla_risk = 0
    bucket_amounts = {label: 0 for _, _, label in AGE_BUCKETS}
    bucket_counts = {label: 0 for _, _, label in AGE_BUCKETS}

    for p in predictions:
        by_rail[p.rail] = by_rail.get(p.rail, 0) + p.predicted_net_amount
        if as_of > p.predicted_date_high:
            at_sla_risk += p.predicted_net_amount

        age_days = (as_of - p.captured_at).days
        for low, high, label in AGE_BUCKETS:
            if age_days >= low and (high is None or age_days < high):
                bucket_amounts[label] += p.predicted_net_amount
                bucket_counts[label] += 1
                break

    return WorkingCapitalReport(
        as_of=as_of,
        total_unsettled_net=sum(p.predicted_net_amount for p in predictions),
        total_unsettled_gross=sum(p.captured_amount for p in predictions),
        by_rail=by_rail,
        at_sla_risk_amount=at_sla_risk,
        aged_buckets=[AgedBucket(label=label, amount=bucket_amounts[label], count=bucket_counts[label]) for _, _, label in AGE_BUCKETS],
    )


class PayrollCoverageResult(BaseModel):
    outflow_amount: int
    outflow_date: date
    predicted_available_amount: int
    clears: bool
    shortfall_amount: int


def check_payroll_coverage(predictions: list[SettlementPrediction], outflow_amount: int, outflow_date: date) -> PayrollCoverageResult:
    """Conservative by construction: only counts a prediction toward the outflow if its *late*
    end of the interval (predicted_date_high) still lands on or before the outflow date -- money
    that might arrive by then isn't counted as money that will."""
    available = sum(p.predicted_net_amount for p in predictions if p.predicted_date_high.date() <= outflow_date)
    shortfall = max(0, outflow_amount - available)
    return PayrollCoverageResult(
        outflow_amount=outflow_amount,
        outflow_date=outflow_date,
        predicted_available_amount=available,
        clears=shortfall == 0,
        shortfall_amount=shortfall,
    )
