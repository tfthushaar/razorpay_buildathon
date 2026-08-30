"""Entropy over resampled readings, and the AUROC that decides whether it is worth gating on.

The AUROC helper is the part worth testing hard. Entropy over five samples takes very few distinct
values, so ties are the common case rather than an edge case, and a rank-sum that mishandles them
would report a signal where there is none.
"""

import math

import pytest

from app.resolver.semantic_entropy import auroc, choice_entropy, components_signature


class _Component:
    def __init__(self, cause: str, amount: int):
        self.cause = cause
        self.amount = amount


# --- entropy ------------------------------------------------------------------------------------------


def test_total_agreement_is_zero_entropy():
    result = choice_entropy(["a", "a", "a", "a"])
    assert result.entropy == 0.0
    assert not math.copysign(1, result.entropy) < 0, "negative zero leaks into the evidence file"
    assert result.n_distinct_answers == 1
    assert result.modal_share == 1.0


def test_total_disagreement_is_maximum_entropy():
    result = choice_entropy(["a", "b", "c", "d"])
    assert result.entropy == pytest.approx(math.log(4), abs=1e-4)
    assert result.normalised_entropy == pytest.approx(1.0, abs=1e-4)


def test_entropy_rises_with_disagreement():
    ordered = [
        choice_entropy(["a", "a", "a", "a"]).entropy,
        choice_entropy(["a", "a", "a", "b"]).entropy,
        choice_entropy(["a", "a", "b", "b"]).entropy,
        choice_entropy(["a", "b", "c", "d"]).entropy,
    ]
    assert ordered == sorted(ordered)


def test_failed_samples_are_counted_and_dropped_not_clustered():
    """Treating a failed call as its own answer would let provider flakiness read as model
    uncertainty, which is how a missing API key once read as a model that could not reason."""
    result = choice_entropy(["a", "a", None, None])
    assert result.failed_samples == 2
    assert result.n_samples == 2
    assert result.entropy == 0.0


def test_all_samples_failing_gives_no_result_rather_than_zero():
    assert choice_entropy([None, None]) is None


def test_normalisation_makes_different_sample_counts_comparable():
    assert choice_entropy(["a", "b"]).normalised_entropy == pytest.approx(1.0, abs=1e-4)
    assert choice_entropy(["a", "b", "c", "d", "e"]).normalised_entropy == pytest.approx(1.0, abs=1e-4)


# --- signatures ------------------------------------------------------------------------------------------


def test_component_order_does_not_create_false_disagreement():
    a = components_signature([_Component("tds", 100), _Component("gst", 50)])
    b = components_signature([_Component("gst", 50), _Component("tds", 100)])
    assert a == b


def test_the_same_causes_at_different_amounts_are_different_answers():
    a = components_signature([_Component("tds", 100)])
    b = components_signature([_Component("tds", 200)])
    assert a != b


# --- AUROC ------------------------------------------------------------------------------------------------


def test_a_perfect_signal_scores_one():
    # entropy low on correct, high on wrong
    assert auroc([0.0, 0.0, 1.0, 1.0], [True, True, False, False]) == 1.0


def test_a_backwards_signal_scores_zero():
    assert auroc([1.0, 1.0, 0.0, 0.0], [True, True, False, False]) == 0.0


def test_no_signal_scores_a_half():
    assert auroc([0.5, 0.5, 0.5, 0.5], [True, False, True, False]) == 0.5


def test_ties_are_averaged_rather_than_ordered_arbitrarily():
    """With five samples entropy takes a handful of values, so most pairs tie. Breaking ties by list
    order would manufacture a signal out of the order cases happened to be scored in."""
    forwards = auroc([0.0, 0.0, 0.0, 1.0], [True, True, False, False])
    backwards = auroc([0.0, 0.0, 0.0, 1.0], [False, True, True, False])
    assert forwards == backwards == 0.75


def test_auroc_is_undefined_when_every_case_agrees():
    assert auroc([0.1, 0.2], [True, True]) is None
    assert auroc([0.1, 0.2], [False, False]) is None


def test_mismatched_lengths_are_rejected():
    with pytest.raises(ValueError):
        auroc([0.1, 0.2], [True])
