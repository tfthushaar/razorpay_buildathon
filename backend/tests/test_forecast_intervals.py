"""The predictor's two interval sources, and the amount-error panel that replaced a single MAPE.

`predict_settlement` can return either the rail's SLA tolerance window or a calibrated empirical
interval. They mean different things -- one is a policy boundary that claims no confidence, the
other states one and can be checked against it -- so both the source and the claimed level travel
with the prediction rather than being inferred by the caller.
"""

import statistics

import pytest

from app.data_gen.generate import generate
from app.forecast.backtest import ape_panel, run_backtest
from app.forecast.calibrated_interval import fit
from app.forecast.forecastability import assess_batch
from app.forecast.predictor import predict_settlement


@pytest.fixture(scope="module")
def batch():
    b, _ = generate(seed=7, main_n=400, stress_n=0)
    return b


@pytest.fixture(scope="module")
def model():
    return fit(generate(seed=1, main_n=2000, stress_n=0)[0])


def _first_pair(batch):
    payments = {p.order_id: p for p in batch.payments}
    for order in batch.orders:
        if order.order_id in payments:
            return order, payments[order.order_id]
    raise AssertionError("no order/payment pair in batch")


# --- the default is unchanged, so older evidence still describes the code -------------------------


def test_without_a_model_the_interval_is_the_sla_window(batch):
    order, payment = _first_pair(batch)
    p = predict_settlement(order, payment)
    assert p.interval_source == "sla_window"
    assert p.interval_confidence is None, "the SLA window claims no confidence level and must not imply one"


def test_the_sla_window_needs_no_history(batch):
    """A merchant with no settled batch yet still gets a window. That is why it stays the default."""
    order, payment = _first_pair(batch)
    assert predict_settlement(order, payment).predicted_date_low > payment.captured_at


# --- the calibrated interval states a level and widens with it ------------------------------------


def test_a_calibrated_prediction_carries_the_level_it_claims(batch, model):
    order, payment = _first_pair(batch)
    p = predict_settlement(order, payment, model, confidence=0.95)
    assert p.interval_source == "calibrated"
    assert p.interval_confidence == 0.95


def test_a_higher_confidence_gives_a_wider_interval(batch, model):
    order, payment = _first_pair(batch)
    widths = [
        (lambda p: p.predicted_date_high - p.predicted_date_low)(predict_settlement(order, payment, model, c))
        for c in (0.50, 0.90, 0.99)
    ]
    assert widths[0] <= widths[1] <= widths[2]


def test_the_amount_forecast_ignores_the_interval_choice(batch, model):
    """Only the date interval is calibrated. Changing it must not move the money."""
    order, payment = _first_pair(batch)
    assert predict_settlement(order, payment).predicted_net_amount == predict_settlement(order, payment, model, 0.99).predicted_net_amount


def test_the_calibrated_interval_covers_more_than_the_sla_window(batch, model):
    """The reason for wiring it in. Measured on the batch, not asserted from the evidence file."""
    by_payment = {p.payment_id: p for p in batch.payments}
    by_order = {o.order_id: o for o in batch.orders}
    accepted = {t for t, a in assess_batch(batch.orders, batch.payments, batch.refunds).items() if a.forecastable}

    def coverage(mdl, conf):
        hits = n = 0
        for s in batch.settlements:
            payment = by_payment.get(s.payment_id)
            order = by_order.get(payment.order_id) if payment else None
            if order is None or order.order_id not in accepted:
                continue
            p = predict_settlement(order, payment, mdl, conf)
            n += 1
            hits += p.predicted_date_low <= s.settled_at <= p.predicted_date_high
        return hits / n

    assert coverage(model, 0.90) > coverage(None, 0.90)


# --- the amount-error panel ------------------------------------------------------------------------


def test_a_settlement_at_or_below_zero_is_counted_not_divided_by():
    """The regression this panel exists for.

    The old guard was `actual != 0`, so a negative settled amount was divided by, the term came out
    negative, and it pulled down the mean of a quantity defined as an absolute error.
    """
    b, _ = generate(seed=7, main_n=2000, stress_n=0)
    report = run_backtest(b)
    assert report.n_undefined_ape > 0, "this batch is supposed to contain over-netted settlements"
    assert report.mape >= 0.0
    assert report.median_ape >= 0.0


def test_the_mean_sits_above_the_median_because_the_tail_carries_it():
    b, _ = generate(seed=7, main_n=2000, stress_n=0)
    report = run_backtest(b)
    assert report.median_ape < report.mape < report.p95_ape
    assert report.exact_rate > 0.5


def test_the_panel_is_empty_safe():
    panel = ape_panel([], 0)
    assert panel == {"median_ape": 0.0, "mape": 0.0, "p95_ape": 0.0, "worst_ape": 0.0, "n_undefined_ape": 0}


def test_the_panel_orders_its_own_quantiles():
    values = [0.0, 0.0, 0.1, 0.2, 5.0]
    panel = ape_panel(values, 2)
    assert panel["median_ape"] == 0.1
    assert panel["worst_ape"] == 5.0
    assert panel["n_undefined_ape"] == 2
    assert panel["mape"] == pytest.approx(statistics.mean(values))
