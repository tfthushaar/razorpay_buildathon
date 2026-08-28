"""Generates committed, checkable evidence for the multi-way netting experiment
(app/narrator/multiway_netting_experiment.py): runs the real experiment across several seeds for
both real providers, with and without the verify_group_sum tool, and dumps the raw results -- so
"the rule misses this, the LLM catches it (at this rate, on this model, with/without verification)"
is independently verifiable from a file, not asserted in prose.

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


def _run_condition(provider: str, with_verification_tool: bool) -> list[dict]:
    entries = []
    label = f"{provider} (verification={'on' if with_verification_tool else 'off'})"
    print(f"--- {label} ---")
    for seed in SEEDS:
        r = run_experiment(seed=seed, provider=provider, with_verification_tool=with_verification_tool)
        entries.append(
            {
                "seed": seed,
                "target_transaction_id": r.target_transaction_id,
                "group_transaction_ids": r.group_transaction_ids,
                "n_other_transactions_in_batch": r.n_other_transactions_in_batch,
                "correct": r.llm_correctly_identified_the_group,
                "cited_transaction_ids": r.llm_cited_transaction_ids,
                "raw_response": r.llm_raw_response,
            }
        )
        print(f"  seed={seed}: correct={r.llm_correctly_identified_the_group}")
    correct = sum(1 for e in entries if e["correct"])
    print(f"{label}: {correct}/{len(entries)} correct")
    return entries


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    first_seed_rule = run_experiment(seed=SEEDS[0], provider="ollama")
    results = {
        "rule": {
            "duplicate_refund_match_found": first_seed_rule.rule_found_duplicate_refund,
            "netting_partner_found": first_seed_rule.rule_found_netting_partner,
            "verdict": first_seed_rule.rule_verdict,
        },
        "ollama_without_verification": _run_condition("ollama", with_verification_tool=False),
        "ollama_with_verification": _run_condition("ollama", with_verification_tool=True),
        "groq_without_verification": _run_condition("groq", with_verification_tool=False),
        "groq_with_verification": _run_condition("groq", with_verification_tool=True),
    }

    print("\n--- summary ---")
    for key in ("ollama_without_verification", "ollama_with_verification", "groq_without_verification", "groq_with_verification"):
        entries = results[key]
        correct = sum(1 for e in entries if e["correct"])
        print(f"{key}: {correct}/{len(entries)}")

    out = EVIDENCE_DIR / f"multiway-netting-experiment-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this -- it's the reproducible evidence for the experiment)")


if __name__ == "__main__":
    main()
