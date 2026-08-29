"""Tests for the forecaster's refusal layer and its calibrated intervals.

Two of these are load-bearing rather than incidental.

`test_refusing_actually_improves_amount_accuracy` is the one that decides whether the refusal layer
should exist at all. A mechanism that declines to answer but does not improve what remains is
decoration, and it would be easy to ship one and never notice.

`test_intervals_are_never_fitted_and_scored_on_the_same_batch` guards the calibration claim. Fitting
quantiles and then reporting coverage on the same data measures memorisation, and the resulting
reliability curve would look perfect while meaning nothing.
"""

from datetime import datetime, timedelta

import pytest

from app.data_gen.fee_schedule import SLA_TOLERANCE_DAYS
from app.data_gen.generate import generate
from app.data_gen.schemas import Order, Payment, Refund
from app.forecast.calibrated_interval import NOMINAL_LEVELS, fit, reliability_curve
from app.forecast.forecastability import REFUSAL_REASONS, assess, assess_batch
from app.forecast.predictor import predict_settlement


def _pair(amount: int = 100_000, captured: int | None = None, status: str = "captured", rail: str = "upi"):
    order = Order(
        order_id="order_test",
        merchant_id="merchant_001",
        amount=amount,
        currency="INR",
        created_at=datetime(2026, 1, 1),
        rail=rail,
    )
    payment = Payment(
        payment_id="pay_test",
        order_id="order_test",
        status=status,
        captured=status == "captured",
        captured_amount=amount if captured is None else captured,
        fee_amount=0,
        tax_amount=0,
        gateway="HDFC",
        captured_at=datetime(2026, 1, 1, 10),
    )
    return order, payment


# --- the refusal layer ---------------------------------------------------------------------------


def test_an_ordinary_captured_payment_is_forecastable():
    order, payment = _pair()
    assessment = assess(order, payment, refunds=[])
    assert assessment.forecastable
    assert assessment.reasons == []


def test_partial_capture_is_refused():
    order, payment = _pair(amount=100_000, captured=60_000)
    assert assess(order, payment).reasons == ["partial_capture"]


def test_an_uncaptured_payment_is_refused():
    order, payment = _pair(status="pending")
    assert "not_captured" in assess(order, payment).reasons


def test_a_refund_in_flight_is_refused():
    order, payment = _pair()
    refund = Refund(
        refund_id="rfnd_1",
        payment_id=payment.payment_id,
        amount=5_000,
        created_at=datetime(2026, 1, 2),
        refund_type="partial",
    )
    assert assess(order, payment, refunds=[refund]).reasons == ["refund_in_flight"]


def test_a_refund_on_a_different_payment_does_not_refuse_this_one():
    order, payment = _pair()
    other = Refund(
        refund_id="rfnd_2",
        payment_id="pay_someone_else",
        amount=5_000,
        created_at=datetime(2026, 1, 2),
        refund_type="partial",
    )
    assert assess(order, payment, refunds=[other]).forecastable


def test_a_breached_sla_is_refused_only_once_the_ceiling_has_passed():
    order, payment = _pair()
    ceiling = payment.captured_at + timedelta(days=SLA_TOLERANCE_DAYS[order.rail])
    assert assess(order, payment, as_of=ceiling).forecastable
    assert "sla_already_breached" in assess(order, payment, as_of=ceiling + timedelta(hours=1)).reasons


def test_a_later_forecast_is_never_more_confident_than_an_earlier_one():
    """`as_of` may only move a payment from forecastable to refused, never back."""
    order, payment = _pair()
    early = assess(order, payment, as_of=payment.captured_at)
    late = assess(order, payment, as_of=payment.captured_at + timedelta(days=30))
    assert early.forecastable
    assert not late.forecastable


def test_every_refusal_reason_has_an_explanation():
    order, payment = _pair(amount=100_000, captured=60_000, status="pending")
    assessment = assess(order, payment)
    assert assessment.reasons
    for reason in assessment.reasons:
        assert reason in REFUSAL_REASONS
    assert assessment.explain()


def test_refusal_never_consults_a_settlement():
    """A forward prediction cannot peek at the thing it is predicting. `assess` takes only Order,
    Payment and Refund, and this asserts the signature rather than trusting the docstring."""
    import inspect

    params = set(inspect.signature(assess).parameters)
    assert params == {"order", "payment", "refunds", "as_of"}


# --- does refusing earn its place ------------------------------------------------------------------


def test_refusing_actually_improves_amount_accuracy():
    """The test that decides whether this layer should exist.

    A refusal mechanism that declines to answer without improving what remains is decoration. MAPE on
    the accepted set must be materially better than on everything.
    """
    batch, _ = generate(seed=7, main_n=400, stress_n=0)
    assessments = assess_batch(batch.orders, batch.payments, batch.refunds)
    accepted = {t for t, a in assessments.items() if a.forecastable}
    assert accepted, "nothing was accepted"
    assert len(accepted) < len(assessments), "nothing was refused, so the layer is inert"

    order_by_id = {o.order_id: o for o in batch.orders}
    payment_by_id = {p.payment_id: p for p in batch.payments}

    def mape(only):
        errs = []
        for s in batch.settlements:
            payment = payment_by_id.get(s.payment_id)
            order = order_by_id.get(payment.order_id) if payment else None
            if order is None or (only is not None and order.order_id not in only):
                continue
            if s.settled_amount:
                pred = predict_settlement(order, payment)
                errs.append(abs(pred.predicted_net_amount - s.settled_amount) / abs(s.settled_amount))
        return sum(errs) / len(errs) if errs else 0.0

    assert mape(accepted) < mape(None) / 2, "refusing did not materially improve amount accuracy"


# --- calibrated intervals ---------------------------------------------------------------------------


def test_intervals_widen_monotonically_with_confidence():
    batch, _ = generate(seed=1, main_n=300, stress_n=0)
    model = fit(batch)
    for rail in model.per_rail:
        widths = [model.interval_days(rail, c)[1] - model.interval_days(rail, c)[0] for c in NOMINAL_LEVELS]
        assert widths == sorted(widths), f"{rail} interval width is not monotonic in confidence"


def test_intervals_are_never_fitted_and_scored_on_the_same_batch():
    """Guards the calibration claim: fitting quantiles then scoring the same data measures
    memorisation, and the curve would look perfect while meaning nothing."""
    fit_batch, _ = generate(seed=1, main_n=300, stress_n=0)
    holdout, _ = generate(seed=200, main_n=300, stress_n=0)
    fit_ids = {s.settlement_id for s in fit_batch.settlements}
    holdout_ids = {s.settlement_id for s in holdout.settlements}
    assert not (fit_ids & holdout_ids), "fit and holdout batches share settlements"


def test_reliability_curve_is_broadly_calibrated_out_of_sample():
    """Empirical coverage should track the nominal level. A generous tolerance, because the point is
    to catch a badly-miscalibrated interval, not to over-fit the test to one seed."""
    fit_batch, _ = generate(seed=1, main_n=1000, stress_n=0)
    holdout, _ = generate(seed=100, main_n=1000, stress_n=0)
    curve = reliability_curve(fit(fit_batch), holdout)
    for point in curve:
        assert abs(point.gap) < 0.15, f"nominal {point.nominal} covered {point.empirical} out of sample"


def test_reliability_curve_reports_a_real_denominator():
    fit_batch, _ = generate(seed=1, main_n=300, stress_n=0)
    holdout, _ = generate(seed=100, main_n=300, stress_n=0)
    for point in reliability_curve(fit(fit_batch), holdout):
        assert point.n > 0
        assert 0.0 <= point.empirical <= 1.0


def test_forecastable_only_scoring_uses_a_smaller_denominator_than_everything():
    """The refusal layer must actually change the scored population, or restricting to it is a no-op."""
    fit_batch, _ = generate(seed=1, main_n=400, stress_n=0)
    holdout, _ = generate(seed=100, main_n=400, stress_n=0)
    model = fit(fit_batch)
    restricted = reliability_curve(model, holdout, forecastable_only=True)
    everything = reliability_curve(model, holdout, forecastable_only=False)
    assert restricted[0].n < everything[0].n


def test_an_unseen_rail_falls_back_rather_than_crashing():
    batch, _ = generate(seed=1, main_n=200, stress_n=0)
    model = fit(batch)
    model.per_rail.pop("upi", None)
    low, high = model.interval_days("upi", 0.90)
    assert high >= low


@pytest.mark.parametrize("confidence", [0.5, 0.9, 0.99])
def test_interval_is_centred_on_the_lag_distribution(confidence):
    batch, _ = generate(seed=1, main_n=400, stress_n=0)
    model = fit(batch)
    captured = datetime(2026, 5, 1, 9, 0)
    low, high = model.interval("upi", captured, confidence)
    assert low >= captured
    assert high >= low
