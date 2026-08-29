"""Three-source reconciliation: does the residual pattern hold on a completely different problem?

Produces docs/evidence/three-source-2026-08-29.json.

Everything else in this project reconciles gateway data against itself, so the join is trivial and all
the difficulty sits in the arithmetic. This is the other half of real reconciliation: a settlement
report, a bank statement, and an ERP ledger that never agreed, joined on nothing reliable. It exists
as a check on the residual argument itself — if under-determination only ever showed up in compound
settlement arithmetic, a reader would be right to suspect the arithmetic was built to produce it.

Four columns, everything else in the matcher held identical between them:

  no cycle parsing    UTR suffix matching + amount tolerance + date window + name similarity
  regex cycle parser  the above, plus the best cycle extractor I could write
  model cycle reader  the above, with a model doing the reading instead of the regex
  chance              1/k over whatever the matcher left tied

Both phrasing conditions are run, and the held-out one is the honest one: my cycle regexes were
written against the generator's own house styles, which is the same authorship problem this project
has been caught by before.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_gen.three_source import generate_three_source_batch  # noqa: E402
from app.resolver.cycle_reader import model_cycle_agrees  # noqa: E402
from app.resolver.entity_resolution import match_all  # noqa: E402


def cached_model_reader(model: str | None):
    """One model call per distinct (cycle, description) pair. Bank descriptions repeat across
    candidates, and re-asking the same question is pure cost."""
    cache: dict[tuple[str, str], bool | None] = {}
    calls = Counter()

    def read(cycle_ref: str, description: str):
        key = (cycle_ref, description)
        if key not in cache:
            cache[key] = model_cycle_agrees(cycle_ref, description, model=model)
            calls["model"] += 1
        calls["total"] += 1
        return cache[key]

    read.calls = calls  # type: ignore[attr-defined]
    return read


def score(batch, results) -> dict:
    st = Counter()
    top_correct = reachable = 0
    ks: list[int] = []
    chance = 0.0
    for sid, r in results.items():
        st[r.status] += 1
        want = batch.truth[sid]
        if r.best() and r.best().bank_row_id == want:
            top_correct += 1
        if r.reachable(want):
            reachable += 1
        if r.status == "UNDER_DETERMINED":
            ks.append(r.ambiguity)
            chance += r.chance_baseline
    n = len(results)
    return {
        "n": n,
        "top_candidate_correct": top_correct,
        "accuracy": round(top_correct / n, 4) if n else 0.0,
        "truth_reachable": reachable,
        "resolved": st["RESOLVED"],
        "under_determined": st["UNDER_DETERMINED"],
        "unmatched": st["UNMATCHED"],
        "mean_chance_on_under_determined": round(chance / len(ks), 4) if ks else None,
        "max_k": max(ks) if ks else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-model", action="store_true", help="skip the model column")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    conditions = {}
    for label, held_out in (("seen_phrasing", False), ("held_out_phrasing", True)):
        batch = generate_three_source_batch(seed=args.seed, n=args.n, held_out_cycle_phrasing=held_out)
        print(f"\n=== {label} ({len(batch.settlements)} settlements, {len(batch.bank_rows)} bank rows) ===", flush=True)

        columns = {}
        columns["no_cycle_parsing"] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=False))
        columns["regex_cycle_parser"] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True))
        if not args.no_model:
            reader = cached_model_reader(args.model)
            columns["model_cycle_reader"] = score(
                batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True, cycle_reader=reader)
            )
            columns["model_cycle_reader"]["model_calls"] = reader.calls["model"]  # type: ignore[attr-defined]
            columns["model_cycle_reader"]["lookups"] = reader.calls["total"]  # type: ignore[attr-defined]

        for name, c in columns.items():
            extra = f"   ({c['model_calls']} model calls for {c['lookups']} lookups)" if "model_calls" in c else ""
            print(
                f"  {name:<20} {c['top_candidate_correct']:>4}/{c['n']:<4} = {c['accuracy'] * 100:5.1f}%   "
                f"RES={c['resolved']:<4} UND={c['under_determined']:<3} UNM={c['unmatched']:<3} "
                f"reachable={c['truth_reachable']}/{c['n']}{extra}",
                flush=True,
            )
        conditions[label] = columns

    print("\n=== generalisation gap (seen -> held-out) ===")
    gaps = {}
    for name in conditions["seen_phrasing"]:
        a = conditions["seen_phrasing"][name]["accuracy"]
        b = conditions["held_out_phrasing"][name]["accuracy"]
        gaps[name] = round(b - a, 4)
        print(f"  {name:<20} {a * 100:5.1f}% -> {b * 100:5.1f}%   ({(b - a) * 100:+.1f} pts)")

    payload = {
        "generated_on": date.today().isoformat(),
        "seed": args.seed,
        "n": args.n,
        "model": args.model,
        "conditions": conditions,
        "generalisation_gap": gaps,
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"three-source-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
