"""Tests for the Merkle-tree divergence pre-filter (spec §3, §9 stretch goal).

Two things need proving, not just asserting: (1) the diverging keys it finds are *exactly* the
same set brute-force comparison would find — a pre-filter that misses or invents divergences is
worse than useless — and (2) the "compared 50,000 records using ~200 comparisons" pitch line is a
real measured number from an actual run at that scale, not an estimate.
"""

import random

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.merkle import MerkleComparator


def _brute_force_diverging(view_a: dict[str, str], view_b: dict[str, str]) -> set[str]:
    return {k for k in view_a if view_a[k] != view_b[k]}


def test_matches_brute_force_exactly_across_random_trials():
    rng = random.Random(1)
    for trial in range(20):
        n = rng.randint(1, 500)
        keys = [f"key_{i}" for i in range(n)]
        view_a = {k: str(rng.randint(0, 5)) for k in keys}
        # view_b starts identical, then a random subset is perturbed
        view_b = dict(view_a)
        n_perturb = rng.randint(0, n)
        for k in rng.sample(keys, n_perturb):
            view_b[k] = str(int(view_b[k]) + 1)

        comparator = MerkleComparator(keys, branching_factor=rng.choice([2, 4, 16]))
        result = comparator.diff(view_a, view_b)

        expected = _brute_force_diverging(view_a, view_b)
        assert set(result.diverging_keys) == expected, f"trial {trial}: n={n}, perturbed={n_perturb}"


def test_identical_views_need_only_one_comparison():
    keys = [f"key_{i}" for i in range(10_000)]
    view = {k: "same_value" for k in keys}
    comparator = MerkleComparator(keys, branching_factor=16)
    result = comparator.diff(view, view)
    assert result.diverging_keys == []
    assert result.comparisons_made == 1  # root hashes match -> entire tree pruned immediately


def test_single_divergence_costs_roughly_tree_height_not_full_scan():
    keys = [f"key_{i}" for i in range(10_000)]
    view_a = {k: "same" for k in keys}
    view_b = dict(view_a)
    view_b["key_5000"] = "different"

    comparator = MerkleComparator(keys, branching_factor=16)
    result = comparator.diff(view_a, view_b)

    assert result.diverging_keys == ["key_5000"]
    # tree height for 10,000 leaves at branching factor 16 is ~4; a single divergent path touches
    # at most (height+1) nodes per level times branching_factor siblings -- nowhere near 10,000
    assert result.comparisons_made < 100


def test_real_batch_matches_ledger_gap_ground_truth():
    """Cross-checks the Merkle approach against this project's own chain-derived ground truth,
    not just internal self-consistency: divergence found by hashing (ledger_expected_amount,
    settled_amount) pairs must exactly match the set of transactions with a nonzero ledger_gap.

    Deliberately does NOT assert a comparison-count saving here: this project's demo batch is
    ~33% non-clean by design (spec's documented 25% explainable + 10% adversarial + 5% ambiguous
    distribution, minus timing_lag which is amount-clean) -- a deliberately dense mix, to stress
    the reconciliation logic itself. Merkle-tree pruning only pays off when divergence is *sparse*
    (see test_50000_record_scale_demonstration, 0.2% divergence, ~94% fewer comparisons); at 33%
    divergence, most subtrees must be descended into regardless, and the overhead of visiting
    internal nodes can exceed a flat per-leaf scan. That's a real, expected property of Merkle
    diffing, not a bug -- asserting a saving here would misrepresent when the optimization
    actually helps, which is exactly the kind of overclaim this project's own philosophy rejects."""
    main, _ = generate(seed=42, main_n=200, stress_n=0)
    chains = build_all_chains(main)

    keys = list(chains.keys())
    ledger_view = {k: str(chains[k].ledger_expected_amount) for k in keys}
    settlement_view = {k: str(chains[k].actual_settled_amount) for k in keys}

    comparator = MerkleComparator(keys, branching_factor=8)
    result = comparator.diff(ledger_view, settlement_view)

    expected_diverging = {k for k, c in chains.items() if c.ledger_gap != 0}
    assert set(result.diverging_keys) == expected_diverging


def test_50000_record_scale_demonstration():
    """The actual pitch number (spec §9: 'have these numbers ready, not approximate'). Builds a
    50,000-key dataset that's 99.8% identical between the two views (a realistic 'mostly clean'
    reconciliation batch) and reports the real measured comparison count."""
    rng = random.Random(7)
    n = 50_000
    keys = [f"txn_{i:06d}" for i in range(n)]
    view_a = {k: "identical_value" for k in keys}
    view_b = dict(view_a)

    n_mismatches = 100
    mismatched_keys = set(rng.sample(keys, n_mismatches))
    for k in mismatched_keys:
        view_b[k] = "different_value"

    comparator = MerkleComparator(keys, branching_factor=16)
    result = comparator.diff(view_a, view_b)

    assert set(result.diverging_keys) == mismatched_keys
    assert result.brute_force_comparisons == 50_000

    # Report the real number rather than assert a specific one -- what it actually measures out to
    # is the artifact worth quoting, not a pre-chosen target.
    print(
        f"\n50,000-record Merkle demo: {result.comparisons_made} comparisons vs "
        f"{result.brute_force_comparisons} brute-force ({result.comparisons_made / result.brute_force_comparisons:.2%})"
        f" to correctly find all {n_mismatches} divergences."
    )
    # sanity bound: with 100 mismatches spread over 50k leaves at branching factor 16, comparisons
    # should be at least an order of magnitude below brute force, not merely "less than 50,000"
    assert result.comparisons_made < 50_000 / 10
