"""Tests for paired significance testing.

Two of these encode mistakes that were actually made and published: a three-case difference reported
as "parsimony beats every reader", and a sensitivity check that returned p=0.0 by summing an empty
range after conceding more cases than a side had.
"""

import pytest

from app.calibration.significance import compare_paired, exact_mcnemar_p, robustness_p


def test_no_discordant_pairs_is_never_significant():
    assert exact_mcnemar_p(0, 0) == 1.0


def test_an_even_split_is_never_significant():
    for n in (1, 5, 20):
        assert exact_mcnemar_p(n, n) == pytest.approx(1.0)


def test_a_clean_sweep_of_discordant_pairs_is_significant():
    assert exact_mcnemar_p(10, 0) < 0.01
    assert exact_mcnemar_p(5, 0) == pytest.approx(2 * (0.5**5))


def test_the_test_is_symmetric():
    for a, b in ((7, 4), (12, 2), (1, 0), (30, 11)):
        assert exact_mcnemar_p(a, b) == exact_mcnemar_p(b, a)


def test_p_value_never_exceeds_one():
    for a in range(6):
        for b in range(6):
            assert 0.0 <= exact_mcnemar_p(a, b) <= 1.0


def test_a_three_case_difference_is_not_a_finding():
    """The published error this module exists to prevent: 19/60 vs 16/60 reported as one system
    beating another. The paired test on those same discordant counts is nowhere near significant."""
    assert exact_mcnemar_p(7, 4) > 0.4


def test_conceding_more_cases_than_a_side_has_cannot_manufacture_significance():
    """Regression: conceding 2 from a 0-vs-1 split produced a count of -1, an empty summation range,
    and a confident p=0.0 for a comparison with a single discordant case."""
    assert robustness_p(0, 1, concede=2) == pytest.approx(1.0)
    assert robustness_p(1, 0, concede=2) == pytest.approx(1.0)
    for a in range(4):
        for b in range(4):
            assert 0.0 <= robustness_p(a, b, concede=2) <= 1.0


def test_conceding_cases_can_only_weaken_a_result():
    for a, b in ((12, 2), (18, 6), (30, 11)):
        assert robustness_p(a, b, concede=2) >= exact_mcnemar_p(a, b)


def test_compare_paired_uses_only_shared_cases_and_keys_by_id():
    a = {"t1": True, "t2": True, "t3": False, "only_in_a": True}
    b = {"t1": True, "t2": False, "t3": False, "only_in_b": True}
    c = compare_paired("a", a, "b", b)
    assert c.n == 3
    assert (c.both, c.only_a, c.only_b, c.neither) == (1, 1, 0, 1)
    assert c.correct_a == 2 and c.correct_b == 1


def test_compare_paired_is_not_fooled_by_ordering():
    """Keyed rather than positional: a reordered dict must give the identical result, since a
    positional pairing would silently compare unrelated cases and still look healthy."""
    a = {"t1": True, "t2": False, "t3": True}
    b = {"t3": True, "t1": False, "t2": True}
    forward = compare_paired("a", a, "b", b)
    reversed_ = compare_paired("a", dict(reversed(list(a.items()))), "b", b)
    assert (forward.only_a, forward.only_b) == (reversed_.only_a, reversed_.only_b)


def test_summary_states_the_verdict_in_words():
    c = compare_paired("x", {"1": True, "2": True}, "y", {"1": False, "2": False})
    assert "discordant" in c.summary() and "McNemar" in c.summary()
    assert "NOT distinguishable" in compare_paired("x", {"1": True}, "y", {"1": True}).summary()
