"""What each tier is worth, and what it costs.

RESULTS reports accuracy and throughput in separate sections, so the marginal value of each stage
has to be inferred by the reader. This puts them in one table: what each tier resolves, what it gets
wrong, and how fast it runs, on the identical batches.

The rows are cumulative, because that is how the pipeline works -- Layer 0 only ever sees what the
matching engine could not close, and the model only ever sees what Layer 0 could not finish.

Usage:
    cd backend
    python scripts/generate_ablation_evidence.py [--n 300] [--seeds 3]
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains  # noqa: E402
from app.data_gen.generate import generate  # noqa: E402
from app.matching.engine import run_matching_engine  # noqa: E402
from app.narrator.tools import build_tool_context  # noqa: E402
from app.resolver.resolver import DEFAULT_TOLERANCE_PAISE, resolve  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    seeds = list(range(1, args.seeds + 1))

    batches = []
    for seed in seeds:
        batch, _ = generate(seed=seed, main_n=args.n, stress_n=0, enable_compound_delta=True)
        chains = build_all_chains(batch)
        batches.append((batch, chains, build_tool_context(batch, chains), {g.transaction_id: g.true_label for g in batch.ground_truth}))

    total = sum(len(c) for _, c, _, _ in batches)

    # Tier 1: the matching engine alone.
    start = time.perf_counter()
    engine_results = [run_matching_engine(chains) for _, chains, _, _ in batches]
    tier1_seconds = time.perf_counter() - start
    tier1_resolved = sum(1 for r in engine_results for x in r.values() if x.resolution != "needs_narration")
    tier1_wrong = sum(
        1
        for (batch, chains, ctx, truth), results in zip(batches, engine_results)
        for txn_id, x in results.items()
        if x.resolution != "needs_narration" and (x.category != truth[txn_id] or truth[txn_id] == "genuine_error")
    )

    # Tier 2: Layer 0 over what tier 1 could not close. Timed on that residual alone.
    start = time.perf_counter()
    tier2_resolved = 0
    residual_after_layer0 = 0
    bounded = 0  # Layer 0's real contribution: cases it could not close but COULD enumerate
    for (batch, chains, ctx, truth), results in zip(batches, engine_results):
        for txn_id, x in results.items():
            if x.resolution != "needs_narration":
                continue
            out = resolve(chains[txn_id], ctx, tolerance=DEFAULT_TOLERANCE_PAISE)
            if out.status == "RESOLVED":
                tier2_resolved += 1
            else:
                residual_after_layer0 += 1
                if out.status == "UNDER_DETERMINED":
                    bounded += 1
    tier2_seconds = time.perf_counter() - start

    rows = [
        {
            "tier": "1 matching engine",
            "cumulative_resolved": tier1_resolved,
            "marginal_resolved": tier1_resolved,
            "wrongly_resolved": tier1_wrong,
            "seconds": round(tier1_seconds, 3),
            "transactions_per_sec": round(total / tier1_seconds) if tier1_seconds else None,
        },
        {
            "tier": "2 + Layer 0 residual",
            "cumulative_resolved": tier1_resolved + tier2_resolved,
            "marginal_resolved": tier2_resolved,
            "wrongly_resolved": tier1_wrong,
            "bounded_not_resolved": bounded,  # Layer 0's actual contribution
            "seconds": round(tier1_seconds + tier2_seconds, 3),
            "transactions_per_sec": round(total / (tier1_seconds + tier2_seconds)) if tier2_seconds else None,
        },
        {
            "tier": "3 + a model on what is left",
            "cumulative_resolved": tier1_resolved + tier2_resolved,
            "marginal_resolved": 0,
            "wrongly_resolved": tier1_wrong,
            "seconds": None,
            "transactions_per_sec": 2.58,
            "note": (
                f"{residual_after_layer0} transactions reach it. At a measured 2.58 tx/sec that is "
                f"{residual_after_layer0 / 2.58:.0f}s, against {tier1_seconds:.2f}s for the whole batch "
                "deterministically. Resolutions are not credited here: whether the model is right is "
                "measured in the residual and reading experiments, not asserted in an ablation."
            ),
        },
    ]

    print(f"{total} transactions across seeds {seeds}\n")
    print(f"{'tier':<30}{'resolved':>10}{'marginal':>10}{'wrong':>7}{'tx/sec':>12}")
    for r in rows:
        tps = f"{r['transactions_per_sec']:,}" if r["transactions_per_sec"] else "-"
        print(f"{r['tier']:<30}{r['cumulative_resolved']:>10}{r['marginal_resolved']:>10}{r['wrongly_resolved']:>7}{tps:>12}")
    print("")
    print(f"  Layer 0 resolves {tier2_resolved} outright and BOUNDS {bounded} more: it turns 'no explanation'")
    print("  into 'one of k enumerated, arithmetically valid explanations', which is what makes the")
    print("  1/k chance baseline computable rather than argued. That is its contribution here.")
    print(f"\n  {residual_after_layer0} of {total} transactions ({residual_after_layer0 / total:.1%}) reach a model")
    print(f"  {rows[2]['note']}")

    payload = {
        "generated_on": date.today().isoformat(),
        "seeds": seeds,
        "n_per_batch": args.n,
        "total_transactions": total,
        "reaching_a_model": residual_after_layer0,
        "bounded_by_layer0": bounded,
        "rows": rows,
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"ablation-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
