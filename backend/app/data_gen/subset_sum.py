"""Pure subset-sum helpers, no pydantic/chain imports -- so both the data generator
(app/data_gen/generate.py, injecting a genuinely-unique multi-way netting case) and the multi-way
netting experiment (app/narrator/multiway_netting_experiment.py, importing SyntheticDataGenerator
from generate.py) can use the same uniqueness check without a circular import between them.

WHY THIS IS MEET-IN-THE-MIDDLE, AND WHY THAT BOUGHT NOTHING.

The original was a loop over itertools.combinations of size 1..4, which is O(n^4):

    n=50    0.098s
    n=100   1.543s
    n=200  25.330s      <- about 16x per doubling, as n^4 predicts

Horowitz and Sahni's meet-in-the-middle (1974) replaces the 3- and 4-subset search with a hash of
every pair sum plus a complement lookup, taking it to O(n^2). Measured: 0.012s at n=200, 1.6s at
n=1,000, 23.7s at n=2,000, against a projected 70 hours for brute force at that size.

None of which this project experiences. Every call site was profiled AFTER the rewrite, and both
pass a pool of ten items or fewer: the generator certifies one multiway group against its own group
plus distractors (n=5), and the experiment against its constructed case (n=10). At n=5 the two
implementations are indistinguishable. The scale experiment, which is the one place n is genuinely
large, does not call this at all -- it has its own k-sum solver in
app/narrator/multiway_netting_optimal_solver.py.

So this is a faster version of something that was never slow, written before checking who calls it.
It is kept rather than reverted because it is equivalence-tested against the original and removes a
cliff if a caller ever does pass a real batch, but it earns no speed claim, and the honest summary is
that the profiling should have come first. Recorded in WHAT_BROKE.md.

Results are identical to the brute-force version, which is not an assumption:
`test_meet_in_the_middle_matches_brute_force_exactly` cross-checks the two implementations over
randomised inputs, and `_brute_force` is kept in this file as the oracle rather than deleted.

Method from the published algorithm, cited in docs/CREDITS.md. No third-party code.
"""

from __future__ import annotations

import itertools
from collections import defaultdict

MAX_SUBSET_SIZE_CHECKED_FOR_UNIQUENESS = 4


def find_other_subsets_that_cancel(
    target_delta: int,
    other_deltas: dict[str, int],
    correct_group: set[str],
    max_subset_size: int = MAX_SUBSET_SIZE_CHECKED_FOR_UNIQUENESS,
) -> list[set[str]]:
    """Every non-empty subset of `other_deltas` up to `max_subset_size` that also cancels
    `target_delta`, excluding `correct_group` itself. An empty return means the case is genuinely
    unambiguous, which is the only thing this is ever used to establish.

    Falls back to brute force above size 4, where the pair index no longer covers every split.
    """
    if max_subset_size > 4:
        return _brute_force(target_delta, other_deltas, correct_group, max_subset_size)

    ids = list(other_deltas)
    want = -target_delta  # a cancelling subset sums to exactly this
    found: list[set[str]] = []
    seen: set[frozenset[str]] = set()

    def keep(combo: tuple[str, ...]) -> None:
        group = set(combo)
        if group == correct_group:
            return
        signature = frozenset(group)
        if signature in seen:
            return
        seen.add(signature)
        found.append(group)

    # Sizes 1 and 2 are cheaper to test directly than to look up.
    for size in (1, 2):
        if size > max_subset_size:
            return found
        for combo in itertools.combinations(ids, size):
            if sum(other_deltas[i] for i in combo) == want:
                keep(combo)

    if max_subset_size < 3:
        return found

    # One pass builds every pair sum; sizes 3 and 4 are then complement lookups against it.
    pairs_by_sum: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for a, b in itertools.combinations(ids, 2):
        pairs_by_sum[other_deltas[a] + other_deltas[b]].append((a, b))

    for single in ids:
        remainder = want - other_deltas[single]
        for a, b in pairs_by_sum.get(remainder, ()):
            if single in (a, b):
                continue  # a real 3-subset, not a pair with one of its own members re-used
            keep((single, a, b))

    if max_subset_size < 4:
        return found

    for first_sum, first_pairs in pairs_by_sum.items():
        for a, b in first_pairs:
            for c, d in pairs_by_sum.get(want - first_sum, ()):
                if len({a, b, c, d}) == 4:
                    keep((a, b, c, d))

    return found


def _brute_force(
    target_delta: int,
    other_deltas: dict[str, int],
    correct_group: set[str],
    max_subset_size: int,
) -> list[set[str]]:
    """The original O(n^4) enumeration, kept as the correctness oracle for the fast path."""
    ids = list(other_deltas)
    matches = []
    for size in range(1, max_subset_size + 1):
        for combo in itertools.combinations(ids, size):
            if set(combo) == correct_group:
                continue
            if target_delta + sum(other_deltas[i] for i in combo) == 0:
                matches.append(set(combo))
    return matches
