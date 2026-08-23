"""Merkle-tree divergence pre-filter (optional stretch, spec §3, §6.3, §9).

The pitch: "compared 50,000 records using ~200 comparisons." Borrowed from how Cassandra/DynamoDB
do anti-entropy repair between replicas — hash-chunk two views of the same key space, compare
rolled-up hashes top-down, and only descend into (and eventually inspect) the branches that
actually diverge. A subtree whose combined hash matches on both sides is *provably* identical
underneath — matching per spec's own exact-match logic — so it never needs a single per-record
comparison.

At this project's actual demo batch size (100-200 records), Pass 1's direct O(1)-per-transaction
check is already fast in absolute terms — the value here isn't runtime speed at that scale, it's
the provable comparison-count claim at a scale where brute-force actually would matter. See
backend/tests/test_merkle.py for a 50,000-record demonstration with real measured numbers, not
estimates.

Both "views" being compared (e.g. the ledger's expected amounts vs. the settlement's actual
amounts) must cover the *same* key set — this lets both trees share one partition of the sorted
keys into leaves/internal nodes, so comparing corresponding nodes is a direct index walk rather
than a general tree-diff algorithm.
"""

import hashlib
from dataclasses import dataclass


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@dataclass
class MerkleDiffResult:
    diverging_keys: list[str]
    comparisons_made: int
    total_keys: int

    @property
    def brute_force_comparisons(self) -> int:
        """What checking every record individually would have cost, for the lift comparison."""
        return self.total_keys


class MerkleComparator:
    """Builds one shared tree shape over a sorted key set, then compares two 'views' (key -> a
    string summarizing whatever should be identical if the record is clean) using that shape."""

    def __init__(self, keys: list[str], branching_factor: int = 16):
        if not keys:
            raise ValueError("MerkleComparator needs at least one key")
        self.keys = sorted(keys)
        self.branching_factor = branching_factor

    def _levels(self, leaf_hashes: list[str]) -> list[list[str]]:
        levels = [leaf_hashes]
        current = leaf_hashes
        while len(current) > 1:
            nxt = [_hash("|".join(current[i : i + self.branching_factor])) for i in range(0, len(current), self.branching_factor)]
            levels.append(nxt)
            current = nxt
        return levels  # levels[0] = per-key leaf hashes, levels[-1] = single root hash

    def diff(self, view_a: dict[str, str], view_b: dict[str, str]) -> MerkleDiffResult:
        leaf_hashes_a = [_hash(f"{k}:{view_a[k]}") for k in self.keys]
        leaf_hashes_b = [_hash(f"{k}:{view_b[k]}") for k in self.keys]
        levels_a = self._levels(leaf_hashes_a)
        levels_b = self._levels(leaf_hashes_b)
        top_level = len(levels_a) - 1

        comparisons = 0
        diverging_indices: list[int] = []

        def walk(level: int, idx: int) -> None:
            nonlocal comparisons
            comparisons += 1
            if levels_a[level][idx] == levels_b[level][idx]:
                return  # whole subtree provably identical -- prune, no further comparisons needed
            if level == 0:
                diverging_indices.append(idx)
                return
            child_start = idx * self.branching_factor
            child_end = min(child_start + self.branching_factor, len(levels_a[level - 1]))
            for child_idx in range(child_start, child_end):
                walk(level - 1, child_idx)

        walk(top_level, 0)
        return MerkleDiffResult(
            diverging_keys=[self.keys[i] for i in diverging_indices],
            comparisons_made=comparisons,
            total_keys=len(self.keys),
        )
