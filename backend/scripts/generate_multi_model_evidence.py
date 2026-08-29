"""Phase 6: "which model" as a measured axis, not an anecdote -- focused specifically on the two
categories this project has already shown are genuinely hard (multiway_netting_trap, Phase 1;
narration_explained, Phase 5). The existing three categories (duplicate_refund/netting_trap/
genuine_error) are deliberately NOT re-swept here: mock itself already scores 100% on them
(measure_mock_narrator_accuracy.py), so every model, regardless of size, is expected to also score
~100% -- re-measuring that across model sizes would only confirm a fact already established, not add
a new one. The two hard categories are where model capability could plausibly matter, and did,
measurably, in Phase 1's own evidence.

Models actually tested, confirmed pullable/runnable on this machine before being included here, not
assumed:
- qwen2.5:7b-instruct (Ollama, local) -- this project's own recommended default.
- qwen2.5:14b-instruct (Ollama, local) -- pulled and confirmed running for this evidence run.
- openai/gpt-oss-20b (Groq, hosted) -- included with a small, bounded sample; a sustained sequential
  run against this account measured real, repeated 429s on Groq's own dashboard under load (not a
  code hang, confirmed directly), so this script does not attempt a full sweep on it by default.
gpt-oss-120b was not included: no verified hosted or local path was confirmed available in this
environment before writing this script, and this project's own discipline is not to promise a model
was tested when it wasn't actually run.

Usage:
    cd backend
    python scripts/generate_multi_model_evidence.py [--with-groq]
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
from app.narrator.agent import narrate_groq, narrate_ollama
from app.narrator.tools import build_tool_context

MULTIWAY_SEEDS = list(range(1, 9))
NARRATION_SEEDS = list(range(1, 6))

OLLAMA_MODELS = ["qwen2.5:7b-instruct", "qwen2.5:14b-instruct"]


def _multiway_cases(seed: int, main_n: int = 150):
    main, _ = generate(seed=seed, main_n=main_n, stress_n=0, enable_multiway_netting=True)
    chains = build_all_chains(main)
    results = run_matching_engine(chains)
    context = build_tool_context(main, chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    ids = [tid for tid, r in results.items() if r.resolution == "needs_narration" and gt_by_id.get(tid) == "multiway_netting_trap"]
    return chains, context, ids


def _narration_cases(seed: int, main_n: int = 200):
    main, _ = generate(seed=seed, main_n=main_n, stress_n=0, enable_narration_explained=True)
    chains = build_all_chains(main)
    results = run_matching_engine(chains)
    context = build_tool_context(main, chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    ids = [tid for tid, r in results.items() if r.resolution == "needs_narration" and gt_by_id.get(tid) == "narration_explained"]
    return chains, context, ids


def _measure_ollama_model(model: str, category: str) -> dict:
    print(f"--- {model} on {category} ---")
    entries = []
    if category == "multiway_netting_trap":
        for seed in MULTIWAY_SEEDS:
            chains, context, ids = _multiway_cases(seed)
            for txn_id in ids[:1]:
                output = narrate_ollama(chains[txn_id], context, model=model)
                entries.append({"seed": seed, "correct": output.category == category, "predicted": output.category})
    else:
        for seed in NARRATION_SEEDS:
            chains, context, ids = _narration_cases(seed)
            for txn_id in ids[:1]:
                output = narrate_ollama(chains[txn_id], context, model=model)
                entries.append({"seed": seed, "correct": output.category == category, "predicted": output.category})
    correct = sum(1 for e in entries if e["correct"])
    print(f"  {model}: {correct}/{len(entries)}" if entries else "  no cases found")
    return {"entries": entries, "correct": correct, "total": len(entries)}


def _measure_groq(category: str, seeds: list[int]) -> dict:
    print(f"--- groq (openai/gpt-oss-20b) on {category} ---")
    entries = []
    getter = _multiway_cases if category == "multiway_netting_trap" else _narration_cases
    for seed in seeds:
        chains, context, ids = getter(seed)
        for txn_id in ids[:1]:
            output = narrate_groq(chains[txn_id], context)
            entries.append({"seed": seed, "correct": output.category == category, "predicted": output.category})
    correct = sum(1 for e in entries if e["correct"])
    print(f"  groq: {correct}/{len(entries)}" if entries else "  no cases found")
    return {"entries": entries, "correct": correct, "total": len(entries)}


def main() -> None:
    evidence_dir = Path(__file__).resolve().parents[2] / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    results: dict = {"multiway_netting_trap": {}, "narration_explained": {}}
    for category in results:
        for model in OLLAMA_MODELS:
            results[category][model] = _measure_ollama_model(model, category)
        if "--with-groq" in sys.argv:
            seeds = MULTIWAY_SEEDS[:3] if category == "multiway_netting_trap" else NARRATION_SEEDS[:3]
            results[category]["openai/gpt-oss-20b (groq)"] = _measure_groq(category, seeds)

    print("\n--- summary ---")
    for category, by_model in results.items():
        print(f"{category}:")
        for model, r in by_model.items():
            print(f"  {model}: {r['correct']}/{r['total']}")

    out = evidence_dir / f"multi-model-evidence-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this)")


if __name__ == "__main__":
    main()
