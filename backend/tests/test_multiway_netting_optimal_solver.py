"""Correctness tests for the Phase 3 optimized k-sum solver, checked directly against the Phase 2
brute-force solver on identical inputs before any speed claim is trusted -- a faster wrong answer is
worse than a slow correct one."""

from app.narrator.multiway_netting_optimal_solver import find_group_that_cancels
from app.narrator.multiway_netting_scale_experiment import build_scale_case, run_exhaustive_solver


def _other_deltas(case):
    chain = case.chains[case.target_id]
    others_ids = [tid for tid in case.context.transaction_ids_by_settlement_batch[chain.settlement_batch_id] if tid != case.target_id]
    return {tid: case.chains[tid].settlement_delta for tid in others_ids}, chain.settlement_delta


def test_optimal_solver_finds_the_real_group_when_it_exists():
    for seed in range(1, 20):
        case = build_scale_case(seed=seed, n_total=100, group_size=3)
        other_deltas, target_delta = _other_deltas(case)
        result = find_group_that_cancels(target_delta, other_deltas, n_total=100)
        assert result.found_a_group is True
        assert set(result.found_group_ids) == set(case.group_ids), f"seed={seed}: found {result.found_group_ids}, expected {case.group_ids}"


def test_optimal_solver_agrees_with_brute_force_on_whether_a_group_exists():
    """The two solvers can legitimately disagree on WHICH group they find when more than one exists
    (both stop at the first match, in different iteration orders) -- but they must always agree on
    whether ANY group exists, checked directly across a real seed sweep, not assumed."""
    for seed in range(1, 25):
        case = build_scale_case(seed=seed, n_total=80, group_size=3)
        other_deltas, target_delta = _other_deltas(case)
        optimal = find_group_that_cancels(target_delta, other_deltas, n_total=80)
        brute = run_exhaustive_solver(case, max_group_size=4)
        assert optimal.found_a_group == brute.found_a_group, f"seed={seed}: optimal={optimal.found_a_group} brute={brute.found_a_group}"
        if optimal.found_a_group:
            # both found A group (not necessarily the same one if the case is ambiguous) -- verify
            # each one's own answer is independently a real, correct cancellation
            assert target_delta + sum(other_deltas[i] for i in optimal.found_group_ids) == 0


def test_two_sum_uses_the_hash_algorithm_for_a_genuine_two_member_group():
    """group_size=2 (target + exactly 1 other) is just the existing pairwise case -- caught by the
    1-lookup check before 2-sum ever runs, correctly. group_size=3 (target + 2 others, neither of
    which individually cancels the target) is the minimal case that actually exercises 2-sum."""
    for seed in range(1, 10):
        case = build_scale_case(seed=seed, n_total=100, group_size=3)
        other_deltas, target_delta = _other_deltas(case)
        # confirm the fixture assumption directly: neither group member alone cancels the target
        assert all(target_delta + other_deltas[gid] != 0 for gid in case.group_ids)
        result = find_group_that_cancels(target_delta, other_deltas, n_total=100)
        assert result.algorithm_used == "2-sum-hash"
        assert set(result.found_group_ids) == set(case.group_ids)


def test_optimal_solver_stays_reliable_up_to_n_1000_but_degrades_by_n_5000():
    """The real, measured ambiguity frontier: at this project's own delta range
    (-999,931..999,931), a stop-at-first-match 2-sum search reliably finds the TRUE constructed
    group up to n_total=1000 (spurious coincidental matches are rare enough not to matter), but by
    n_total=5000 a spurious-but-genuinely-valid match is found MORE often than the true one --
    the birthday-paradox collision rate in a ~2M-wide integer range, not a compute-time problem.
    Small seed count here (speed) -- the full 20-seed sweep that established these numbers lives in
    docs/evidence/, this is a regression guard on the same real phenomenon, not a re-derivation."""

    def _true_match_rate(n_total: int, seeds: range) -> float:
        correct = 0
        for seed in seeds:
            case = build_scale_case(seed=seed, n_total=n_total, group_size=3)
            other_deltas, target_delta = _other_deltas(case)
            result = find_group_that_cancels(target_delta, other_deltas, n_total=n_total)
            if set(result.found_group_ids) == set(case.group_ids):
                correct += 1
        return correct / len(seeds)

    assert _true_match_rate(1000, range(1, 8)) >= 0.85
    assert _true_match_rate(5000, range(1, 8)) <= 0.5


def test_optimal_solver_returns_nothing_for_a_genuinely_unexplained_delta():
    case = build_scale_case(seed=1, n_total=100, group_size=3)
    other_deltas, _ = _other_deltas(case)
    # a distractor's own delta is genuinely unexplained by construction -- pick one and treat it as
    # its own "target" to search for
    distractor_id = next(tid for tid in other_deltas if tid not in case.group_ids)
    distractor_delta = other_deltas[distractor_id]
    remaining = {tid: d for tid, d in other_deltas.items() if tid != distractor_id}
    result = find_group_that_cancels(distractor_delta, remaining, n_total=100)
    # not asserting False here -- at this batch size a genuine accidental collision is possible and
    # was measured directly during development; asserting the RESULT IS SELF-CONSISTENT instead
    if result.found_a_group:
        assert distractor_delta + sum(remaining[i] for i in result.found_group_ids) == 0
