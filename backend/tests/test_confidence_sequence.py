"""The anytime-valid bound that replaced a repeatedly-peeked Wilson interval.

The load-bearing test here is `test_the_bound_is_not_exceeded_under_repeated_checking`. Everything
else could pass on a bound that is simply very conservative; only that one shows the guarantee holds
when the gate is checked after every batch, which is how the calibration loop actually uses it.
"""

import random

import pytest

from app.calibration.confidence_sequence import (
    accuracy_lower_bound,
    lower_bound_from_outcomes,
    wilson_vs_sequence,
)
from app.calibration.wilson import wilson_score_interval


# --- validity ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("true_p", [0.85, 0.95])
def test_the_bound_is_not_exceeded_under_repeated_checking(true_p):
    """A confidence sequence must satisfy P(exists t : lower bound > true accuracy) <= alpha.

    Checked the way the gate uses it: recompute after every batch, and count a trial as a violation
    if the bound EVER claimed more than the truth. A fixed-n interval fails this; that failure is
    the reason this module exists.
    """
    rng = random.Random(23)
    violations = trials = 0
    for _ in range(60):
        outcomes: list[bool] = []
        violated = False
        for _ in range(20):
            outcomes += [rng.random() < true_p for _ in range(5)]
            if lower_bound_from_outcomes(outcomes) > true_p:
                violated = True
        violations += violated
        trials += 1
    assert violations / trials <= 0.10, f"bound exceeded the truth in {violations}/{trials} sequences"


def test_a_below_threshold_cause_rarely_reaches_the_gate():
    """The practical version of the same property. A cause genuinely at 85% should almost never be
    handed autonomy at a 90% gate, however many times the gate is checked."""
    rng = random.Random(11)
    crossed = 0
    for _ in range(40):
        outcomes: list[bool] = []
        for _ in range(20):
            outcomes += [rng.random() < 0.85 for _ in range(5)]
            if lower_bound_from_outcomes(outcomes) >= 0.90:
                crossed += 1
                break
    assert crossed <= 2, f"{crossed}/40 sequences at 85% true accuracy reached a 90% gate"


# --- it is stricter than Wilson exactly where that matters -------------------------------------------


def test_forty_perfect_decisions_no_longer_qualify():
    """The concrete change. Wilson calls 40/40 a 91.2% lower bound and clears a 90% gate; under a
    bound valid at every stopping time it is 86.6%, and 55 is the first n that qualifies."""
    assert wilson_score_interval(40, 40)[0] >= 0.90
    assert accuracy_lower_bound(40, 40) < 0.90
    assert accuracy_lower_bound(55, 55) >= 0.90


def test_more_evidence_raises_the_bound():
    bounds = [accuracy_lower_bound(n, n) for n in (20, 40, 80, 160)]
    assert bounds == sorted(bounds)


def test_a_wrong_decision_lowers_the_bound():
    assert accuracy_lower_bound(59, 60) < accuracy_lower_bound(60, 60)


# --- ordering ------------------------------------------------------------------------------------------


def test_the_counts_only_path_takes_the_least_favourable_ordering():
    """The bound depends on WHEN the failures happened, because the bets are sized from history.

    A first draft replayed the correct decisions first, which is the ordering that flatters the
    bound most. Callers that never recorded the order must get the worst case, not the best.
    """
    n, correct = 60, 57
    failures_first = lower_bound_from_outcomes([False] * (n - correct) + [True] * correct)
    failures_last = lower_bound_from_outcomes([True] * correct + [False] * (n - correct))
    assert failures_first < failures_last, "this fixture no longer exercises ordering sensitivity"
    assert accuracy_lower_bound(correct, n) == pytest.approx(failures_first)


def test_a_known_ordering_is_used_when_it_is_available():
    """`lower_bound_from_outcomes` is given the real chronological sequence by the calibrator, so it
    is allowed to be less pessimistic than the counts-only path."""
    outcomes = [True] * 57 + [False] * 3
    assert lower_bound_from_outcomes(outcomes) >= accuracy_lower_bound(57, 60)


# --- edges ---------------------------------------------------------------------------------------------


def test_no_decisions_gives_no_confidence():
    assert accuracy_lower_bound(0, 0) == 0.0
    assert lower_bound_from_outcomes([]) == 0.0


def test_all_wrong_gives_a_zero_bound():
    assert accuracy_lower_bound(0, 30) == 0.0


def test_the_bound_never_leaves_the_unit_interval():
    for correct, n in ((0, 1), (1, 1), (500, 500), (250, 500)):
        assert 0.0 <= accuracy_lower_bound(correct, n) <= 1.0


def test_the_comparison_helper_reports_both_bounds():
    row = wilson_vs_sequence(40, 40)
    assert row["wilson_lower"] > row["sequence_lower"]
    assert row["cost_points"] == pytest.approx((row["wilson_lower"] - row["sequence_lower"]) * 100, abs=0.01)
