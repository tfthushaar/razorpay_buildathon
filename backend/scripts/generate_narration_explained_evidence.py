"""Phase 5 evidence: narration_explained -- a delta explained only by the settlement's own free-text
remarks field, never by any structured field or delta-arithmetic a rule could check at any scale
(not even the combinatorial multiway_netting_trap machinery, which only ever looks at deltas).
mock never calls read_bank_narration, so it fails structurally by construction -- confirmed
empirically here, not assumed, the same posture as multiway_netting_trap's own evidence script.

Groq is skipped by default (pass --with-groq to include it): a real, sustained sequential-call run
against this account measured repeated 429s on Groq's own dashboard (confirmed directly, not
assumed -- an isolated single call succeeds in under a second), the same free-tier constraint
BUILD_LOG already documents elsewhere (real Groq batches taking 11-70 minutes).

Usage:
    cd backend
    python scripts/generate_narration_explained_evidence.py [--with-groq]
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

MOCK_SEEDS = list(range(1, 21))  # free -- a full sweep
# Raised from 5 seeds (which produced n=10) after a review pointed out the obvious: a 10/10 result has
# a 95% Wilson LOWER BOUND of 72.2%, so "10/10" and "clearly good enough to trust" are not the same
# claim and the first should never be written as if it were the second. Thirty seeds is still a
# tractable number of real local calls and puts the lower bound somewhere worth quoting.
REAL_PROVIDER_SEEDS = list(range(1, 31))


def _first_case_per_seed(seed: int, main_n: int = 200):
    main, _ = generate(seed=seed, main_n=main_n, stress_n=0, enable_narration_explained=True)
    chains = build_all_chains(main)
    results = run_matching_engine(chains)
    context = build_tool_context(main, chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    ids = [tid for tid, r in results.items() if r.resolution == "needs_narration" and gt_by_id.get(tid) == "narration_explained"]
    return chains, context, ids


def _measure(provider: str, seeds: list[int]) -> dict:
    print(f"--- {provider} ---")
    entries = []
    for seed in seeds:
        chains, context, ids = _first_case_per_seed(seed)
        for txn_id in ids:
            output = narrate(chains[txn_id], context, provider=provider)
            correct = output.category == "narration_explained"
            entries.append(
                {
                    "seed": seed,
                    "transaction_id": txn_id,
                    "bank_narration": chains[txn_id].bank_narration,
                    "predicted": output.category,
                    "correct": correct,
                    "confidence": output.confidence,
                    "reasoning": output.reasoning,
                    "tool_calls": [tc.tool for tc in output.tool_calls],
                }
            )
    correct_n = sum(1 for e in entries if e["correct"])
    print(f"  {provider}: {correct_n}/{len(entries)} = {correct_n/len(entries):.1%}" if entries else f"  {provider}: no cases found")
    return {"entries": entries, "correct": correct_n, "total": len(entries)}


def main() -> None:
    evidence_dir = Path(__file__).resolve().parents[2] / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "mock": _measure("mock", MOCK_SEEDS),
        "ollama": _measure("ollama", REAL_PROVIDER_SEEDS),
    }
    if "--with-groq" in sys.argv:
        results["groq"] = _measure("groq", REAL_PROVIDER_SEEDS)

    out = evidence_dir / f"narration-explained-evidence-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this)")


if __name__ == "__main__":
    main()
