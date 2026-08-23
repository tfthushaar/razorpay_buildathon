"""Regression tests for the causal chain builder + matching engine (spec §6.2, §6.3).

The critical property under test: the deterministic Pass 1 + Pass 2 must correctly resolve
clean_match / fee_deduction / partial_refund / timing_lag / currency_rounding *without* ever
claiming to resolve duplicate_refund / netting_trap / genuine_error — those three must always
fall through to "needs_narration". A false auto-resolve on an adversarial case here would be
exactly the failure mode the whole "calibrated autonomy" pitch exists to prevent.
"""

import collections

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.baseline import run_naive_baseline
from app.matching.engine import run_matching_engine

DETERMINISTIC_CATEGORIES = {"clean_match", "fee_deduction", "partial_refund", "timing_lag", "currency_rounding"}
NARRATION_REQUIRED_LABELS = {"duplicate_refund", "netting_trap", "genuine_error"}


def _setup(seed=42, main_n=150, stress_n=0):
    main, _ = generate(seed=seed, main_n=main_n, stress_n=stress_n)
    chains = build_all_chains(main)
    results = run_matching_engine(chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    return main, chains, results, gt_by_id


def test_deterministic_categories_resolve_without_narration_and_match_ground_truth():
    _, _, results, gt_by_id = _setup()
    checked = 0
    for txn_id, true_label in gt_by_id.items():
        if true_label not in DETERMINISTIC_CATEGORIES:
            continue
        result = results[txn_id]
        assert result.resolution != "needs_narration", f"{txn_id} ({true_label}) should resolve deterministically"
        assert result.category == true_label, f"{txn_id}: predicted {result.category}, ground truth {true_label}"
        checked += 1
    assert checked > 0


def test_adversarial_and_ambiguous_categories_always_escalate():
    _, _, results, gt_by_id = _setup()
    checked = 0
    for txn_id, true_label in gt_by_id.items():
        if true_label not in NARRATION_REQUIRED_LABELS:
            continue
        result = results[txn_id]
        assert result.resolution == "needs_narration", (
            f"{txn_id} ({true_label}) was auto-resolved as {result.category} — "
            "an adversarial/ambiguous case must never be resolved without the narrator"
        )
        checked += 1
    assert checked > 0


def test_only_the_genuinely_hard_slice_reaches_the_narrator():
    """Cost check: Pass 1 + Pass 2 alone should resolve clean_match + all four explainable
    exception types with zero LLM calls. Only adversarial + ambiguous should need narration."""
    _, _, results, gt_by_id = _setup(main_n=150)
    needs_narration = {txn_id for txn_id, r in results.items() if r.resolution == "needs_narration"}
    labels_needing_narration = {gt_by_id[txn_id] for txn_id in needs_narration}
    assert labels_needing_narration <= NARRATION_REQUIRED_LABELS
    # roughly matches the spec's 10% adversarial + 5% ambiguous share
    assert 0.05 <= len(needs_narration) / len(gt_by_id) <= 0.25


def test_naive_baseline_silently_misses_timing_lag():
    """Documents the baseline's blind spot on purpose: it has no date/SLA awareness, so a
    transaction stuck beyond the rail's normal window still reads as 'clean' if the amount
    happens to match. This is the false-negative half of the lift story (spec §6.7)."""
    main, chains, _, gt_by_id = _setup()
    baseline = run_naive_baseline(chains)
    timing_lag_ids = [txn_id for txn_id, label in gt_by_id.items() if label == "timing_lag"]
    assert timing_lag_ids
    for txn_id in timing_lag_ids:
        assert baseline[txn_id].clean is True, "naive baseline should (wrongly) call timing_lag cases clean"


def test_naive_baseline_false_positives_on_rounding_noise():
    """The other half of the lift story: zero tolerance means harmless FX rounding drift gets
    flagged as a mismatch requiring manual review, when it isn't one."""
    main, chains, _, gt_by_id = _setup()
    baseline = run_naive_baseline(chains)
    rounding_ids = [txn_id for txn_id, label in gt_by_id.items() if label == "currency_rounding"]
    assert rounding_ids
    for txn_id in rounding_ids:
        assert baseline[txn_id].clean is False, "naive baseline should flag rounding noise as a false-positive mismatch"


def test_match_rate_lift_over_naive_baseline():
    """The one-sentence pitch stat (spec §6.7): our system should resolve (clean + deterministic
    + eventually-narrated) a materially higher share correctly than the naive baseline's blind
    binary check."""
    main, chains, results, gt_by_id = _setup(main_n=150)
    baseline = run_naive_baseline(chains)

    baseline_correct = sum(
        1 for txn_id, label in gt_by_id.items() if baseline[txn_id].clean == (label == "clean_match")
    )
    engine_correct_or_appropriately_escalated = sum(
        1
        for txn_id, label in gt_by_id.items()
        if (results[txn_id].category == label) or (results[txn_id].resolution == "needs_narration" and label in NARRATION_REQUIRED_LABELS)
    )
    total = len(gt_by_id)
    baseline_rate = baseline_correct / total
    engine_rate = engine_correct_or_appropriately_escalated / total

    assert engine_rate > baseline_rate, f"engine {engine_rate:.2%} should beat naive baseline {baseline_rate:.2%}"
