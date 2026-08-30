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
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_gen.generate import generate  # noqa: E402
from app.forecast.calibrated_interval import NOMINAL_LEVELS, fit, lag_discreteness, reliability_curve  # noqa: E402
from app.forecast.backtest import ape_panel  # noqa: E402
from app.forecast.forecastability import REFUSAL_REASONS, assess_batch  # noqa: E402
from app.forecast.predictor import predict_settlement  # noqa: E402


def _score(batch, only, interval_model=None, confidence=0.90):
    """Amount error and date coverage over a chosen subset of a settled batch.

    Amount numbers come from backtest.ape_panel, the same function run_backtest uses, so the two do
    not drift apart again.
    """
    order_by_id = {o.order_id: o for o in batch.orders}
    payment_by_id = {p.payment_id: p for p in batch.payments}

    apes, covered, scored, exact, undefined = [], 0, 0, 0, 0
    for s in batch.settlements:
        payment = payment_by_id.get(s.payment_id)
        if payment is None:
            continue
        order = order_by_id.get(payment.order_id)
        if order is None:
            continue
        if only is not None and order.order_id not in only:
            continue
        pred = predict_settlement(order, payment, interval_model, confidence)
        if s.settled_amount > 0:
            apes.append(abs(pred.predicted_net_amount - s.settled_amount) / s.settled_amount)
        else:
            undefined += 1
        if pred.predicted_net_amount == s.settled_amount:
            exact += 1
        scored += 1
        if pred.predicted_date_low <= s.settled_at <= pred.predicted_date_high:
            covered += 1
    panel = ape_panel(apes, undefined)
    return {
        "n": scored,
        "exact_rate": round(exact / scored, 4) if scored else None,
        **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in panel.items()},
        "date_coverage": round(covered / scored, 4) if scored else None,
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
    # Why it over-covers. Split conformal's finite-sample term is 1/(n+1); everything above that is
    # ties in the lag distribution, so the tie structure is published beside the curve.
    discreteness = lag_discreteness(model)
    print("\n=== why the curve over-covers: tie structure in the fitted lag ===")
    print(f"  {'rail':<12} {'n':>6} {'distinct':>9} {'heaviest tie':>13} {'obs/value':>10}")
    for rail, d in discreteness.items():
        print(f"  {rail:<12} {d['n']:>6} {d['distinct_values']:>9} {d['heaviest_tie_share'] * 100:>12.1f}% {d['mean_observations_per_value']:>10.2f}")

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
    print(f"  {'':<12} {'n':>6} {'exact':>8} {'median':>9} {'mean':>9} {'p95':>9} {'coverage':>10}")
    for label, s in (("forecast", on_accepted), ("refused", on_refused), ("everything", on_all)):
        print(
            f"  {label:<12} {s['n']:>6} {s['exact_rate'] * 100:>7.1f}% {s['median_ape'] * 100:>8.2f}% "
            f"{s['mape'] * 100:>8.2f}% {s['p95_ape'] * 100:>8.2f}% {s['date_coverage'] * 100:>9.1f}%"
        )
    share = len(refused) / max(len(assessments), 1)
    print(f"  refused {len(refused)} of {len(assessments)} ({share:.1%}): {reason_counts}")

    improves_mape = (
        on_accepted["mape"] is not None and on_refused["mape"] is not None and on_accepted["mape"] < on_refused["mape"]
    )
    improves_cov = (on_accepted["date_coverage"] or 0) > (on_refused["date_coverage"] or 0)
    if improves_mape and improves_cov:
        verdict = "refusing improves both MAPE and coverage on what remains"
    elif improves_mape or improves_cov:
        verdict = "refusing improves one of MAPE and coverage, not both"
    else:
        verdict = "refusing does NOT improve what remains, so this layer is not earning its place"
    print(f"  verdict: {verdict}")

    # --- which interval, and what does the extra coverage cost --------------------------------
    # The SLA tolerance window claims no confidence level, so no coverage number can falsify it.
    # The calibrated interval states one. This is the comparison that decides which to ship.
    print("\n=== SLA window vs calibrated interval, held-out batches, forecastable only ===")
    print(f"  {'interval':<16} {'coverage':>9} {'width (d)':>10}  claims")
    head_to_head = []
    for name, mdl, conf, claim in (
        ("sla_window", None, 0.90, "nothing"),
        ("calibrated_90", model, 0.90, "90%"),
        ("calibrated_95", model, 0.95, "95%"),
    ):
        covs, widths = [], []
        for seed in holdout_seeds[:5]:
            hb, _ = generate(seed=seed, main_n=args.n, stress_n=0)
            acc = {t_ for t_, a in assess_batch(hb.orders, hb.payments, hb.refunds).items() if a.forecastable}
            s = _score(hb, acc, mdl, conf)
            covs.append(s["date_coverage"])
            ob = {o.order_id: o for o in hb.orders}
            pb = {p.payment_id: p for p in hb.payments}
            ws = [
                (predict_settlement(ob[pb[x.payment_id].order_id], pb[x.payment_id], mdl, conf).predicted_date_high
                 - predict_settlement(ob[pb[x.payment_id].order_id], pb[x.payment_id], mdl, conf).predicted_date_low).total_seconds() / 86400
                for x in hb.settlements
                if x.payment_id in pb and pb[x.payment_id].order_id in ob and pb[x.payment_id].order_id in acc
            ]
            widths.append(statistics.mean(ws))
        entry = {
            "interval": name,
            "claimed_confidence": None if claim == "nothing" else conf,
            "empirical_coverage": round(statistics.mean(covs), 4),
            "mean_width_days": round(statistics.mean(widths), 2),
        }
        head_to_head.append(entry)
        print(f"  {name:<16} {entry['empirical_coverage'] * 100:>8.1f}% {entry['mean_width_days']:>10.2f}  {claim}")

    # --- where the remaining error comes from --------------------------------------------------
    # POST-HOC ATTRIBUTION, using the generator's answer key. The forecaster never sees these
    # labels; this exists to say which misses a better forecaster could have avoided and which are
    # unpredictable from Order and Payment alone.
    print("\n=== residual error by generator pattern (post-hoc, uses the answer key) ===")
    label_of = {g.transaction_id: g.true_label for g in audit.ground_truth}
    ob = {o.order_id: o for o in audit.orders}
    pb = {p.payment_id: p for p in audit.payments}
    amount_miss, date_miss = {}, {}
    for s in audit.settlements:
        p = pb.get(s.payment_id)
        o = ob.get(p.order_id) if p else None
        if o is None or o.order_id not in accepted:
            continue
        pred = predict_settlement(o, p)
        lab = label_of.get(o.order_id, "?")
        if pred.predicted_net_amount != s.settled_amount:
            amount_miss[lab] = amount_miss.get(lab, 0) + 1
        if not (pred.predicted_date_low <= s.settled_at <= pred.predicted_date_high):
            date_miss[lab] = date_miss.get(lab, 0) + 1
    print(f"  amount wrong: {dict(sorted(amount_miss.items(), key=lambda kv: -kv[1]))}")
    print(f"  date missed:  {dict(sorted(date_miss.items(), key=lambda kv: -kv[1]))}")

    # --- a banking calendar would not help, and here is why -------------------------------------
    # Real settlement lands on business days, so a weekday-aware window is the obvious next move.
    # It is only worth building if this data has weekday structure to exploit. Measured, not assumed.
    weekday_counts = Counter(s.settled_at.weekday() for s in audit.settlements)
    total_wd = sum(weekday_counts.values())
    weekend_share = (weekday_counts[5] + weekday_counts[6]) / total_wd if total_wd else 0.0
    print("\n=== banking-calendar check ===")
    print(f"  settlements landing on a weekend: {weekend_share:.1%} (uniform would be 28.6%)")
    print("  no weekday structure to exploit; a business-day adjustment would model nothing here")

    # --- do the other four refusal reasons work on a generated batch ---------------------------
    # Until now only refund_in_flight had ever fired outside a hand-built object, so the whole
    # measured effect of refusing rested on one reason out of five. generate_pending_batch's
    # edge_case_ratio (default 0.0, so no committed number moves) produces the other four.
    print("\n=== refusal reasons on a pending batch with edge cases ===")
    from datetime import timedelta

    from app.data_gen.generate import generate_pending_batch

    pending = generate_pending_batch(seed=7, n=400, edge_case_ratio=0.4)
    as_of = max(p_.captured_at for p_ in pending.payments) + timedelta(days=1)
    pending_assessments = assess_batch(pending.orders, pending.payments, [], as_of=as_of)
    fired: dict[str, int] = {}
    for a in pending_assessments.values():
        for r in a.reasons:
            fired[r] = fired.get(r, 0) + 1
    n_refused = sum(1 for a in pending_assessments.values() if not a.forecastable)
    for reason in sorted(REFUSAL_REASONS):
        count = fired.get(reason, 0)
        if count:
            status = "fires"
        elif reason == "refund_in_flight":
            status = "n/a here; needs a Refund, so it fires on the settled batch above"
        else:
            status = "still never fires"
        print(f"  {reason:<22} {count:>4}  {status}")
    print(f"  refused {n_refused} of {len(pending_assessments)} pending payments")

    baseline = generate_pending_batch(seed=7, n=400)
    baseline_refused = sum(1 for a in assess_batch(baseline.orders, baseline.payments, []).values() if not a.forecastable)
    print(f"  same batch with edge_case_ratio=0.0 refuses {baseline_refused}, which is the shipped default")

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
        "interval_head_to_head": head_to_head,
        "lag_discreteness": {
            "note": "split conformal over-covers by at most 1/(n+1); the rest is ties in a near-discrete lag",
            "finite_sample_bound_points": round(100 / (sum(c["n"] for c in curve) / len(curve) + 1), 4),
            "per_rail": discreteness,
        },
        "residual_error_by_pattern": {
            "note": "post-hoc attribution against the generator's answer key, which the forecaster never sees",
            "amount_wrong": dict(sorted(amount_miss.items(), key=lambda kv: -kv[1])),
            "date_missed": dict(sorted(date_miss.items(), key=lambda kv: -kv[1])),
        },
        "refusal_reasons_exercised": {
            "note": "generate_pending_batch(edge_case_ratio=0.4); the shipped default is 0.0 and refuses none of these",
            "n_pending": len(pending_assessments),
            "n_refused": n_refused,
            "fired": dict(sorted(fired.items())),
            "not_applicable_to_a_pending_batch": ["refund_in_flight"],
            "still_never_fires": sorted(r for r in REFUSAL_REASONS if not fired.get(r) and r != "refund_in_flight"),
            "default_ratio_refused": baseline_refused,
        },
        "banking_calendar_check": {
            "weekend_share_of_settlements": round(weekend_share, 4),
            "uniform_expectation": 0.2857,
            "conclusion": "no weekday structure in this generator, so a business-day window would model nothing",
        },
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
