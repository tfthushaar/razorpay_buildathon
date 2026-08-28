"""Measures narrate_mock's real accuracy against ground truth across several seeds -- the
reproducible source for the "mock scores 100.0% on all three LLM-routed categories" claim in
BUILD_LOG.md/README.md. Previously run as an ad-hoc one-liner and never committed as a script,
which is exactly the kind of unreproducible headline number this project's own discipline exists to
avoid -- found and fixed after a direct check of what was actually committed.

Usage:
    cd backend
    python scripts/measure_mock_narrator_accuracy.py
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
    by_label: dict[str, list[int]] = {}

    for seed in SEEDS:
        main_batch, stress_batch = generate(seed=seed, main_n=150, stress_n=60)
        for batch in (main_batch, stress_batch):
            chains = build_all_chains(batch)
            results = run_matching_engine(chains)
            context = build_tool_context(batch, chains)
            gt = {g.transaction_id: g.true_label for g in batch.ground_truth}
            queue = [t for t, r in results.items() if r.resolution == "needs_narration"]
            for txn_id in queue:
                output = narrate_mock(chains[txn_id], context)
                total += 1
                label = gt[txn_id]
                by_label.setdefault(label, [0, 0])
                by_label[label][1] += 1
                if output.category == label:
                    correct += 1
                    by_label[label][0] += 1

    print(f"TOTAL: {correct}/{total} = {correct / total:.1%}")
    for label, (c, n) in sorted(by_label.items()):
        print(f"  {label}: {c}/{n} = {c / n:.1%}")


if __name__ == "__main__":
    main()
