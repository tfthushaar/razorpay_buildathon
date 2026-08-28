"""Pure subset-sum helpers, no pydantic/chain imports -- so both the data generator
(app/data_gen/generate.py, injecting a genuinely-unique multi-way netting case) and the multi-way
netting experiment (app/narrator/multiway_netting_experiment.py, importing SyntheticDataGenerator
from generate.py) can use the same uniqueness check without a circular import between them.

Extracted from multiway_netting_experiment.py's own `_find_other_subsets_that_cancel`, unchanged in
behavior -- a pure refactor, not a new algorithm."""

from __future__ import annotations

import itertools

MAX_SUBSET_SIZE_CHECKED_FOR_UNIQUENESS = 4  # brute-force cap; cheap at the batch sizes this runs against


def find_other_subsets_that_cancel(
    target_delta: int,
    other_deltas: dict[str, int],
    correct_group: set[str],
    max_subset_size: int = MAX_SUBSET_SIZE_CHECKED_FOR_UNIQUENESS,
) -> list[set[str]]:
    """Brute-force every non-empty subset of `other_deltas` up to `max_subset_size` that also
    cancels `target_delta` -- used only to verify a hand-constructed (or generated) case has exactly
    one right answer, not left to chance. Returns every OTHER subset that cancels besides
    `correct_group` itself; an empty return means the case is genuinely unambiguous."""
    ids = list(other_deltas.keys())
    matches = []
    for size in range(1, max_subset_size + 1):
        for combo in itertools.combinations(ids, size):
            if set(combo) == correct_group:
                continue
            if target_delta + sum(other_deltas[i] for i in combo) == 0:
                matches.append(set(combo))
    return matches
