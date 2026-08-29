"""Measure the residual architecture end to end, against every baseline that could beat it.

Produces docs/evidence/residual-architecture-<date>.json. Nothing in docs/ should quote a number
about the residual that this script did not produce.

What it measures, and why each piece is here:

  1. LAYER 0 CORRECTNESS. Before any accuracy claim means anything, the resolver has to actually
     recover the true decomposition into its candidate set. If it doesn't, "the model chose wrong"
     and "the right answer was never on the table" are indistinguishable. Asserted, not assumed --
     this check is what caught a wrong fee base that had made the pool look full of plausible numbers
     while never containing the true ones.

  2. THE AMBIGUITY-VS-TOLERANCE CURVE, including the row that is worst for the architecture: zero
     rounding noise, zero tolerance, exact integer arithmetic. If compositionality alone were not
     enough to make this under-determined, that row would show it and the whole design would be
     resting on a tolerance knob.

  3. FOUR COLUMNS ON THE RESIDUAL, all choosing from the identical shuffled option list:
       chance         exactly 1/k. Computed, not argued.
       parsimony      always take the fewest-component explanation. Occam, free, and strong.
       keyword rule   the best rule I could write: fragment splitting, cause keywords, and a
                      negation-cue list built with full sight of the generator's own phrasing.
       model          the LLM.
     A column is not omitted because it wins.

  4. PER-CAUSE ATTRIBUTION ACCURACY, which is what calibration actually gates on -- trust is granted
     and revoked per cause, not per transaction.

Groq is opt-in (--with-groq) because this project's free-tier daily token quota has been exhausted by
a sweep before, and a quota failure mid-run is indistinguishable from a capability finding unless you
go and read the error text.
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains  # noqa: E402
from app.data_gen.generate import SyntheticDataGenerator  # noqa: E402
from app.data_gen.schemas import SyntheticBatch  # noqa: E402
from app.narrator.attribution import OPTION_WINDOW, attribute  # noqa: E402
from app.narrator.tools import build_tool_context  # noqa: E402
from app.resolver import resolve  # noqa: E402
from app.resolver.enumerate import build_candidate_pool  # noqa: E402
from app.resolver.keyword_baseline import best_decomposition_by_advice  # noqa: E402
from app.resolver.resolver import DEFAULT_TOLERANCE_PAISE, most_parsimonious, present_options  # noqa: E402


def build_compound_batch(seed: int, n: int, rounding_noise: int = 3, held_out: bool = False):
    g = SyntheticDataGenerator(seed=seed)
    parts = [g._gen_compound_delta(rounding_noise=rounding_noise, held_out_phrasing=held_out) for _ in range(n)]
    o, p, r, s, l, gt = [], [], [], [], [], []
    for a, b, c, d, e, f in parts:
        o += a
        p += b
        r += c
        s += d
        l += e
        gt += f
    batch = SyntheticBatch(orders=o, payments=p, refunds=r, settlements=s, ledger_entries=l, ground_truth=gt)
    chains = build_all_chains(batch)
    return batch, chains, build_tool_context(batch, chains), {e.transaction_id: e for e in gt}


def truth_multiset(entry):
    return tuple(sorted((c.cause, c.amount) for c in entry.true_causes))


def measure_layer0(seed: int, n: int) -> dict:
    """Section 1 + 2: does Layer 0 recover the truth, and where does the ambiguity come from."""
    rows = []
    for noise, tolerances in ((0, [0, 5, 10, 25]), (3, [0, 5, 10, 25, 50])):
        _, chains, ctx, truth = build_compound_batch(seed, n, rounding_noise=noise)
        for tol in tolerances:
            status = Counter()
            ks, recovered = [], 0
            for tid, chain in chains.items():
                out = resolve(chain, ctx, tolerance=tol)
                status[out.status] += 1
                if out.status == "UNDER_DETERMINED":
                    ks.append(out.ambiguity)
                if any(d.cause_multiset() == truth_multiset(truth[tid]) for d in out.decompositions):
                    recovered += 1
            rows.append(
                {
                    "rounding_noise": noise,
                    "tolerance": tol,
                    "resolved": status["RESOLVED"],
                    "under_determined": status["UNDER_DETERMINED"],
                    "unmatched": status["UNMATCHED"],
                    "median_k": statistics.median(ks) if ks else None,
                    "mean_k": round(statistics.mean(ks), 1) if ks else None,
                    "max_k": max(ks) if ks else None,
                    "true_decomposition_recovered": recovered,
                    "n": len(chains),
                }
            )
    return {"ambiguity_vs_tolerance": rows}


def measure_baselines(seed: int, n: int, providers: list[str], model: str | None, held_out: bool = False) -> dict:
    _, chains, ctx, truth = build_compound_batch(seed, n, held_out=held_out)

    per_case = []
    per_cause_hits: dict[str, Counter] = defaultdict(Counter)
    totals = {name: 0 for name in ("chance", "parsimony", "keyword", *providers)}
    chance_sum = 0.0
    residual_n = 0
    truth_outside_window = 0
    verified_counts = Counter()
    rounds_used = Counter()
    elapsed = Counter()

    for tid, chain in chains.items():
        out = resolve(chain, ctx)
        if out.status != "UNDER_DETERMINED":
            continue
        residual_n += 1
        want = truth_multiset(truth[tid])
        options = present_options(out.decompositions, tid, limit=OPTION_WINDOW)
        if not any(d.cause_multiset() == want for d in options):
            truth_outside_window += 1

        chance_sum += out.chance_baseline
        row = {"transaction_id": tid, "k": out.ambiguity, "shown": len(options), "chance": round(out.chance_baseline, 4)}

        pars = most_parsimonious(out.decompositions)
        if pars is not None and pars.cause_multiset() == want:
            totals["parsimony"] += 1
            row["parsimony"] = True
        else:
            row["parsimony"] = False

        best, tied = best_decomposition_by_advice(options, chain.bank_narration)
        kw_ok = best is not None and best.cause_multiset() == want
        totals["keyword"] += kw_ok
        row["keyword"] = kw_ok
        row["keyword_ties"] = tied

        pool = build_candidate_pool(chain, ctx)
        for provider in providers:
            t0 = time.monotonic()
            result = attribute(chain, ctx, out, pool, provider=provider, model=model)
            elapsed[provider] += time.monotonic() - t0
            ok = result.cause_multiset == want
            totals[provider] += ok
            verified_counts[provider] += result.verified
            rounds_used[provider] += result.verify_rounds_used
            row[provider] = ok
            row[f"{provider}_verified"] = result.verified
            row[f"{provider}_rounds"] = result.verify_rounds_used
            # per-cause credit: of the causes the model named, how many were genuinely in the truth
            true_causes = {(c.cause, c.amount) for c in truth[tid].true_causes}
            for c in result.components:
                per_cause_hits[c.cause]["proposed"] += 1
                if (c.cause, c.amount) in true_causes:
                    per_cause_hits[c.cause]["correct"] += 1
            for cause, amount in true_causes:
                per_cause_hits[cause]["actual"] += 1

        per_case.append(row)

    summary = {
        "residual_n": residual_n,
        "truth_outside_option_window": truth_outside_window,
        "option_window": OPTION_WINDOW,
        "mean_chance_baseline": round(chance_sum / residual_n, 4) if residual_n else 0.0,
        "columns": {},
    }
    for name in ("parsimony", "keyword", *providers):
        summary["columns"][name] = {
            "correct": totals[name],
            "n": residual_n,
            "accuracy": round(totals[name] / residual_n, 4) if residual_n else 0.0,
        }
        if name in providers:
            summary["columns"][name]["verified"] = verified_counts[name]
            summary["columns"][name]["mean_verify_rounds"] = round(rounds_used[name] / residual_n, 2) if residual_n else 0
            summary["columns"][name]["mean_seconds_per_case"] = round(elapsed[name] / residual_n, 2) if residual_n else 0

    per_cause = {
        cause: {
            "proposed": c["proposed"],
            "correct": c["correct"],
            "actual_occurrences": c["actual"],
            "precision": round(c["correct"] / c["proposed"], 4) if c["proposed"] else None,
            "recall": round(c["correct"] / c["actual"], 4) if c["actual"] else None,
        }
        for cause, c in sorted(per_cause_hits.items())
    }

    return {"summary": summary, "per_cause": per_cause, "per_case": per_case}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--layer0-n", type=int, default=120)
    ap.add_argument("--with-ollama", action="store_true", default=True)
    ap.add_argument("--no-ollama", dest="with_ollama", action="store_false")
    ap.add_argument("--with-groq", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    providers = []
    if args.with_ollama:
        # BOTH shapes of the model column, because they answer different questions and the weaker one
        # is not dropped for being weaker. "ollama" hands the model the whole option list and asks it
        # to choose; "ollama_reader" has it read only the advice and lets the deterministic scorer do
        # the matching, which is the division of labour the architecture actually implies.
        providers += ["ollama", "ollama_reader"]
    if args.with_groq:
        providers += ["groq", "groq_reader"]

    print(f"Layer 0: ambiguity vs tolerance (n={args.layer0_n})...", flush=True)
    layer0 = measure_layer0(args.seed, args.layer0_n)
    for row in layer0["ambiguity_vs_tolerance"]:
        print(
            f"  noise={row['rounding_noise']:<2} tol={row['tolerance']:<3} "
            f"RES={row['resolved']:<3} UND={row['under_determined']:<3} UNM={row['unmatched']:<3} "
            f"med_k={str(row['median_k']):<6} truth_recovered={row['true_decomposition_recovered']}/{row['n']}",
            flush=True,
        )

    print(f"\nBaselines on the residual (n={args.n}, providers={providers or ['none']})...", flush=True)
    baselines = measure_baselines(args.seed, args.n, providers, args.model)
    s = baselines["summary"]
    print(f"  residual cases: {s['residual_n']}  (truth outside the {s['option_window']}-option window: {s['truth_outside_option_window']})")
    print(f"  chance (mean 1/k):  {s['mean_chance_baseline'] * 100:.1f}%")
    for name, col in s["columns"].items():
        extra = ""
        if "verified" in col:
            extra = f"   verified={col['verified']}/{col['n']}  {col['mean_seconds_per_case']}s/case"
        print(f"  {name:<10} {col['correct']:>4}/{col['n']:<4} = {col['accuracy'] * 100:5.1f}%{extra}")

    payload = {
        "generated_on": date.today().isoformat(),
        "seed": args.seed,
        "n": args.n,
        "tolerance": DEFAULT_TOLERANCE_PAISE,
        "model": args.model,
        **layer0,
        **baselines,
    }
    out_path = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"residual-architecture-{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
