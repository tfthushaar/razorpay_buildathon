"""Phase 3 evidence: the strongest deterministic rule actually built for multi-way netting
(app/narrator/multiway_netting_optimal_solver.py), measured against the honest frontier -- where it
wins outright (fast, reliable), where brute force alone becomes impractical, and where ambiguity
(a spurious-but-genuinely-valid match) becomes a real risk distinct from a compute-time problem.

Usage:
    cd backend
    python scripts/generate_multiway_netting_optimal_solver_evidence.py
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.narrator.multiway_netting_optimal_solver import find_group_that_cancels
from app.narrator.multiway_netting_scale_experiment import build_scale_case, run_exhaustive_solver

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"

TIMING_SIZES = [100, 500, 1000, 2000, 5000]
AMBIGUITY_SIZES = [50, 100, 200, 500, 1000, 1500, 2000, 3000, 5000]
AMBIGUITY_SEEDS = 30
BRUTE_FORCE_CUTOFF = 800  # skip brute force above this -- already shown impractical, don't re-burn the time

# build_scale_case's `group_size` counts the target ITSELF, so group_size=3 means only 2 other
# transactions cancel it -- which is a 2-sum. The first version of this script swept group_size=3
# alone and consequently published a timing table in which `optimal_algorithm` read "2-sum-hash" at
# every single row: the 3-sum and 4-sum paths were built, tested, and never once exercised by the
# evidence that described them. Sweeping 3/4/5 runs all three, which is the point of having written
# all three.
GROUP_SIZES = [3, 4, 5]


def _other_deltas(case):
    chain = case.chains[case.target_id]
    others_ids = [tid for tid in case.context.transaction_ids_by_settlement_batch[chain.settlement_batch_id] if tid != case.target_id]
    return {tid: case.chains[tid].settlement_delta for tid in others_ids}, chain.settlement_delta


def _timing_comparison() -> list[dict]:
    print("--- timing: brute force vs. optimal, real measurements (all three k-sum paths) ---")
    entries = []
    for group_size in GROUP_SIZES:
      for n in TIMING_SIZES:
        case = build_scale_case(seed=1, n_total=n, group_size=group_size)
        other_deltas, target_delta = _other_deltas(case)

        t0 = time.perf_counter()
        optimal = find_group_that_cancels(target_delta, other_deltas, n_total=n)
        optimal_secs = time.perf_counter() - t0

        brute_secs = None
        brute_correct = None
        if n <= BRUTE_FORCE_CUTOFF:
            brute = run_exhaustive_solver(case, max_group_size=4)
            brute_secs = brute.seconds_to_find_it
            brute_correct = set(brute.found_group_ids) == set(case.group_ids)

        entry = {
            "n_total": n,
            "group_size": group_size,
            "true_group_members": len(case.group_ids),
            "optimal_seconds": optimal_secs,
            "optimal_algorithm": optimal.algorithm_used,
            "optimal_found_true_group": set(optimal.found_group_ids) == set(case.group_ids),
            "brute_force_seconds": brute_secs,
            "brute_force_found_true_group": brute_correct,
            "brute_force_skipped_above_cutoff": n > BRUTE_FORCE_CUTOFF,
        }
        entries.append(entry)
        bf_str = f"{brute_secs:.4f}s" if brute_secs is not None else "skipped (>{})".format(BRUTE_FORCE_CUTOFF)
        print(
            f"  group_size={group_size} ({len(case.group_ids)} others) n_total={n}: "
            f"optimal={optimal_secs:.5f}s ({optimal.algorithm_used}), brute_force={bf_str}, "
            f"found_true={entry['optimal_found_true_group']}"
        )
    return entries


def _ambiguity_sweep() -> list[dict]:
    print("--- ambiguity frontier: does the optimal solver find the TRUE constructed group, or a spurious-but-valid one? ---")
    entries = []
    for group_size in GROUP_SIZES:
      for n in AMBIGUITY_SIZES:
        correct = 0
        wrong_size = 0
        for seed in range(1, AMBIGUITY_SEEDS + 1):
            case = build_scale_case(seed=seed, n_total=n, group_size=group_size)
            other_deltas, target_delta = _other_deltas(case)
            result = find_group_that_cancels(target_delta, other_deltas, n_total=n)
            if set(result.found_group_ids) == set(case.group_ids):
                correct += 1
            elif result.found_a_group and len(result.found_group_ids) < len(case.group_ids):
                # a SMALLER spurious group cancelled first -- the solver stops at the first match by
                # design, so a coincidental 2-sum pre-empts a real 4-sum. Counted separately because
                # it is a different failure from "found nothing": the answer genuinely cancels, it is
                # just not the constructed one, and it arrives sooner the larger the true group is.
                wrong_size += 1
        rate = correct / AMBIGUITY_SEEDS
        entries.append(
            {
                "n_total": n,
                "group_size": group_size,
                "true_group_found": correct,
                "spurious_smaller_group_found_first": wrong_size,
                "seeds": AMBIGUITY_SEEDS,
                "true_match_rate": rate,
            }
        )
        print(f"  group_size={group_size} n_total={n}: true group found {correct}/{AMBIGUITY_SEEDS} = {rate:.0%} (spurious-smaller-first: {wrong_size})")
    return entries


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    results = {
        "timing_comparison": _timing_comparison(),
        "ambiguity_frontier": _ambiguity_sweep(),
    }
    out = EVIDENCE_DIR / f"multiway-netting-optimal-solver-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this)")


if __name__ == "__main__":
    main()
