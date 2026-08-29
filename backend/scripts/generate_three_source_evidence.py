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

from app.calibration.significance import compare_paired, robustness_p  # noqa: E402
from app.calibration.wilson import wilson_score_interval  # noqa: E402
from app.data_gen.three_source import generate_three_source_batch  # noqa: E402
from app.narrator.preflight import GroqBudgetError, check_groq_budget, check_ollama_available  # noqa: E402
from app.resolver.cycle_reader import model_cycle_agrees  # noqa: E402
from app.resolver.entity_resolution import match_all  # noqa: E402


def cached_model_reader(model: str | None, provider: str = "ollama"):
    """One model call per distinct (cycle, description) pair. Bank descriptions repeat across
    candidates, and re-asking the same question is pure cost -- which matters more on Groq, where the
    free tier rate-limits by tokens per minute."""
    cache: dict[tuple[str, str], bool | None] = {}
    calls = Counter()

    def read(cycle_ref: str, description: str):
        key = (cycle_ref, description)
        if key not in cache:
            cache[key] = model_cycle_agrees(cycle_ref, description, model=model, provider=provider)
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
    # per-settlement correctness, keyed by id. Required for a PAIRED significance test: these columns
    # are run over the identical settlements, so comparing their independent confidence intervals
    # ignores the pairing and is badly conservative -- the three-source intervals overlap while the
    # paired test on the same cases is decisive. Keyed rather than positional so a reorder can never
    # silently mis-pair. See app/calibration/significance.py.
    per_case: dict[str, bool] = {}
    for sid, r in results.items():
        st[r.status] += 1
        want = batch.truth[sid]
        hit = bool(r.best() and r.best().bank_row_id == want)
        per_case[sid] = hit
        if hit:
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
        "per_case": per_case,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-model", action="store_true", help="skip the local model column")
    ap.add_argument("--groq-model", default=None, help="add a hosted column, e.g. openai/gpt-oss-20b")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.groq_model:
        # Roughly 250 distinct (cycle, description) pairs per condition after caching, two conditions.
        try:
            budget = check_groq_budget(estimated_calls=500, model=args.groq_model)
            print(
                f"groq preflight ok: ~{budget['estimated_tokens']:,} tokens needed, "
                f"{budget['tokens_per_minute_remaining']:,} tokens/min headroom",
                flush=True,
            )
        except GroqBudgetError as e:
            raise SystemExit(f"Refusing to start: {e}")

    if not args.no_model:
        check_ollama_available([args.model or "qwen2.5:7b-instruct"])

    conditions = {}
    for label, held_out in (("seen_phrasing", False), ("held_out_phrasing", True)):
        batch = generate_three_source_batch(seed=args.seed, n=args.n, held_out_cycle_phrasing=held_out)
        print(f"\n=== {label} ({len(batch.settlements)} settlements, {len(batch.bank_rows)} bank rows) ===", flush=True)

        columns = {}
        columns["no_cycle_parsing"] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=False))
        columns["regex_cycle_parser"] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True))
        readers = [] if args.no_model else [("ollama", args.model, "ollama")]
        if args.groq_model:
            # a second model FAMILY, not just a second size. Every headline in this project has rested
            # on qwen, which makes "a model reads better than a rule" really "qwen reads better than a
            # rule" -- a narrower claim than the one the docs make.
            readers.append((f"groq_{args.groq_model.split('/')[-1]}", args.groq_model, "groq"))
        for col_name, model_name, provider in readers:
            reader = cached_model_reader(model_name, provider=provider)
            key = "model_cycle_reader" if provider == "ollama" else col_name
            columns[key] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True, cycle_reader=reader))
            columns[key]["model"] = model_name or "qwen2.5:7b-instruct"
            columns[key]["model_calls"] = reader.calls["model"]  # type: ignore[attr-defined]
            columns[key]["lookups"] = reader.calls["total"]  # type: ignore[attr-defined]

        for name, c in columns.items():
            extra = f"   ({c['model_calls']} calls / {c['lookups']} lookups)" if "model_calls" in c else ""
            lo, hi = wilson_score_interval(c["top_candidate_correct"], c["n"])
            print(
                f"  {name:<24} {c['top_candidate_correct']:>4}/{c['n']:<4} = {c['accuracy'] * 100:5.1f}% "
                f"[{lo * 100:.1f}, {hi * 100:.1f}]   UND={c['under_determined']:<3} "
                f"reachable={c['truth_reachable']}/{c['n']}{extra}",
                flush=True,
            )

        # paired significance against the regex parser -- the column every claim is made relative to
        print("  --- paired vs regex_cycle_parser (exact McNemar) ---", flush=True)
        base = columns["regex_cycle_parser"]["per_case"]
        for name, c in columns.items():
            if name == "regex_cycle_parser":
                continue
            cmp = compare_paired(name, c["per_case"], "regex_cycle_parser", base)
            columns[name]["vs_regex"] = {
                "discordant_a": cmp.only_a,
                "discordant_b": cmp.only_b,
                "p_value": round(cmp.p_value, 5),
                "p_value_conceding_2": round(robustness_p(cmp.only_a, cmp.only_b, concede=2), 5),
            }
            print(
                f"    {name:<24} wins {cmp.only_a:>3} / loses {cmp.only_b:<3}  p={cmp.p_value:.4f}"
                f"  (conceding 2: p={robustness_p(cmp.only_a, cmp.only_b, 2):.4f})",
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
