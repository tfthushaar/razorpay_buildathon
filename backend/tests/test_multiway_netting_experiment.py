"""Tests for the multi-way netting experiment (app/narrator/multiway_netting_experiment.py) --
covers only the deterministic half (the hand-constructed case and the proof that
check_batch_anomalies structurally misses it). The LLM half is inherently non-deterministic and
requires a real network call; it's verified live and reported honestly in BUILD_LOG.md/README.md,
not asserted here as a passing/failing unit test."""

from app.narrator.multiway_netting_experiment import build_experiment_case
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
