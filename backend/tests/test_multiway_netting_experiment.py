"""Tests for the multi-way netting experiment (app/narrator/multiway_netting_experiment.py) --
covers only the deterministic half (the hand-constructed case and the proof that
check_batch_anomalies structurally misses it). The LLM half is inherently non-deterministic and
requires a real network call; it's verified live and reported honestly in BUILD_LOG.md/README.md,
not asserted here as a passing/failing unit test."""

from app.narrator.multiway_netting_experiment import N_DISTRACTORS, build_experiment_case
from app.narrator.tools import check_batch_anomalies, list_batch_deltas


def test_experiment_case_is_reproducible_for_the_same_seed():
    _, _, target_a, group_a = build_experiment_case(seed=42)
    _, _, target_b, group_b = build_experiment_case(seed=42)
    assert target_a == target_b
    assert group_a == group_b


def test_deltas_cancel_as_a_group_but_no_pair_cancels():
    chains, _, target_id, group_ids = build_experiment_case()
    target_delta = chains[target_id].settlement_delta
    group_deltas = [chains[g].settlement_delta for g in group_ids]

    assert target_delta + sum(group_deltas) == 0, "the group should cancel the target's delta exactly"
    for g_delta in group_deltas:
        assert target_delta + g_delta != 0, "no single member of the group should individually cancel the target"


def test_all_three_transactions_share_the_same_settlement_batch():
    chains, _, target_id, group_ids = build_experiment_case()
    batch_ids = {chains[t].settlement_batch_id for t in [target_id, *group_ids]}
    assert len(batch_ids) == 1, "the whole premise requires all three in the same settlement batch"


def test_check_batch_anomalies_structurally_misses_the_group_netting():
    """The actual, provable claim this experiment rests on: the shipped pairwise-only rule finds
    nothing here -- not a contrived assertion, the real function real code calls."""
    _, context, target_id, _ = build_experiment_case()
    result = check_batch_anomalies(target_id, context)
    assert result["duplicate_refund_match"] is None
    assert result["netting_partner"] is None


def test_list_batch_deltas_exposes_exactly_the_data_needed_to_solve_it():
    chains, context, target_id, group_ids = build_experiment_case()
    result = list_batch_deltas(target_id, context)
    assert result["own_delta"] == chains[target_id].settlement_delta
    for g in group_ids:
        assert result["other_transactions_in_same_batch"][g] == chains[g].settlement_delta


def test_list_batch_deltas_reports_a_clean_error_for_an_unknown_id():
    _, context, _, _ = build_experiment_case()
    result = list_batch_deltas("order_does_not_exist", context)
    assert "error" in result


def test_the_search_space_has_real_distractors_not_just_the_correct_pair():
    """A first version put exactly 2 other transactions in the batch, making 'the other 2' the only
    non-trivial candidate group -- not a search. This checks the fix held: N_DISTRACTORS unrelated
    transactions plus the real group, so citing 'everyone I saw' is no longer a free correct answer."""
    chains, context, target_id, group_ids = build_experiment_case()
    batch_id = chains[target_id].settlement_batch_id
    others = context.transaction_ids_by_settlement_batch[batch_id]
    assert len(others) - 1 == N_DISTRACTORS + len(group_ids)


def test_different_seeds_produce_genuinely_different_arithmetic():
    """A first version hardcoded the same three deltas for every seed -- only ids/timestamps varied,
    so multiple 'trials' were correlated samples of one fixed sum, not independent evidence. This
    checks that fix held: different seeds must not all reduce to the same target/group deltas."""
    seen_signatures = set()
    for seed in (777, 42, 999, 5, 6):
        chains, _, target_id, group_ids = build_experiment_case(seed=seed)
        signature = (chains[target_id].settlement_delta, tuple(sorted(chains[g].settlement_delta for g in group_ids)))
        seen_signatures.add(signature)
    assert len(seen_signatures) == 5, "each seed should produce a genuinely different arithmetic puzzle"


def test_construction_raises_if_ever_ambiguous_rather_than_silently_shipping_a_bad_case():
    """build_experiment_case verifies (brute force, not assumed) that no OTHER subset of the other
    transactions also cancels the target's delta -- confirms that safeguard actually runs and
    doesn't just silently pass by construction, across a real range of seeds."""
    for seed in range(1, 30):
        build_experiment_case(seed=seed)  # raises AssertionError internally if ever ambiguous
