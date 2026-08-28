"""Generates committed, checkable evidence for the multi-way netting experiment
(app/narrator/multiway_netting_experiment.py): runs the real experiment across several seeds for
both real providers and dumps the raw results -- so "the rule misses this, the LLM catches it (at
this rate, on this model)" is independently verifiable from a file, not asserted in prose.

Usage:
    cd backend
    python scripts/generate_multiway_netting_evidence.py
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app.narrator.multiway_netting_experiment import run_experiment

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"

SEEDS = [777, 42, 999, 5, 6, 100, 200, 300]


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    results = {"rule": None, "ollama": [], "groq": []}

    for provider in ("ollama", "groq"):
        print(f"--- {provider} ---")
        for seed in SEEDS:
            r = run_experiment(seed=seed, provider=provider)
            if results["rule"] is None:
                results["rule"] = {
                    "duplicate_refund_match_found": r.rule_found_duplicate_refund,
                    "netting_partner_found": r.rule_found_netting_partner,
                    "verdict": r.rule_verdict,
                }
            entry = {
                "seed": seed,
                "target_transaction_id": r.target_transaction_id,
                "group_transaction_ids": r.group_transaction_ids,
                "correct": r.llm_correctly_identified_the_group,
                "cited_transaction_ids": r.llm_cited_transaction_ids,
                "raw_response": r.llm_raw_response,
            }
            results[provider].append(entry)
            print(f"  seed={seed}: correct={r.llm_correctly_identified_the_group}")

    for provider in ("ollama", "groq"):
        n = len(results[provider])
        correct = sum(1 for e in results[provider] if e["correct"])
        print(f"{provider}: {correct}/{n} correct")

    out = EVIDENCE_DIR / f"multiway-netting-experiment-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this -- it's the reproducible evidence for the experiment)")


if __name__ == "__main__":
    main()
