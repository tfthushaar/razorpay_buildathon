"""Cascade routing, measured: accuracy, cost, and latency per RESOLVED transaction at each tier.

Produces docs/evidence/cascade-routing-<date>.json.

Cost per *call* flatters an expensive tier that rarely fires. Cost per *resolution* is what an
operations budget actually experiences, so that is what this reports, alongside how much of the batch
each tier absorbed.

Run on held-out advice phrasing by default. That is deliberate: on phrasing the keyword rule's author
saw, tier 0 absorbs nearly everything and the cascade looks free, which is a real result about a
condition that does not hold in production. The interesting question is what the cascade costs when
the cheap tier meets language nobody anticipated.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.resolver import resolve  # noqa: E402
from app.resolver.cascade import route  # noqa: E402

from scripts.generate_residual_evidence import build_compound_batch, truth_multiset  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seen-phrasing", action="store_true", help="use phrasing the keyword rule's cues were written against")
    ap.add_argument("--tiers", default="qwen2.5:7b-instruct,qwen2.5:14b-instruct")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    held_out = not args.seen_phrasing
    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    _, chains, ctx, truth = build_compound_batch(args.seed, args.n, held_out=held_out)

    absorbed = Counter()
    correct = Counter()
    seconds = Counter()
    rows = []
    residual_n = 0

    for tid, chain in chains.items():
        out = resolve(chain, ctx)
        if out.status != "UNDER_DETERMINED":
            continue
        residual_n += 1
        want = truth_multiset(truth[tid])
        result = route(chain, ctx, out, model_tiers=tiers)
        got = tuple(sorted((c["cause"], c["amount"]) for c in result.components))

        absorbed[result.final_tier] += 1
        if got == want:
            correct[result.final_tier] += 1
        for t in result.tiers_tried:
            seconds[t.tier] += t.seconds

        rows.append(
            {
                "transaction_id": tid,
                "k": result.ambiguity,
                "final_tier": result.final_tier,
                "correct": got == want,
                "total_seconds": result.total_seconds,
                "tiers_tried": [t.model_dump() for t in result.tiers_tried],
            }
        )

    order = ["keyword_rule", *tiers, "human"]
    print(f"=== cascade on {'held-out' if held_out else 'seen'} phrasing (n={residual_n} under-determined cases) ===")
    print(f"{'tier':<24} {'absorbed':>9} {'correct':>9} {'accuracy':>9} {'sec/resolved':>13}")
    summary = {}
    for tier in order:
        n = absorbed[tier]
        acc = correct[tier] / n if n else None
        per = seconds[tier] / n if n else None
        summary[tier] = {
            "absorbed": n,
            "share": round(n / residual_n, 4) if residual_n else 0.0,
            "correct": correct[tier],
            "accuracy": round(acc, 4) if acc is not None else None,
            "total_seconds": round(seconds[tier], 3),
            "seconds_per_resolved": round(per, 3) if per else None,
        }
        print(
            f"{tier:<24} {n:>9} {correct[tier]:>9} "
            f"{(f'{acc * 100:.1f}%' if acc is not None else '-'):>9} "
            f"{(f'{per:.2f}s' if per else '-'):>13}"
        )

    end_to_end_correct = sum(correct.values())
    total_seconds = sum(seconds.values())
    print(
        f"\nend to end: {end_to_end_correct}/{residual_n} = {end_to_end_correct / residual_n * 100:.1f}% "
        f"in {total_seconds:.1f}s total ({total_seconds / residual_n:.2f}s per case)"
    )
    print(f"escalated to a human: {absorbed['human']}/{residual_n}")

    payload = {
        "generated_on": date.today().isoformat(),
        "seed": args.seed,
        "n": args.n,
        "phrasing": "held_out" if held_out else "seen",
        "tiers": list(tiers),
        "residual_n": residual_n,
        "end_to_end_correct": end_to_end_correct,
        "end_to_end_accuracy": round(end_to_end_correct / residual_n, 4) if residual_n else 0.0,
        "total_seconds": round(total_seconds, 3),
        "per_tier": summary,
        "per_case": rows,
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"cascade-routing-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
