"""Date intervals whose stated confidence is fitted and then verified, rather than assumed.

The existing predictor emits one interval per transaction: the rail's SLA tolerance window. The
backtest then reports how often the real settlement date landed inside it, and this project has been
quoting that number (90.8% at n=120) as though it were a confidence level. It is not. The window is
not parameterised by confidence at all, so there is no nominal level to check the empirical figure
against. "90.8% coverage" is an observation about a fixed rule window, not evidence that a stated
confidence is honest.

That is exactly the asserted-versus-earned distinction the rest of this project is built on, left
unexamined in its own forecaster. This module closes it.

    fit()      learns, per rail, the empirical quantiles of settlement lag (settled_at minus
               captured_at) from a calibration batch
    interval() issues a window at a REQUESTED confidence, from those quantiles
    reliability curve  measured out-of-sample on a different batch: at every nominal level from
               50% to 99%, what fraction of real settlements actually landed inside

A forecaster whose stated 90% interval really contains 60% of outcomes is the same failure as a
category that auto-resolves without having earned it. The reliability curve is the forecasting
analogue of the Wilson lower bound: it is what makes the number a claim rather than a decoration.

Fitting and verification always run on separate batches. Fitting a quantile and then reporting
coverage on the same data measures memorisation.

WHAT THIS IS, IN THE LITERATURE'S TERMS. Fitting quantiles on one batch and applying them to another
is split conformal prediction. Naming it matters, because the theory then says what to expect: split
conformal gives a distribution-free marginal coverage guarantee bounded BELOW by the nominal level,
so over-coverage is the predicted direction rather than a bug. The upper bound is nominal + 1/(n+1),
which at the n this project measures on is +0.005 points.

The reliability curve over-covers by up to 7.5 points, roughly 1,600 times that bound, so the finite
sample correction explains almost none of it. The cause is ties: settlement lag here takes only 35 to
53 distinct values per rail across hundreds of observations, and single values carry around 10% of the
mass, so moving a quantile a fraction of a percent steps across a whole block at once. Vovk's smoothed
conformal predictors fix exactly this by breaking ties with a uniform draw. That is deliberately not
done here: it would make the same payment yield a different window on each call, and a finance tool
that cannot reproduce its own answer trades a real property for a cosmetic one.

`lag_discreteness()` reports the tie structure so the claim above is measured rather than asserted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from app.data_gen.schemas import Rail, SyntheticBatch

# Nominal levels the reliability curve is measured at. Wide enough to show a curve rather than a
# point, and includes 0.5 because a badly-fitted interval is most obviously wrong at the middle.
NOMINAL_LEVELS: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99)


class LagQuantiles(BaseModel):
    """Empirical settlement-lag distribution for one rail, in days."""

    rail: str
    n: int
    sorted_lag_days: list[float]

    def quantile(self, q: float) -> float:
        if not self.sorted_lag_days:
            return 0.0
        if len(self.sorted_lag_days) == 1:
            return self.sorted_lag_days[0]
        pos = q * (len(self.sorted_lag_days) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(self.sorted_lag_days) - 1)
        frac = pos - lo
        return self.sorted_lag_days[lo] * (1 - frac) + self.sorted_lag_days[hi] * frac


class CalibratedIntervalModel(BaseModel):
    """Per-rail lag quantiles, plus a global fallback for a rail never seen while fitting."""

    per_rail: dict[str, LagQuantiles]
    fallback: LagQuantiles

    def interval_days(self, rail: Rail, confidence: float) -> tuple[float, float]:
        """The (low, high) lag in days containing `confidence` of observed settlements."""
        q = self.per_rail.get(rail) or self.fallback
        tail = (1.0 - confidence) / 2.0
        return q.quantile(tail), q.quantile(1.0 - tail)

    def interval(self, rail: Rail, captured_at: datetime, confidence: float) -> tuple[datetime, datetime]:
        low, high = self.interval_days(rail, confidence)
        return captured_at + timedelta(days=low), captured_at + timedelta(days=high)


def fit(batch: SyntheticBatch) -> CalibratedIntervalModel:
    """Learn settlement-lag quantiles from a batch whose settlements have already happened.

    Uses only captured_at and settled_at, both of which a merchant genuinely has in their own
    history. Nothing here reads a ground-truth label.
    """
    captured_at = {p.payment_id: p.captured_at for p in batch.payments}
    by_rail: dict[str, list[float]] = {}
    everything: list[float] = []

    for settlement in batch.settlements:
        start = captured_at.get(settlement.payment_id)
        if start is None:
            continue
        lag = (settlement.settled_at - start).total_seconds() / 86400.0
        by_rail.setdefault(settlement.rail, []).append(lag)
        everything.append(lag)

    return CalibratedIntervalModel(
        per_rail={
            rail: LagQuantiles(rail=rail, n=len(lags), sorted_lag_days=sorted(lags))
            for rail, lags in by_rail.items()
        },
        fallback=LagQuantiles(rail="__fallback__", n=len(everything), sorted_lag_days=sorted(everything)),
    )


class ReliabilityPoint(BaseModel):
    nominal: float
    empirical: float
    n: int
    mean_width_days: float

    @property
    def gap(self) -> float:
        """Empirical minus nominal. Negative means the interval is overconfident."""
        return round(self.empirical - self.nominal, 4)


def lag_discreteness(model: CalibratedIntervalModel) -> dict:
    """How tied the fitted lag distribution is, per rail.

    The reliability curve over-covers, and this is the evidence for why. A continuous lag would let a
    quantile land anywhere; a lag taking a few dozen distinct values with ~10% of the mass on one of
    them cannot, so empirical quantiles snap and coverage overshoots the level asked for.
    """
    out: dict[str, dict] = {}
    for rail, q in sorted(model.per_rail.items()):
        lags = q.sorted_lag_days
        if not lags:
            continue
        counts: dict[float, int] = {}
        for value in lags:
            key = round(value, 6)
            counts[key] = counts.get(key, 0) + 1
        heaviest = max(counts.values())
        out[rail] = {
            "n": len(lags),
            "distinct_values": len(counts),
            "heaviest_tie_share": round(heaviest / len(lags), 4),
            "mean_observations_per_value": round(len(lags) / len(counts), 2),
        }
    return out


def reliability_curve(
    model: CalibratedIntervalModel,
    holdout: SyntheticBatch,
    levels: tuple[float, ...] = NOMINAL_LEVELS,
    forecastable_only: bool = True,
) -> list[ReliabilityPoint]:
    """Out-of-sample coverage at each nominal level.

    `forecastable_only` restricts scoring to transactions the refusal layer accepts, which is the
    honest denominator: an interval is not claiming to cover a payment the system declined to
    forecast. Both settings are reported by the evidence script, because the difference between them
    is the whole case for having a refusal layer.
    """
    from app.forecast.forecastability import assess_batch

    captured_at = {p.payment_id: p.captured_at for p in holdout.payments}
    order_by_id = {o.order_id: o for o in holdout.orders}
    payment_by_id = {p.payment_id: p for p in holdout.payments}

    accepted: set[str] | None = None
    if forecastable_only:
        assessments = assess_batch(holdout.orders, holdout.payments, holdout.refunds)
        accepted = {tid for tid, a in assessments.items() if a.forecastable}

    points: list[ReliabilityPoint] = []
    for level in levels:
        covered = scored = 0
        widths: list[float] = []
        for settlement in holdout.settlements:
            payment = payment_by_id.get(settlement.payment_id)
            if payment is None:
                continue
            order = order_by_id.get(payment.order_id)
            if order is None:
                continue
            if accepted is not None and order.order_id not in accepted:
                continue
            start = captured_at.get(settlement.payment_id)
            if start is None:
                continue
            low, high = model.interval(order.rail, start, level)
            widths.append((high - low).total_seconds() / 86400.0)
            scored += 1
            if low <= settlement.settled_at <= high:
                covered += 1
        points.append(
            ReliabilityPoint(
                nominal=level,
                empirical=round(covered / scored, 4) if scored else 0.0,
                n=scored,
                mean_width_days=round(sum(widths) / len(widths), 2) if widths else 0.0,
            )
        )
    return points
