"""Correct disposition, and the guards that stop it being a number that only goes up.

The batch match rate scores every escalation as a failure. For `genuine_error` that is wrong by the
system's own policy: the category is in NEVER_AUTO_RESOLVE, so escalating one is the right answer.
Correct disposition counts that, which raises the mock from 85.0% to 90.0% on seed 42.

A metric that moves a headline up deserves more scepticism than one that moves it down, so the
guards get more tests than the metric does. `test_resolving_a_forbidden_category_is_scored_wrong`
is the load-bearing one: without it, disposition could be maximised by auto-resolving exactly the
cases the policy exists to stop.
"""

import pytest

from app.calibration.calibrator import NEVER_AUTO_RESOLVE
from app.calibration.disposition import score_dispositions
from app.pipeline import run_batch


def _o(true_label, predicted, auto):
    return (true_label, predicted, auto)


# --- the guards ---------------------------------------------------------------------------------


def test_resolving_a_forbidden_category_is_scored_wrong_not_correct():
    """The whole reason this metric is safe to publish. Auto-resolving a genuine_error is a policy
    violation even though the category label is right, so it must never read as a good outcome."""
    report = score_dispositions([_o("genuine_error", "genuine_error", True)])
    assert report.wrongly_resolved == 1
    assert report.correctly_resolved == 0
    assert report.correct_disposition == 0


def test_disposition_cannot_be_gamed_by_resolving_everything():
    """A system that auto-resolves the entire batch, including what it must not touch, scores
    WORSE on disposition than one that escalates properly."""
    forbidden = [_o("genuine_error", "genuine_error", True) for _ in range(10)]
    proper = [_o("genuine_error", "genuine_error", False) for _ in range(10)]
    assert score_dispositions(forbidden).correct_disposition_rate == 0.0
    assert score_dispositions(proper).correct_disposition_rate == 1.0


def test_wrongly_resolved_is_a_share_of_the_total_not_the_resolved_subset():
    """Guard 3. A system that resolves two things and gets one wrong is at 50% of resolutions but
    10% of the batch, and the batch is the honest denominator."""
    outcomes = [_o("netting_trap", "duplicate_refund", True), _o("netting_trap", "netting_trap", True)]
    outcomes += [_o("clean_match", "clean_match", False) for _ in range(18)]
    report = score_dispositions(outcomes)
    assert report.wrongly_resolved == 1
    assert report.wrongly_resolved_rate == pytest.approx(1 / 20)


def test_the_strict_rate_is_still_available_beside_it():
    """Guard 1. Disposition is an addition to the headline, never a replacement."""
    report = score_dispositions([_o("genuine_error", "genuine_error", False), _o("clean_match", "clean_match", True)])
    assert report.strict_resolution_rate == 0.5
    assert report.correct_disposition_rate == 1.0
    assert report.strict_resolution_rate < report.correct_disposition_rate


# --- the ordinary outcomes ----------------------------------------------------------------------


def test_a_wrong_category_on_a_resolved_case_is_wrongly_resolved():
    assert score_dispositions([_o("netting_trap", "duplicate_refund", True)]).wrongly_resolved == 1


def test_escalating_something_resolvable_is_a_miss_not_a_win():
    """Escalating is only correct where the policy forbids resolving. Escalating a netting_trap the
    system could have closed is a real cost -- a human does work a machine could have done."""
    report = score_dispositions([_o("netting_trap", "netting_trap", False)])
    assert report.missed == 1
    assert report.correctly_escalated == 0


def test_the_four_outcomes_partition_the_batch():
    outcomes = [
        _o("netting_trap", "netting_trap", True),
        _o("netting_trap", "duplicate_refund", True),
        _o("netting_trap", "netting_trap", False),
        _o("genuine_error", "genuine_error", False),
    ]
    r = score_dispositions(outcomes)
    assert r.correctly_resolved + r.wrongly_resolved + r.missed + r.correctly_escalated == r.total == 4


def test_an_empty_batch_is_not_a_division_by_zero():
    r = score_dispositions([])
    assert r.correct_disposition_rate == 0.0 and r.strict_resolution_rate == 0.0


# --- on a real batch ----------------------------------------------------------------------------


def test_disposition_exceeds_the_strict_rate_on_a_real_run_and_says_why():
    """On seed 42 the gap is exactly the six genuine_error cases the policy requires escalating."""
    result = run_batch(seed=42, main_n=120, stress_n=0, provider="mock")
    d = result.disposition
    assert d.wrongly_resolved == 0
    assert d.correctly_escalated == 6
    assert d.correct_disposition_rate > d.strict_resolution_rate
    assert d.correct_disposition == d.correctly_resolved + d.correctly_escalated


def test_genuine_error_is_still_the_category_the_policy_forbids():
    """If this ever changes, every number above changes with it."""
    assert NEVER_AUTO_RESOLVE == {"genuine_error"}
