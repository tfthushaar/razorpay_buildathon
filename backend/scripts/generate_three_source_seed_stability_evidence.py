"""Does the three-source reading result survive a different draw of the data?

Produces docs/evidence/three-source-seed-stability-<date>.json.

Every model column in this project is scored on one batch at one seed, and the headline was an exact
McNemar p = 0.049 -- significant, and one discordant case away from not being. A sweep of the
deterministic columns across ten seeds showed the case set moving between 129 and 138 correct out of
150, a spread of nine, which is far larger than the margin that p-value rests on. A result that thin
sitting on a draw that wide is a result that has not been shown to exist.

So this re-runs the comparison that matters -- the model reader against the best regex I could write,
on held-out phrasing -- across several independent draws, and reports the distribution rather than
the friendliest member of it. Ollama only by default: this needs one full run per seed, and spending
hosted quota to discover a result is unstable is a poor trade.

Two things are published whatever they say:

    per-seed wins and losses      does the direction hold, or does it flip with the draw?
    pooled McNemar over all seeds the same test with the draw-to-draw noise averaged out

If the direction flips across seeds, the honest reading is that the single-seed p-value was noise,
and the committed table has to say so.

Usage:
    cd backend
    python scripts/generate_three_source_seed_stability_evidence.py --seeds 42,1,7,100,202
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calibration.significance import compare_paired, exact_mcnemar_p, robustness_p  # noqa: E402
from app.data_gen.three_source import generate_three_source_batch  # noqa: E402
from app.narrator.preflight import check_ollama_available  # noqa: E402
from app.resolver.entity_resolution import match_all  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_three_source_evidence import cached_model_reader  # noqa: E402


def correctness(batch, results) -> dict[str, bool]:
    return {s: bool(r.best() and r.best().bank_row_id == batch.truth[s]) for s, r in results.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="42,1,7,100,202")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--provider", default="ollama")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    if args.provider == "ollama":
        check_ollama_available([args.model])

    rows = []
    pooled_only_model = pooled_only_regex = pooled_n = 0

    print(f"{args.model} on held-out phrasing, {len(seeds)} independent draws\n")
    print(f"{'seed':>9}{'no-parse':>10}{'regex':>8}{'model':>8}{'W':>4}{'L':>4}{'p':>9}")
    for seed in seeds:
        batch = generate_three_source_batch(seed=seed, n=args.n, held_out_cycle_phrasing=True)
        reader = cached_model_reader(args.model, provider=args.provider)

        no_parse = correctness(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=False))
        regex = correctness(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True))
        model = correctness(batch, match_all(batch.settlements, batch.bank_rows, use_cycle_ref=True, cycle_reader=reader))

        cmp = compare_paired("regex_cycle_parser", regex, "model_cycle_reader", model)
        pooled_only_model += cmp.only_b
        pooled_only_regex += cmp.only_a
        pooled_n += cmp.n

        n = len(regex)
        print(f"{seed:>9}{sum(no_parse.values()):>7}/{n}{sum(regex.values()):>5}/{n}"
              f"{sum(model.values()):>5}/{n}{cmp.only_b:>4}{cmp.only_a:>4}{cmp.p_value:>9.4f}")
        rows.append({
            "seed": seed,
            "n": n,
            "no_cycle_parsing": sum(no_parse.values()),
            "regex_cycle_parser": sum(regex.values()),
            "model_cycle_reader": sum(model.values()),
            "model_wins": cmp.only_b,
            "model_loses": cmp.only_a,
            "p_value": cmp.p_value,
            "model_calls": reader.calls["model"],
        })

    pooled_p = exact_mcnemar_p(pooled_only_regex, pooled_only_model)
    pooled_robust = robustness_p(pooled_only_regex, pooled_only_model, concede=2)
    flipped = sum(1 for r in rows if r["model_loses"] > r["model_wins"])
    tied = sum(1 for r in rows if r["model_loses"] == r["model_wins"])

    print(f"\npooled over {pooled_n} settlements: model wins {pooled_only_model}, "
          f"loses {pooled_only_regex}, p = {pooled_p:.4f} (conceding 2: {pooled_robust:.4f})")
    print(f"seeds where the model did not beat the regex: {flipped + tied}/{len(rows)} "
          f"({flipped} lost, {tied} tied)")
    print(f"significant at 0.05 on their own: {sum(1 for r in rows if r['p_value'] < 0.05)}/{len(rows)} seeds")

    payload = {
        "generated_on": date.today().isoformat(),
        "model": args.model,
        "provider": args.provider,
        "condition": "held_out_phrasing",
        "n_per_seed": args.n,
        "seeds": seeds,
        "per_seed": rows,
        "pooled": {
            "n": pooled_n,
            "model_wins": pooled_only_model,
            "model_loses": pooled_only_regex,
            "p_value": pooled_p,
            "p_value_conceding_2": pooled_robust,
        },
        "seeds_not_won": flipped + tied,
        "seeds_significant_alone": sum(1 for r in rows if r["p_value"] < 0.05),
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"three-source-seed-stability-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
