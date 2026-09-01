"""The three-source cycle reader, on a second model family.

Produces docs/evidence/three-source-second-family-<date>.json.

LIMITATIONS records that only the reading experiment has a second family behind it; three-source,
the compound residual and the Q&A agent are all measured on qwen alone. This closes it for
three-source, which is the strongest technical result in the project.

HELD-OUT PHRASING ONLY, and that is a budget decision rather than a preference. Groq's free tier
allows 200,000 tokens a day; a real cycle-reading call costs about 500, and both conditions at n=120
need roughly 500 calls, so scoring both would need 250,000 and could not finish. Running it anyway
is how two days of quota were already spent. Held-out is the condition the finding rests on -- on
seen phrasing the regex wins and nothing about a model column there would change the argument.

n stays at 120 so this is directly comparable to the committed table rather than a new scope. The
deterministic columns are re-scored in the same run for exactly that reason: if `no_cycle_parsing`
and `regex_cycle_parser` do not reproduce their published values, the model column is not comparable
either and this script says so instead of publishing it.

Usage:
    cd backend
    python scripts/generate_three_source_second_family_evidence.py [--model openai/gpt-oss-20b]
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calibration.significance import exact_mcnemar_p, robustness_p  # noqa: E402
from app.data_gen.three_source import generate_three_source_batch  # noqa: E402
from app.narrator.preflight import check_groq_budget  # noqa: E402
from app.resolver.cycle_reader import _pacer, model_cycle_agrees  # noqa: E402
from app.resolver.entity_resolution import match_all  # noqa: E402

# What the committed n=120 held-out run reports. The deterministic columns must reproduce these or
# the model column scored a different case set and is not comparable.
# The deterministic held-out columns for this seed. Was 132/132 before Razorpay's documented
# narration format joined the generator's house styles and the cosmetic bank-name draw was moved off
# the main RNG stream; both changed which batch seed 42 produces. If a model column is scored against
# a case set where these do not reproduce, it is not comparable to the others in the table, and the
# run aborts rather than publishing a number that looks like the others but is not.
PUBLISHED_HELD_OUT = {"no_cycle_parsing": 128, "regex_cycle_parser": 128}


def score(batch, results) -> dict:
    per_case = {}
    for sid, r in results.items():
        per_case[sid] = bool(r.best() and r.best().bank_row_id == batch.truth[sid])
    correct = sum(per_case.values())
    return {
        "correct": correct,
        "total": len(batch.settlements),
        "accuracy": round(correct / len(batch.settlements), 4),
        "under_determined": sum(1 for r in results.values() if r.status == "UNDER_DETERMINED"),
        "per_case": per_case,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-oss-20b")
    ap.add_argument("--provider", default=None, help="groq or ollama; inferred from the model name if omitted")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    provider = args.provider or ("groq" if "/" in args.model else "ollama")
    batch = generate_three_source_batch(seed=args.seed, n=args.n, held_out_cycle_phrasing=True)
    estimated_calls = len(batch.settlements) * 2

    if provider == "groq":
        budget = check_groq_budget(estimated_calls=estimated_calls, model=args.model)
        print(f"preflight ok: ~{budget['estimated_tokens']:,} tokens for ~{estimated_calls} calls")
        print(f"paced at {60 / _pacer().min_interval:.1f} calls/min, so roughly "
              f"{_pacer().estimate_seconds(estimated_calls) / 60:.0f} minutes")

    columns = {}
    columns["no_cycle_parsing"] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=False))
    columns["regex_cycle_parser"] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True))

    # Comparability gate, before spending any quota on the model column.
    for name, expected in PUBLISHED_HELD_OUT.items():
        got = columns[name]["correct"]
        if got != expected:
            raise SystemExit(
                f"REFUSING TO RUN: {name} scored {got}, the committed table says {expected}. "
                "The case set differs, so a model column here would not be comparable."
            )
    print("deterministic columns reproduce the committed table exactly; the model column is comparable\n")

    calls = {"n": 0}

    def reader(cycle_ref, description):
        calls["n"] += 1
        if calls["n"] % 25 == 0:
            print(f"  {calls['n']} calls", flush=True)
        return model_cycle_agrees(cycle_ref, description, model=args.model, provider=provider)

    columns[args.model] = score(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True, cycle_reader=reader))

    base = columns["regex_cycle_parser"]["per_case"]
    paired = {}
    for name, col in columns.items():
        if name == "regex_cycle_parser":
            continue
        wins = sum(1 for sid, hit in col["per_case"].items() if hit and not base[sid])
        losses = sum(1 for sid, hit in col["per_case"].items() if not hit and base[sid])
        paired[name] = {"wins": wins, "losses": losses, "p": round(exact_mcnemar_p(wins, losses), 4),
                        "p_conceding_two": round(robustness_p(wins, losses), 4)}

    print(f"\n=== held-out phrasing, {len(batch.settlements)} settlements, seed {args.seed} ===")
    for name, col in columns.items():
        line = f"  {name:<26} {col['correct']}/{col['total']} = {col['accuracy']:.1%}  UND={col['under_determined']}"
        if name in paired:
            p = paired[name]
            line += f"   vs regex: {p['wins']}W/{p['losses']}L p={p['p']}"
        print(line)

    identical = columns[args.model]["correct"] == columns["no_cycle_parsing"]["correct"]
    print(f"\n  model calls: {calls['n']}, pacing: {_pacer().summary()}")
    if identical:
        print("  WARNING: the model column scored identically to no_cycle_parsing. That is the "
              "signature of a reader returning None on every call, and this project has published "
              "that mistake once already. Check before believing this row.")

    payload = {
        "generated_on": date.today().isoformat(),
        "model": args.model,
        "provider": provider,
        "seed": args.seed,
        "condition": "held_out_phrasing",
        "why_one_condition": "Groq's free tier allows 200,000 tokens a day; both conditions need ~250,000",
        "n_settlements": len(batch.settlements),
        "model_calls": calls["n"],
        "columns": {k: {kk: vv for kk, vv in v.items() if kk != "per_case"} for k, v in columns.items()},
        "paired_vs_regex": paired,
        "identical_to_baseline": identical,
        "pacing": _pacer().summary(),
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"three-source-second-family-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
