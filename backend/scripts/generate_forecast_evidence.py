"""Forecasting measured against the same bar as reconciliation.

Produces docs/evidence/forecast-<date>.json.

The track's bar is throughput, measured accuracy, and an honest exception list. The reconciler meets
all three. The forecaster met one and a half: it reported MAPE and coverage, it predicted every
pending payment with identical confidence, and the coverage figure it quoted was not a confidence at
all but the hit rate of a fixed SLA window.

Three things are measured here, and the second decides whether any of this was worth building.

  1. THE RELIABILITY CURVE. Intervals fitted on one batch, verified on different ones, at nominal
     levels from 50% to 99%. Fitting and scoring on the same data measures memorisation, so they
     never share a batch. A stated 90% that really covers 60% is the same failure as a category that
     auto-resolves without having earned it.

  2. DOES REFUSING HELP. Accuracy on the forecast set AND on the refused set, separately. A refusal
     layer that does not improve what remains is decoration, and if the two are equal this script
     says so rather than reporting only the flattering half.

  3. THROUGHPUT. Predictions per second, which the forecaster had never had measured.

Usage:
    cd backend
    python scripts/generate_forecast_evidence.py [--n 2000] [--seeds 12]
"""

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_gen.generate import generate  # noqa: E402
from app.forecast.calibrated_interval import NOMINAL_LEVELS, fit, reliability_curve  # noqa: E402
from app.forecast.forecastability import assess_batch  # noqa: E402
from app.forecast.predictor import predict_settlement  # noqa: E402


def _score(batch, only):
    """MAPE on net amount and SLA-window coverage, over a chosen subset of a settled batch."""
    order_by_id = {o.order_id: o for o in batch.orders}
    payment_by_id = {p.payment_id: p for p in batch.payments}

    apes, covered, scored = [], 0, 0
    for s in batch.settlements:
        payment = payment_by_id.get(s.payment_id)
        if payment is None:
            continue
        order = order_by_id.get(payment.order_id)
        if order is None:
            continue
        if only is not None and order.order_id not in only:
            continue
        pred = predict_settlement(order, payment)
        if s.settled_amount:
            apes.append(abs(pred.predicted_net_amount - s.settled_amount) / abs(s.settled_amount))
        scored += 1
        if pred.predicted_date_low <= s.settled_at <= pred.predicted_date_high:
            covered += 1
    return {
        "n": scored,
        "mape": round(sum(apes) / len(apes), 6) if apes else None,
        "sla_window_coverage": round(covered / scored, 4) if scored else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000, help="transactions per batch")
    ap.add_argument("--seeds", type=int, default=12, help="held-out batches for the reliability curve")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    # --- fit on one batch, verify on entirely different ones ------------------------------------
    fit_batch, _ = generate(seed=1, main_n=args.n, stress_n=0)
    model = fit(fit_batch)
    print(f"fitted lag quantiles on seed 1, n={args.n}")
    for rail, q in sorted(model.per_rail.items()):
        lo, hi = model.interval_days(rail, 0.90)
        print(f"  {rail:<11} n={q.n:<5} 90% window = {lo:.2f} to {hi:.2f} days")

    holdout_seeds = list(range(100, 100 + args.seeds))
    per_seed = []
    for seed in holdout_seeds:
        holdout, _ = generate(seed=seed, main_n=args.n, stress_n=0)
        per_seed.append(reliability_curve(model, holdout, forecastable_only=True))

    print(f"\n=== reliability curve, {args.seeds} held-out batches (seeds {holdout_seeds[0]}-{holdout_seeds[-1]}) ===")
    print(f"{'nominal':>8} {'empirical':>10} {'gap':>8} {'width (d)':>10}")
    curve = []
    for i, level in enumerate(NOMINAL_LEVELS):
        emp = statistics.mean(c[i].empirical for c in per_seed)
        width = statistics.mean(c[i].mean_width_days for c in per_seed)
        total = sum(c[i].n for c in per_seed)
        curve.append(
            {
                "nominal": level,
                "empirical": round(emp, 4),
                "gap": round(emp - level, 4),
                "mean_width_days": round(width, 2),
                "n": total,
            }
        )
        print(f"{level:>8.2f} {emp:>10.3f} {emp - level:>+8.3f} {width:>10.2f}")
    worst = max(curve, key=lambda p: abs(p["gap"]))
    print(f"largest deviation from nominal: {worst['gap']:+.3f} at the {worst['nominal']:.0%} level")

    # --- does refusing actually help -------------------------------------------------------------
    print("\n=== does refusing improve what remains ===")
    audit, _ = generate(seed=7, main_n=args.n, stress_n=0)
    assessments = assess_batch(audit.orders, audit.payments, audit.refunds)
    accepted = {t for t, a in assessments.items() if a.forecastable}
    refused = {t for t, a in assessments.items() if not a.forecastable}
    reason_counts = {}
    for a in assessments.values():
        for r in a.reasons:
            reason_counts[r] = reason_counts.get(r, 0) + 1

    on_accepted = _score(audit, accepted)
    on_refused = _score(audit, refused)
    on_all = _score(audit, None)
    print(f"  {'':<12} {'n':>6} {'MAPE':>9} {'SLA coverage':>13}")
    for label, s in (("forecast", on_accepted), ("refused", on_refused), ("everything", on_all)):
        mape = f"{s['mape'] * 100:.2f}%" if s["mape"] is not None else "n/a"
        cov = f"{s['sla_window_coverage'] * 100:.1f}%" if s["sla_window_coverage"] is not None else "n/a"
        print(f"  {label:<12} {s['n']:>6} {mape:>9} {cov:>13}")
    share = len(refused) / max(len(assessments), 1)
    print(f"  refused {len(refused)} of {len(assessments)} ({share:.1%}): {reason_counts}")

    improves_mape = (
        on_accepted["mape"] is not None and on_refused["mape"] is not None and on_accepted["mape"] < on_refused["mape"]
    )
    improves_cov = (on_accepted["sla_window_coverage"] or 0) > (on_refused["sla_window_coverage"] or 0)
    if improves_mape and improves_cov:
        verdict = "refusing improves both MAPE and coverage on what remains"
    elif improves_mape or improves_cov:
        verdict = "refusing improves one of MAPE and coverage, not both"
    else:
        verdict = "refusing does NOT improve what remains, so this layer is not earning its place"
    print(f"  verdict: {verdict}")

    # --- throughput -------------------------------------------------------------------------------
    bench, _ = generate(seed=3, main_n=args.n, stress_n=0)
    order_by_id = {o.order_id: o for o in bench.orders}
    pairs = [(order_by_id[p.order_id], p) for p in bench.payments if p.order_id in order_by_id]
    t0 = time.perf_counter()
    for o, p in pairs:
        predict_settlement(o, p)
    elapsed = time.perf_counter() - t0
    tps = round(len(pairs) / elapsed)
    print(f"\nthroughput: {len(pairs):,} predictions in {elapsed:.3f}s = {tps:,} predictions/sec")

    payload = {
        "generated_on": date.today().isoformat(),
        "n_per_batch": args.n,
        "fit_seed": 1,
        "holdout_seeds": holdout_seeds,
        "fitted_90pct_window_days": {r: list(model.interval_days(r, 0.90)) for r in sorted(model.per_rail)},
        "reliability_curve": curve,
        "largest_deviation": worst,
        "refusal": {
            "audit_seed": 7,
            "total": len(assessments),
            "refused": len(refused),
            "refused_share": round(share, 4),
            "reason_counts": reason_counts,
            "on_forecast_set": on_accepted,
            "on_refused_set": on_refused,
            "on_everything": on_all,
            "verdict": verdict,
        },
        "throughput_predictions_per_sec": tps,
    }
    out = (
        Path(args.out)
        if args.out
        else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"forecast-{date.today().isoformat()}.json"
    )
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
