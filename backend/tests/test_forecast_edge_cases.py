"""The four refusal reasons this generator never produced.

`refund_in_flight` was the only one of five ever exercised against a generated batch; the other four
were implemented, unit-tested against hand-built objects, and never fired on real data. That made the
whole measured effect of refusing rest on one reason.

`generate_pending_batch(edge_case_ratio=...)` produces the four missing shapes. It defaults to 0.0, so
every forecast evidence file committed before this still describes the batch it was measured on, and
`test_the_default_batch_is_byte_identical_to_before` holds that default in place.
"""

from collections import Counter
from datetime import timedelta

import pytest

from app.data_gen.generate import generate_pending_batch
from app.forecast.forecastability import REFUSAL_REASONS, assess_batch
from app.forecast.predictor import predict_pending_batch


def _assess(batch, as_of=None):
    return assess_batch(batch.orders, batch.payments, [], as_of=as_of)


# --- the default has to stay exactly what it was ---------------------------------------------------


def test_the_default_batch_refuses_nothing():
    batch = generate_pending_batch(seed=7, n=200)
    assessments = _assess(batch)
    assert all(a.forecastable for a in assessments.values())


def test_the_default_batch_is_byte_identical_to_before():
    """edge_case_ratio=0.0 must not perturb the rng stream, or every committed forecast number moves."""
    a = generate_pending_batch(seed=7, n=50)
    b = generate_pending_batch(seed=7, n=50, edge_case_ratio=0.0)
    assert a.model_dump_json() == b.model_dump_json()


# --- with the flag on, the reasons actually fire ----------------------------------------------------


def test_every_refusal_reason_fires_on_a_generated_batch():
    """The point of the flag. Four reasons had never been seen outside a hand-built object."""
    batch = generate_pending_batch(seed=7, n=200, edge_case_ratio=0.4)
    as_of = max(p.captured_at for p in batch.payments) + timedelta(days=1)
    fired = Counter(r for a in _assess(batch, as_of).values() for r in a.reasons)

    for reason in ("partial_capture", "not_captured", "non_positive_net", "sla_already_breached"):
        assert fired[reason] > 0, f"{reason} still never fires"


def test_refund_in_flight_is_the_one_reason_a_pending_batch_cannot_show():
    """It needs a Refund, and a pending batch has none. It fires on the settled batch instead."""
    batch = generate_pending_batch(seed=7, n=100, edge_case_ratio=0.5)
    fired = {r for a in _assess(batch).values() for r in a.reasons}
    assert "refund_in_flight" not in fired
    assert "refund_in_flight" in REFUSAL_REASONS


def test_a_zero_capture_is_refused_for_two_reasons_not_one():
    """Under a pure-percentage fee schedule with no flat floor, fee + tax never exceeds a positive
    capture, so non_positive_net is reachable only at zero -- where partial_capture fires too."""
    batch = generate_pending_batch(seed=7, n=200, edge_case_ratio=0.4)
    zero = [a for a in _assess(batch).values() if "non_positive_net" in a.reasons]
    assert zero, "no zero-capture case in the batch"
    assert all("partial_capture" in a.reasons for a in zero)


def test_more_edge_cases_means_fewer_forecastable():
    counts = [
        sum(1 for a in _assess(generate_pending_batch(seed=7, n=200, edge_case_ratio=r)).values() if a.forecastable)
        for r in (0.0, 0.25, 0.5)
    ]
    assert counts[0] > counts[1] > counts[2]


@pytest.mark.parametrize("ratio", [0.0, 0.3, 1.0])
def test_the_predictor_never_crashes_on_a_refused_shape(ratio):
    """Refusing is a policy the caller applies. The predictor itself still has to survive the input,
    including a payment captured for zero and one never captured at all."""
    batch = generate_pending_batch(seed=3, n=60, edge_case_ratio=ratio)
    predictions = predict_pending_batch(batch.orders, batch.payments)
    assert len(predictions) == len(batch.payments)


def test_every_reason_that_fires_has_an_explanation():
    batch = generate_pending_batch(seed=7, n=120, edge_case_ratio=0.5)
    as_of = max(p.captured_at for p in batch.payments) + timedelta(days=1)
    for assessment in _assess(batch, as_of).values():
        if not assessment.forecastable:
            assert assessment.explain()
            assert all(r in REFUSAL_REASONS for r in assessment.reasons)
