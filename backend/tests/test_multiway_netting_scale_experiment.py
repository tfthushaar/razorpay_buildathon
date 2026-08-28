"""Tests for the deterministic parts of app/narrator/multiway_netting_scale_experiment.py (Phase 2:
the real-scale multiway netting stress test). LLM-calling functions (run_llm_condition) are exercised
live via scripts/generate_multiway_netting_scale_evidence.py, not here -- consistent with how
test_multiway_netting_experiment.py itself only unit-tests construction, not live model calls."""

from app.narrator.multiway_netting_scale_experiment import (
    _list_batch_deltas_prefiltered,
    _list_batch_deltas_unfiltered,
    build_scale_case,
    run_exhaustive_solver,
    run_pairwise_rule,
)


def test_build_scale_case_produces_the_requested_total():
    """n_total is the whole batch, including the target itself -- group_size of those n_total are
    the target + its real group, the rest are distractors."""
    case = build_scale_case(seed=1, n_total=50, group_size=3)
    batch_ids = case.context.transaction_ids_by_settlement_batch[case.chains[case.target_id].settlement_batch_id]
    assert len(batch_ids) == 50
    assert case.target_id in batch_ids
    assert set(case.group_ids) <= set(batch_ids)
    assert len(case.group_ids) == case.group_size - 1


def test_build_scale_case_group_deltas_sum_to_exactly_zero():
    case = build_scale_case(seed=1, n_total=50, group_size=4)
    total = case.chains[case.target_id].settlement_delta
    for gid in case.group_ids:
        total += case.chains[gid].settlement_delta
    assert total == 0


def test_build_scale_case_is_deterministic():
    a = build_scale_case(seed=7, n_total=30, group_size=3)
    b = build_scale_case(seed=7, n_total=30, group_size=3)
    assert a.target_id == b.target_id
    assert a.group_ids == b.group_ids


def test_pairwise_rule_never_solves_a_genuine_multiway_case():
    """Structural claim, verified across several seeds/sizes, not assumed from a single run."""
    for seed in range(1, 8):
        case = build_scale_case(seed=seed, n_total=40, group_size=3)
        result = run_pairwise_rule(case)
        assert result.solved is False


def test_exhaustive_solver_always_finds_the_real_group_at_moderate_scale():
    for seed in range(1, 8):
        case = build_scale_case(seed=seed, n_total=60, group_size=3)
        result = run_exhaustive_solver(case, max_group_size=4)
        assert result.found_a_group is True
        assert set(result.found_group_ids) == set(case.group_ids)
        assert result.combinations_checked_to_find_it > 0
        assert result.seconds_to_find_it >= 0


def test_exhaustive_solver_scales_with_n_total_as_expected():
    """Not a strict monotonic guarantee per-seed (where the true group happens to sit in iteration
    order varies), but the combinatorial growth should be visible in aggregate across seeds."""
    small_checks = sum(run_exhaustive_solver(build_scale_case(seed=s, n_total=30, group_size=3)).combinations_checked_to_find_it for s in range(1, 6))
    large_checks = sum(run_exhaustive_solver(build_scale_case(seed=s, n_total=150, group_size=3)).combinations_checked_to_find_it for s in range(1, 6))
    assert large_checks > small_checks


def test_prefilter_discards_the_real_group_at_a_real_measured_rate_not_never():
    """A first version of this test asserted the pre-filter NEVER discards the real group -- false,
    caught by running it: group deltas can have wildly uneven individual magnitudes even though they
    sum to zero together (e.g. +999,000 and -998,900 both explaining a target delta of -100), so a
    ratio-to-target filter can genuinely drop a real member. Measured directly at the shipped default
    (MAGNITUDE_PREFILTER_MULTIPLE=10.0): under 10% discard rate across a real seed sweep -- real,
    disclosed, not assumed to be zero."""
    discarded = 0
    total = 40
    for seed in range(1, total + 1):
        case = build_scale_case(seed=seed, n_total=200, group_size=3)
        shown = _list_batch_deltas_prefiltered(case)
        shown_ids = set(shown["other_transactions_in_same_batch"].keys())
        if not (set(case.group_ids) <= shown_ids):
            discarded += 1
    assert discarded / total < 0.10, f"discard rate {discarded}/{total} exceeds the measured expectation -- investigate before trusting the default tolerance"


def test_prefilter_shows_strictly_fewer_or_equal_transactions_than_unfiltered():
    case = build_scale_case(seed=1, n_total=200, group_size=3)
    filtered = _list_batch_deltas_prefiltered(case)
    unfiltered = _list_batch_deltas_unfiltered(case)
    assert len(filtered["other_transactions_in_same_batch"]) <= len(unfiltered["other_transactions_in_same_batch"])
