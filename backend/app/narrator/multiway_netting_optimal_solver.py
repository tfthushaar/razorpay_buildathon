"""Phase 3: the strongest deterministic rule actually worth building for this task, not a brute-force
placeholder standing in for "a rule could theoretically be extended to do this."

`multiway_netting_scale_experiment.py`'s own `run_exhaustive_solver` is real brute force --
`itertools.combinations` up to a bounded group size, correct, but O(n^k) for a group of size k. That's
fine up to a few hundred transactions (measured directly: C(800,2) is still fast), but the honest
frontier this module exists to find requires something better before claiming anything about n in the
high hundreds to a thousand.

Real k-sum algorithms, not a bigger brute-force budget:
- size 1: direct O(n) scan (what check_batch_anomalies itself already does).
- size 2 ("2-sum"): a single hash-set pass, O(n).
- size 3 ("3-sum"): sort once, then a two-pointer scan per anchor element, O(n^2).
- size 4 ("4-sum" / meet-in-the-middle): hash every pair's sum, O(n^2) pairs, then look up each
  pair's complement among the other pairs, checking for four genuinely distinct transactions -- O(n^2)
  instead of brute force's O(n^4).

Every one of these is correctness-checked against the brute-force solver on identical inputs
(tests/test_multiway_netting_optimal_solver.py) before being trusted for any scale claim -- a faster
wrong answer is worse than a slow correct one, and this project's whole discipline is measuring, not
assuming, so the speed claim itself gets checked the same way every other claim here does.
"""

from __future__ import annotations

import time

from pydantic import BaseModel


class OptimalSolverResult(BaseModel):
    n_total: int
    found_a_group: bool
    found_group_ids: list[str]
    algorithm_used: str  # "1-lookup" | "2-sum-hash" | "3-sum-two-pointer" | "4-sum-meet-in-the-middle" | "none-up-to-max-group-size"
    elapsed_seconds: float


def _two_sum(target: int, deltas: dict[str, int]) -> list[str] | None:
    seen: dict[int, str] = {}
    for tid, d in deltas.items():
        need = -target - d
        match = seen.get(need)
        if match is not None and match != tid:
            return [match, tid]
        seen[d] = tid
    return None


def _three_sum(target: int, deltas: dict[str, int]) -> list[str] | None:
    items = sorted(deltas.items(), key=lambda kv: kv[1])
    n = len(items)
    for i in range(n):
        anchor_id, anchor_delta = items[i]
        need = -target - anchor_delta
        lo, hi = i + 1, n - 1
        while lo < hi:
            lo_id, lo_delta = items[lo]
            hi_id, hi_delta = items[hi]
            s = lo_delta + hi_delta
            if s == need:
                return [anchor_id, lo_id, hi_id]
            if s < need:
                lo += 1
            else:
                hi -= 1
    return None


def _four_sum_meet_in_the_middle(target: int, deltas: dict[str, int]) -> list[str] | None:
    ids = list(deltas.keys())
    n = len(ids)
    pair_sums: dict[int, list[tuple[str, str]]] = {}
    for i in range(n):
        di = deltas[ids[i]]
        for j in range(i + 1, n):
            s = di + deltas[ids[j]]
            pair_sums.setdefault(s, []).append((ids[i], ids[j]))

    for s, pairs in pair_sums.items():
        complement = -target - s
        complement_pairs = pair_sums.get(complement)
        if not complement_pairs:
            continue
        for a1, a2 in pairs:
            for b1, b2 in complement_pairs:
                group = {a1, a2, b1, b2}
                if len(group) == 4:
                    return [a1, a2, b1, b2]
    return None


def find_group_that_cancels(target_delta: int, other_deltas: dict[str, int], n_total: int, max_group_size: int = 4) -> OptimalSolverResult:
    """other_deltas keyed by transaction id, EXCLUDING the target itself. Tries increasing group
    sizes with the fastest correct algorithm for each, stopping at the first match -- same
    first-match semantics as the brute-force solver, so the two are directly comparable."""
    t0 = time.perf_counter()

    for tid, d in other_deltas.items():
        if target_delta + d == 0:
            return OptimalSolverResult(n_total=n_total, found_a_group=True, found_group_ids=[tid], algorithm_used="1-lookup", elapsed_seconds=time.perf_counter() - t0)

    if max_group_size >= 2:
        found = _two_sum(target_delta, other_deltas)
        if found:
            return OptimalSolverResult(n_total=n_total, found_a_group=True, found_group_ids=found, algorithm_used="2-sum-hash", elapsed_seconds=time.perf_counter() - t0)

    if max_group_size >= 3:
        found = _three_sum(target_delta, other_deltas)
        if found:
            return OptimalSolverResult(n_total=n_total, found_a_group=True, found_group_ids=found, algorithm_used="3-sum-two-pointer", elapsed_seconds=time.perf_counter() - t0)

    if max_group_size >= 4:
        found = _four_sum_meet_in_the_middle(target_delta, other_deltas)
        if found:
            return OptimalSolverResult(n_total=n_total, found_a_group=True, found_group_ids=found, algorithm_used="4-sum-meet-in-the-middle", elapsed_seconds=time.perf_counter() - t0)

    return OptimalSolverResult(n_total=n_total, found_a_group=False, found_group_ids=[], algorithm_used="none-up-to-max-group-size", elapsed_seconds=time.perf_counter() - t0)
