"""Generates committed, checkable evidence for multiway_netting_trap as a REAL PRODUCTION
category -- runs the actual narrate() entry point (app/narrator/agent.py, the same one every real
batch run uses) against real generated batches (enable_multiway_netting=True), not the hand-built
case in app/narrator/multiway_netting_experiment.py.

Distinct from that experiment's own evidence file (multiway-netting-experiment-*.json): this one
answers "does the shipped narrator, on real generated data, actually solve this," not "can an LLM
solve a deliberately hard hand-built puzzle." Both matter; neither substitutes for the other.

Usage:
    cd backend
    python scripts/generate_multiway_netting_trap_production_evidence.py
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.engine import run_matching_engine
from app.narrator.agent import narrate
from app.narrator.tools import build_tool_context

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"
SEEDS = [1, 2, 3, 4, 5, 6, 7, 8]


def _run_condition(provider: str) -> list[dict]:
    entries = []
    print(f"--- {provider} ---")
    for seed in SEEDS:
        main_batch, _ = generate(seed=seed, main_n=150, stress_n=0, enable_multiway_netting=True)
        chains = build_all_chains(main_batch)
        results = run_matching_engine(chains)
        context = build_tool_context(main_batch, chains)
        gt = {g.transaction_id: g.true_label for g in main_batch.ground_truth}
        multiway_ids = [
            tid for tid, r in results.items() if r.resolution == "needs_narration" and gt.get(tid) == "multiway_netting_trap"
        ]
        if not multiway_ids:
            print(f"  seed={seed}: no multiway_netting_trap case in this batch, skipped")
            continue
        txn_id = multiway_ids[0]  # one case per seed -- enough to see real seed-to-seed variance without an oversized run
        output = narrate(chains[txn_id], context, provider=provider)
        correct = output.category == "multiway_netting_trap"
        entries.append(
            {
                "seed": seed,
                "transaction_id": txn_id,
                "n_other_transactions_in_batch": len(context.transaction_ids_by_settlement_batch[chains[txn_id].settlement_batch_id]) - 1,
                "correct": correct,
                "predicted_category": output.category,
                "confidence": output.confidence,
                "reasoning": output.reasoning,
                "tool_calls": [tc.tool for tc in output.tool_calls],
            }
        )
        print(f"  seed={seed}: correct={correct} (predicted {output.category})")
    correct_n = sum(1 for e in entries if e["correct"])
    print(f"{provider}: {correct_n}/{len(entries)} correct")
    return entries


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        "mock": _run_condition("mock"),
        "ollama": _run_condition("ollama"),
        "groq": _run_condition("groq"),
    }

    print("\n--- summary ---")
    for key, entries in results.items():
        correct_n = sum(1 for e in entries if e["correct"])
        print(f"{key}: {correct_n}/{len(entries)}")

    out = EVIDENCE_DIR / f"multiway-netting-trap-production-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this -- it's the reproducible evidence for the production category)")


if __name__ == "__main__":
    main()
