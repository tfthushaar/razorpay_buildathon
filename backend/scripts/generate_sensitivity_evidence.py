"""What the tuning actually buys, measured instead of asserted.

RESULTS says the system is built to escalate rather than guess. That is a claim about constants
nobody has been shown. This sweeps each one across its usable range, changing one value and nothing
else, and reports where a wrong resolution first appears.

The number worth arguing about is the distance between the shipped value and that boundary. A knob
with a wide margin is not what makes the system safe; a knob with none is what would make it unsafe.
Either way the reader gets the curve rather than the assurance.

Sweeping can disprove things this project currently claims. If a constant turns out not to govern
correctness at all, that is the finding and it goes in RESULTS whether it flatters the system or not.

Usage:
    cd backend
    python scripts/generate_sensitivity_evidence.py [--n 300] [--seeds 3]
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains  # noqa: E402
from app.data_gen.generate import generate  # noqa: E402
from app.matching import engine as engine_module  # noqa: E402
from app.matching.engine import run_matching_engine  # noqa: E402
from app.narrator.tools import build_tool_context  # noqa: E402
from app.resolver import resolver as resolver_module  # noqa: E402


def _score(seeds: list[int], n: int) -> dict:
    """Resolved, and wrongly resolved, over several batches at the current constants."""
    resolved = wrong = total = 0
    for seed in seeds:
        batch, _ = generate(seed=seed, main_n=n, stress_n=0)
        truth = {g.transaction_id: g.true_label for g in batch.ground_truth}
        for txn_id, result in run_matching_engine(build_all_chains(batch)).items():
            total += 1
            if result.resolution == "needs_narration":
                continue
            resolved += 1
            # The engine resolved it. Wrong if the category disagrees with the answer key, or if it
            # closed something the policy never permits closing.
            if result.category != truth[txn_id] or truth[txn_id] == "genuine_error":
                wrong += 1
    return {"total": total, "resolved": resolved, "wrongly_resolved": wrong}


def _score_resolver(seeds: list[int], n: int) -> dict:
    """Layer 0 over the exceptions the matching engine could not close.

    A separate scorer because DEFAULT_TOLERANCE_PAISE lives in the resolver, which the matching
    engine never calls. A first version of this script swept it against `_score` and reported that
    it "does not govern correctness", which was true only because the constant was not on the code
    path being measured. Verified directly: setting it to 99999 leaves every matching-engine
    resolution byte-identical.
    """
    resolved = under = unmatched = total = 0
    for seed in seeds:
        batch, _ = generate(seed=seed, main_n=n, stress_n=0, enable_compound_delta=True)
        chains = build_all_chains(batch)
        context = build_tool_context(batch, chains)
        results = run_matching_engine(chains)
        for txn_id, result in results.items():
            if result.resolution != "needs_narration":
                continue
            total += 1
            out = resolver_module.resolve(chains[txn_id], context, tolerance=resolver_module.DEFAULT_TOLERANCE_PAISE)
            if out.status == "RESOLVED":
                resolved += 1
            elif out.status == "UNDER_DETERMINED":
                under += 1
            else:
                unmatched += 1
    return {"total": total, "resolved": resolved, "under_determined": under, "unmatched": unmatched}


def sweep(module, name: str, values: list, seeds: list[int], n: int, scorer=None) -> dict:
    """Set one constant at a time, restoring it afterwards."""
    original = getattr(module, name)
    rows = []
    try:
        for value in values:
            setattr(module, name, value)
            row = (scorer or _score)(seeds, n)
            row["value"] = value
            row["shipped"] = value == original
            rows.append(row)
    finally:
        setattr(module, name, original)

    breaks_at = next((r["value"] for r in rows if r.get("wrongly_resolved", 0) > 0), None)
    return {
        "constant": name,
        "shipped_value": original,
        "first_wrong_resolution_at": breaks_at,
        "governs_correctness": breaks_at is not None,
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    seeds = list(range(1, args.seeds + 1))

    # ROUNDING_EPSILON is the matcher's own correctness knob: the delta it will call FX noise and
    # close without further evidence. It is imported by value into engine.py, so the sweep patches
    # the name the engine actually reads rather than its definition in chain/builder.py.
    sweeps = [
        sweep(engine_module, "ROUNDING_EPSILON", [0, 1, 3, 5, 10, 50, 100, 200, 1000, 5000], seeds, args.n),
        sweep(resolver_module, "DEFAULT_TOLERANCE_PAISE", [0, 5, 10, 25, 100, 500], seeds, args.n, scorer=_score_resolver),
    ]

    for s in sweeps:
        print(f"\n=== {s['constant']}  (shipped: {s['shipped_value']}) ===")
        wrong_col = "wrongly_resolved" in s["rows"][0]
        header = "wrongly resolved" if wrong_col else "under-determined"
        print(f"  {'value':>8} {'resolved':>10} {header:>18}")
        for row in s["rows"]:
            mark = "  <- shipped" if row["shipped"] else ""
            other = row["wrongly_resolved"] if wrong_col else row["under_determined"]
            print(f"  {row['value']:>8} {row['resolved']:>10} {other:>18}{mark}")
        if not wrong_col:
            print("  scored on Layer 0's own output; this knob trades resolutions against admitted ambiguity")
        elif s["first_wrong_resolution_at"] is None:
            print("  never posts a wrong resolution across this range: it does not govern correctness here")
        else:
            margin = s["first_wrong_resolution_at"] / s["shipped_value"] if s["shipped_value"] else float("inf")
            print(f"  first wrong resolution at {s['first_wrong_resolution_at']}, a {margin:.0f}x margin on the shipped value")

    payload = {
        "generated_on": date.today().isoformat(),
        "seeds": seeds,
        "n_per_batch": args.n,
        "sweeps": sweeps,
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"sensitivity-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
