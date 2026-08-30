"""Meet-in-the-middle subset sum, checked against the brute force it replaced.

The speedup is worth nothing if the answers change, and this helper is used to certify that a
generated multi-way netting case has exactly ONE right answer. A fast solver that misses a
cancelling subset would silently publish ambiguous cases as unambiguous, which is a worse failure
than being slow.

So `_brute_force` stays in the module as the oracle, and
`test_meet_in_the_middle_matches_brute_force_exactly` cross-checks the two over randomised inputs
with deliberately many collisions.
"""

import random
import time

import pytest

from app.data_gen.subset_sum import _brute_force, find_other_subsets_that_cancel


def _as_sets(groups: list[set[str]]) -> set[frozenset[str]]:
    return {frozenset(g) for g in groups}


# --- equivalence, which is the whole basis for the swap ---------------------------------------------


@pytest.mark.parametrize("seed", range(12))
def test_meet_in_the_middle_matches_brute_force_exactly(seed):
    """Small amplitudes on purpose: they force many coincidental cancellations, which is exactly
    where a lookup-based solver would drift from an exhaustive one."""
    rng = random.Random(seed)
    n = rng.randint(6, 18)
    deltas = {f"t{i}": rng.randint(-6, 6) for i in range(n)}
    target = rng.randint(-6, 6)
    correct = {f"t{i}" for i in rng.sample(range(n), rng.randint(1, 3))}

    fast = find_other_subsets_that_cancel(target, deltas, correct)
    slow = _brute_force(target, deltas, correct, 4)
    assert _as_sets(fast) == _as_sets(slow)


@pytest.mark.parametrize("max_size", [1, 2, 3, 4])
def test_every_subset_size_matches_brute_force(max_size):
    rng = random.Random(99)
    deltas = {f"t{i}": rng.randint(-5, 5) for i in range(14)}
    fast = find_other_subsets_that_cancel(3, deltas, set(), max_subset_size=max_size)
    slow = _brute_force(3, deltas, set(), max_size)
    assert _as_sets(fast) == _as_sets(slow)


def test_sizes_above_four_fall_back_rather_than_answering_wrongly():
    """The pair index covers 4-subsets. Beyond that it would miss splits, so the slow path runs."""
    rng = random.Random(5)
    deltas = {f"t{i}": rng.randint(-4, 4) for i in range(10)}
    fast = find_other_subsets_that_cancel(2, deltas, set(), max_subset_size=5)
    slow = _brute_force(2, deltas, set(), 5)
    assert _as_sets(fast) == _as_sets(slow)


# --- the properties the generator depends on ---------------------------------------------------------


def test_the_correct_group_is_never_returned_as_an_alternative():
    deltas = {"a": 5, "b": -3, "c": -2}
    assert find_other_subsets_that_cancel(0, deltas, {"b", "c"}) == [] or {"b", "c"} not in find_other_subsets_that_cancel(0, deltas, {"b", "c"})


def test_a_genuinely_unique_case_reports_no_alternatives():
    # -10 is cancelled only by {a, b}: no other subset of these sums to 10.
    deltas = {"a": 7, "b": 3, "c": 100, "d": -55}
    assert find_other_subsets_that_cancel(-10, deltas, {"a", "b"}) == []


def test_a_member_is_never_reused_inside_one_subset():
    """A pair index makes double-counting easy: {a, b} plus {a, c} is not a 4-subset."""
    deltas = {"a": 5, "b": -5, "c": 1, "d": -1}
    for group in find_other_subsets_that_cancel(0, deltas, set()):
        assert len(group) == len(set(group))


def test_results_are_deduplicated():
    deltas = {"a": 2, "b": -2, "c": 3, "d": -3}
    groups = find_other_subsets_that_cancel(0, deltas, set())
    assert len(groups) == len(_as_sets(groups))


def test_an_empty_pool_finds_nothing():
    assert find_other_subsets_that_cancel(10, {}, set()) == []


# --- the reason for the change -----------------------------------------------------------------------


def test_it_is_fast_enough_to_reach_the_scale_the_experiment_wanted():
    """Brute force needed about 25s at n=200 and roughly four hours at n=1,000. The frontier
    recorded in WHAT_BROKE.md was set by that cost, not by the problem."""
    rng = random.Random(1)
    deltas = {f"t{i}": rng.randint(-50_000, 50_000) for i in range(1_000)}
    start = time.perf_counter()
    find_other_subsets_that_cancel(12_345, deltas, set())
    assert time.perf_counter() - start < 20.0
