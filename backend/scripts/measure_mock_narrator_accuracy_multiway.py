"""Measures narrate_mock's real accuracy on multiway_netting_trap specifically, across several
seeds of real generated batches (enable_multiway_netting=True) -- not the hand-built experiment
case. Expected, disclosed outcome: near-0%, since narrate_mock never calls list_batch_deltas or
verify_group_sum -- it structurally cannot solve this by construction, unlike every other
LLM-routed category, where the same author's rule already matches the LLM exactly (see
measure_mock_narrator_accuracy.py's own 519/519 result). Confirmed empirically here rather than
assumed, the same discipline every other real-provider claim in this project follows.

Usage:
    cd backend
    python scripts/measure_mock_narrator_accuracy_multiway.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.engine import run_matching_engine
from app.narrator.agent import narrate_mock
from app.narrator.tools import build_tool_context

SEEDS = [1, 7, 42, 101, 123, 202, 303]


def main() -> None:
    total = 0
    correct = 0
    other_total = 0  # every other LLM-routed category, in the SAME batches, as a sanity check
    other_correct = 0

    for seed in SEEDS:
        main_batch, _ = generate(seed=seed, main_n=150, stress_n=0, enable_multiway_netting=True)
        chains = build_all_chains(main_batch)
        results = run_matching_engine(chains)
        context = build_tool_context(main_batch, chains)
        gt = {g.transaction_id: g.true_label for g in main_batch.ground_truth}
        queue = [t for t, r in results.items() if r.resolution == "needs_narration"]
        for txn_id in queue:
            output = narrate_mock(chains[txn_id], context)
            label = gt[txn_id]
            if label == "multiway_netting_trap":
                total += 1
                if output.category == label:
                    correct += 1
            else:
                other_total += 1
                if output.category == label:
                    other_correct += 1

    pct = f"{correct / total:.1%}" if total else "n/a (0 cases)"
    other_pct = f"{other_correct / other_total:.1%}" if other_total else "n/a"
    print(f"multiway_netting_trap: {correct}/{total} = {pct}")
    print(f"every other LLM-routed category, same batches: {other_correct}/{other_total} = {other_pct}")


if __name__ == "__main__":
    main()
