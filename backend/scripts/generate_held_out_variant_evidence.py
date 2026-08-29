"""Phase 4 evidence: the "shared author" problem, and whether a real LLM actually generalizes past
it. On the CLEAN duplicate_refund/netting_trap patterns, mock scores 100% (measure_mock_narrator_
accuracy.py) because the same author wrote both the generator's injectors and check_batch_anomalies'
detector to the same exact-match definition -- there's no genuine ambiguity left to resolve.

The held-out near-miss variants (app/data_gen/generate.py, enable_held_out_variants=True) are still
genuinely the same true_label, but perturbed just enough that check_batch_anomalies' exact-match
check can never confirm them.

Measured, not what was hoped going in: mock scores 0/101 (expected -- it only calls check_batch_
anomalies, which never fires on these by construction). Ollama (qwen2.5:7b-instruct) does NOT
generalize past this either -- 0/21 on a real run, the same near-0% floor. Reading the raw reasoning
traces shows why, and it's a genuinely different finding than "the model can't do arithmetic": on
several cases the model's own free-text reasoning correctly notices the near-cancellation (e.g. "the
delta of -15000 is offset by a transaction with delta 15116"), but its own verify_group_sum call --
a STRICT exact-zero check, built for multiway_netting_trap, where exactness genuinely matters --
correctly reports the candidate does NOT cancel exactly, and the model, following its own system
prompt's instruction to never assert an explanation it hasn't verified, appropriately declines rather
than commit to an unverified near-match. This is the SAME cautious-tool-use discipline this project
credits elsewhere (an honest "I don't know" over a confident guess) actively working against success
on THIS specific task -- a real tool-design tension (a tool built correctly for one category can
structurally block a different category that needs approximate matching), not a reasoning failure,
and not fixed by this evidence run -- disclosed as the actual finding, not smoothed over.

Groq is intentionally NOT run by default here: this evidence run measured real, repeated 429s on the
account's own dashboard under sustained sequential calls (confirmed directly, not assumed -- a single
isolated call succeeds in under a second) -- the same free-tier constraint BUILD_LOG already
documents elsewhere (real Groq batches taking 11-70 minutes). Pass --with-groq to include it anyway.

Usage:
    cd backend
    python scripts/generate_held_out_variant_evidence.py [--with-groq]
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
from app.narrator.agent import narrate, narrate_mock
from app.narrator.tools import build_tool_context

SEEDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # mock is free -- the full sweep
REAL_PROVIDER_SEEDS = [1, 2]  # keep modest -- each case can take several tool-call rounds


def _near_miss_cases(seed: int):
    main, _ = generate(seed=seed, main_n=200, stress_n=0, enable_held_out_variants=True)
    chains = build_all_chains(main)
    results = run_matching_engine(chains)
    context = build_tool_context(main, chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    notes_by_id = {g.transaction_id: g.internal_note or "" for g in main.ground_truth}
    near_miss_ids = [
        tid
        for tid, r in results.items()
        if r.resolution == "needs_narration" and ("near-miss" in notes_by_id.get(tid, "") or "near-nets" in notes_by_id.get(tid, ""))
    ]
    return chains, context, near_miss_ids, gt_by_id


def _measure_mock() -> dict:
    print("--- mock, on held-out near-miss cases only ---")
    correct = 0
    total = 0
    for seed in SEEDS:
        chains, context, near_miss_ids, gt_by_id = _near_miss_cases(seed)
        for txn_id in near_miss_ids:
            output = narrate_mock(chains[txn_id], context)
            total += 1
            if output.category == gt_by_id[txn_id]:
                correct += 1
    print(f"  mock: {correct}/{total} = {correct/total:.1%}" if total else "  mock: no near-miss cases found")
    return {"correct": correct, "total": total}


def _measure_real(provider: str) -> dict:
    print(f"--- {provider}, on held-out near-miss cases only ---")
    entries = []
    for seed in REAL_PROVIDER_SEEDS:
        chains, context, near_miss_ids, gt_by_id = _near_miss_cases(seed)
        for txn_id in near_miss_ids:
            output = narrate(chains[txn_id], context, provider=provider)
            correct = output.category == gt_by_id[txn_id]
            entries.append(
                {
                    "seed": seed,
                    "transaction_id": txn_id,
                    "true_label": gt_by_id[txn_id],
                    "predicted": output.category,
                    "correct": correct,
                    "confidence": output.confidence,
                    "reasoning": output.reasoning,
                }
            )
    correct_n = sum(1 for e in entries if e["correct"])
    print(f"  {provider}: {correct_n}/{len(entries)} = {correct_n/len(entries):.1%}" if entries else f"  {provider}: no cases found")
    return {"entries": entries, "correct": correct_n, "total": len(entries)}


def main() -> None:
    evidence_dir = Path(__file__).resolve().parents[2] / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "mock": _measure_mock(),
        "ollama": _measure_real("ollama"),
    }
    # Groq skipped by default: this run measured real, repeated 429s on the account's dashboard
    # under sustained sequential calls (not a code hang -- confirmed directly, a single isolated
    # call succeeds in under a second) -- the same free-tier constraint this project's own
    # BUILD_LOG already documents elsewhere (real Groq batches taking 11-70 minutes). Pass
    # --with-groq to include it anyway, accepting the real wait.
    if "--with-groq" in sys.argv:
        results["groq"] = _measure_real("groq")

    out = evidence_dir / f"held-out-variant-evidence-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this)")


if __name__ == "__main__":
    main()
