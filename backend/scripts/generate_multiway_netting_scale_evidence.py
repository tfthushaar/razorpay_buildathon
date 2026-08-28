"""Phase 2 evidence: the multi-way netting task at real-settlement-batch scale (hundreds of
transactions in one batch), not the small, hand-built experiment case or the product category's own
deliberately-bounded one. Three real measurements, committed together:

1. The pairwise rule and a real exhaustive combinatorial solver, at increasing n_total -- real wall
   clock, not a projection dressed as one where it was actually measured.
2. The model (list_batch_deltas + verify_group_sum, as wired into the real narrator) at increasing
   n_total, unfiltered -- looking directly for where accuracy/convergence degrades, not assuming it
   does.
3. The same model, same task, with the magnitude pre-filter (app/narrator/multiway_netting_scale_
   experiment.py's own measured, disclosed-imperfect version) -- a clean one-axis comparison against
   condition 2.

Usage:
    cd backend
    python scripts/generate_multiway_netting_scale_evidence.py
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app.narrator.multiway_netting_scale_experiment import (
    MAGNITUDE_PREFILTER_MULTIPLE,
    build_scale_case,
    run_exhaustive_solver,
    run_llm_condition,
    run_pairwise_rule,
)

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"

RULE_SIZES = [50, 100, 200, 300, 500, 800]
LLM_SIZES = [20, 50, 100, 200, 400, 760]
GROQ_SIZES = [20, 100, 200, 300, 400]  # groq is rate-limited/slower to iterate -- a smaller, targeted sweep
SEEDS_PER_SIZE = 3
GROQ_SEEDS_PER_SIZE = 2


def _rule_and_solver_sweep() -> list[dict]:
    print("--- pairwise rule + exhaustive solver ---")
    entries = []
    for n in RULE_SIZES:
        for seed in range(1, SEEDS_PER_SIZE + 1):
            case = build_scale_case(seed=seed, n_total=n, group_size=3)
            rule = run_pairwise_rule(case)
            solver = run_exhaustive_solver(case, max_group_size=4)
            entries.append(
                {
                    "n_total": n,
                    "seed": seed,
                    "rule_solved": rule.solved,
                    "rule_elapsed_seconds": rule.elapsed_seconds,
                    "solver_found_correct_group": set(solver.found_group_ids) == set(case.group_ids),
                    "solver_combinations_checked": solver.combinations_checked_to_find_it,
                    "solver_elapsed_seconds": solver.seconds_to_find_it,
                }
            )
        avg_solver_secs = sum(e["solver_elapsed_seconds"] for e in entries if e["n_total"] == n) / SEEDS_PER_SIZE
        print(f"  n_total={n}: rule solved=0/{SEEDS_PER_SIZE} (structural), solver avg={avg_solver_secs:.4f}s")
    return entries


def _llm_sweep(provider: str, sizes: list[int] | None = None, seeds_per_size: int | None = None, prefilter_conditions: tuple[bool, ...] = (False, True)) -> list[dict]:
    sizes = sizes if sizes is not None else LLM_SIZES
    seeds_per_size = seeds_per_size if seeds_per_size is not None else SEEDS_PER_SIZE
    print(f"--- {provider}: unfiltered vs. magnitude-prefiltered ---")
    entries = []
    for n in sizes:
        for use_prefilter in prefilter_conditions:
            for seed in range(1, seeds_per_size + 1):
                case = build_scale_case(seed=seed, n_total=n, group_size=3)
                t0 = time.perf_counter()
                r = run_llm_condition(case, provider=provider, use_prefilter=use_prefilter)
                elapsed = time.perf_counter() - t0
                entries.append(
                    {
                        "n_total": n,
                        "use_prefilter": use_prefilter,
                        "seed": seed,
                        "other_transactions_shown_to_model": r.other_transactions_shown_to_model,
                        "correct": r.correctly_identified,
                        "errored": r.errored,
                        "error_message": r.error_message,
                        "elapsed_seconds": elapsed,
                        "raw_response": r.llm_raw_response[:500],
                    }
                )
            label = "prefiltered" if use_prefilter else "unfiltered"
            correct = sum(1 for e in entries if e["n_total"] == n and e["use_prefilter"] == use_prefilter and e["correct"])
            errors = sum(1 for e in entries if e["n_total"] == n and e["use_prefilter"] == use_prefilter and e["errored"])
            print(f"  n_total={n} ({label}): {correct}/{seeds_per_size} correct, {errors} errored")
    return entries


def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        "magnitude_prefilter_multiple": MAGNITUDE_PREFILTER_MULTIPLE,
        "rule_and_solver_sweep": _rule_and_solver_sweep(),
        "ollama_sweep": _llm_sweep("ollama"),
        "groq_sweep": _llm_sweep("groq", sizes=GROQ_SIZES, seeds_per_size=GROQ_SEEDS_PER_SIZE),
    }

    out = EVIDENCE_DIR / f"multiway-netting-scale-experiment-{date.today().isoformat()}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out} (commit this)")


if __name__ == "__main__":
    main()
