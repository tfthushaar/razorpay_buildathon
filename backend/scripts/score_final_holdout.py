"""Score the untouched holdout, exactly once.

Every held-out figure in RESULTS was re-measured across passes; the numbers that survived are the
ones I kept. This scores seeds no experiment has ever touched, once, and whatever comes out is what
ships. Re-running raises rather than overwriting -- see app/final_holdout.py.

Usage:
    cd backend
    python scripts/score_final_holdout.py [--only reading|three_source]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.final_holdout import FINAL_SEEDS, HoldoutAlreadyScored, claim  # noqa: E402


def score_three_source(seed: int) -> dict:
    from app.data_gen.three_source import generate_three_source_batch
    from app.resolver.entity_resolution import match_all
    from app.resolver.fellegi_sunter import fit_weights, match_all_weighted

    out = {}
    for condition, held_out in (("seen", False), ("held_out", True)):
        batch = generate_three_source_batch(seed=seed, n=120, held_out_cycle_phrasing=held_out)
        fit = generate_three_source_batch(seed=seed + 100, n=120, held_out_cycle_phrasing=held_out)
        weights = fit_weights(fit.settlements, fit.bank_rows, fit.truth)

        columns = {
            "no_cycle_parsing": match_all(batch.settlements, batch.bank_rows, use_cycle_ref=False),
            "estimated_weights_no_cycle": match_all_weighted(batch.settlements, batch.bank_rows, weights),
            "regex_cycle_parser": match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True),
        }
        out[condition] = {
            name: {
                "correct": sum(1 for sid, r in res.items() if r.best() and r.best().bank_row_id == batch.truth[sid]),
                "total": len(batch.settlements),
            }
            for name, res in columns.items()
        }
    return out


def score_reading(seed: int) -> dict:
    """The keyword rule alone. The model columns need a provider and a quota; the rule is the one
    that carries the generalisation claim, and it is deterministic."""
    from scripts.generate_reading_evidence import CAUSES, build, rule_read, score

    out = {}
    for condition, held_out in (("seen", False), ("held_out", True)):
        chains, truth = build(seed, 60, held_out)
        scored = score(chains, truth, {"keyword_rule": rule_read})
        row = scored["keyword_rule"]
        out[condition] = {
            "correct": row["correct"],
            "total": row["total"],
            "accuracy": row["accuracy"],
            "dangerous_errors": row["dangerous_errors"],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    jobs = {"reading": score_reading, "three_source": score_three_source}
    if args.only:
        jobs = {args.only: jobs[args.only]}

    for name, fn in jobs.items():
        seed = FINAL_SEEDS[name]
        print(f"\n=== {name}, seed {seed}, scored once ===")
        try:
            payload = fn(seed)
        except Exception as e:  # noqa: BLE001
            print(f"  could not score: {type(e).__name__}: {e}")
            continue
        try:
            path = claim(name, payload)
        except HoldoutAlreadyScored as e:
            print(f"  REFUSED: {e}")
            continue
        print(f"  {payload}")
        print(f"  wrote {path}")


if __name__ == "__main__":
    main()
