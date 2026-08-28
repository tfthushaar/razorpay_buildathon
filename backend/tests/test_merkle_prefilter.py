"""Tests for wiring the Merkle pre-filter into the live matching pipeline (matching/
merkle_prefilter.py) as an opt-in Pass 0.

Two things need proving, not just asserting: (1) the pre-filter's "provably clean" set is exactly
the set Pass 1 (engine.run_pass1) would resolve as clean_match on its own -- never more, never
fewer -- and (2) routing those transactions through the fast path produces byte-identical
MatchResults to the unfiltered pipeline, for every transaction in the batch, not just the clean
ones. An optimization that changes even one result is a correctness bug, not a stretch goal.
"""

import time

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.engine import run_matching_engine
from app.matching.merkle_prefilter import run_merkle_prefilter


def test_provably_clean_set_matches_pass1_exactly():
    """Cross-checks against the chain-derived ground truth directly: provably_clean_order_ids
    must equal exactly the set of transactions with ledger_gap == 0 and within_sla -- the same
    condition run_pass1 checks -- across both the dense demo distribution and a sparse one."""
    for clean_ratio in (0.60, 0.97):
        main, _ = generate(seed=11, main_n=300, stress_n=0, clean_ratio=clean_ratio)
        chains = build_all_chains(main)
        prefilter = run_merkle_prefilter(main)

        expected = {txn_id for txn_id, c in chains.items() if c.ledger_gap == 0 and c.within_sla}
        assert prefilter.provably_clean_order_ids == expected, f"clean_ratio={clean_ratio}"


def test_filtered_and_unfiltered_pipelines_produce_identical_results():
    """The correctness invariant the whole integration depends on: for every seed/ratio tried,
    running the matching engine with the Merkle-derived clean set must produce EXACTLY the same
    MatchResult (resolution, category, confidence, reasoning) as running it without, for every
    single transaction -- not just the fast-pathed ones."""
    for seed in (1, 7, 42, 123):
        for clean_ratio in (0.60, 0.85, 0.97):
            main, _ = generate(seed=seed, main_n=250, stress_n=0, clean_ratio=clean_ratio)
            chains = build_all_chains(main)
            prefilter = run_merkle_prefilter(main)

            unfiltered = run_matching_engine(chains)
            filtered = run_matching_engine(chains, merkle_clean_ids=prefilter.provably_clean_order_ids)

            assert unfiltered.keys() == filtered.keys()
            for txn_id in unfiltered:
                assert unfiltered[txn_id] == filtered[txn_id], (
                    f"seed={seed}, clean_ratio={clean_ratio}, txn_id={txn_id}: "
                    f"merkle-filtered path diverged from the unfiltered result"
                )
            # every transaction the pre-filter claimed clean must actually have been fast-pathed
            # to clean_pass1 -- not just "still correct by coincidence via the slow path"
            for txn_id in prefilter.provably_clean_order_ids:
                assert filtered[txn_id].resolution == "clean_pass1"


def test_50000_record_realistic_scale_benchmark():
    """The honest, measured pitch numbers for the actual live-pipeline integration (have
    real numbers ready, not estimates) -- both the comparison-count reduction (merkle.py's own
    strength) AND the wall-clock difference in THIS project's specific in-memory implementation,
    reported as measured, not assumed. See BUILD_LOG.md for the numbers this run produced and the
    honest framing of what they do and don't show given every transaction still needs a full
    CausalChain built regardless of the pre-filter (MatchResult.chain is a required field)."""
    main, _ = generate(seed=7, main_n=50_000, stress_n=0, clean_ratio=0.97)
    chains = build_all_chains(main)
    prefilter = run_merkle_prefilter(main)

    non_clean = len(main.orders) - len(prefilter.provably_clean_order_ids)
    assert 0 < non_clean < 2_000

    diff = prefilter.merkle_diff
    # NOT a >90% reduction here, and deliberately not asserted as one: the generator shuffles all
    # records together (generate_main_batch's own rng.shuffle), so the ~3% divergent keys are
    # scattered uniformly across the full 50,000-key space rather than clustered. At branching_
    # factor=16, a leaf-group of 16 keys has only a ~60% chance of containing zero divergent keys
    # (0.97^16), so ~40% of groups must still be fully descended into and rehashed -- this is a
    # real, measured property of scattered divergence, not the sparser 0.2%-divergence, clustering-
    # agnostic case test_merkle.py's own 50k demo measures. Asserting a specific bound here would
    # either be a tautology (< brute_force_comparisons) or an overclaim; the honest number is
    # printed below and belongs in BUILD_LOG.md as measured, not assumed.
    assert diff.comparisons_made < diff.brute_force_comparisons

    n_trials = 5
    unfiltered_times = []
    filtered_times = []
    for _ in range(n_trials):
        t0 = time.perf_counter()
        run_matching_engine(chains)
        unfiltered_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        run_matching_engine(chains, merkle_clean_ids=prefilter.provably_clean_order_ids)
        filtered_times.append(time.perf_counter() - t0)

    best_unfiltered = min(unfiltered_times)
    best_filtered = min(filtered_times)
    print(
        f"\n50,000-record live-pipeline Merkle benchmark (clean_ratio=0.97):\n"
        f"  Merkle comparisons: {diff.comparisons_made} vs {diff.brute_force_comparisons} brute-force "
        f"({diff.comparisons_made / diff.brute_force_comparisons:.2%})\n"
        f"  run_matching_engine wall-clock: unfiltered={best_unfiltered*1000:.1f}ms, "
        f"merkle-filtered={best_filtered*1000:.1f}ms "
        f"({(1 - best_filtered / best_unfiltered):.1%} faster)"
    )
