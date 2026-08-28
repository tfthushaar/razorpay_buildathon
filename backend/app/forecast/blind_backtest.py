"""Genuinely-blind backtest for the forward settlement predictor.

`run_backtest` (app/forecast/backtest.py) reuses this project's own generated batches, whose real
settlements were computed with the exact same fee/SLA schedule (app/data_gen/fee_schedule.py) that
`predict_settlement` itself reads to make its prediction. That makes the ~73%-exact-by-construction
result an honest check that the predictor correctly APPLIES known reference data -- but it can't
tell you what happens when that reference data is stale, which is the realistic way a forward
forecast actually fails in practice: a merchant's real contracted fee rate, or the real settlement
timing, quietly drifting from what the platform's own schedule still assumes.

This module generates a separate, self-contained batch whose "real" settlements are computed against
a schedule perturbed from the canonical one -- a hidden, per-rail fee-rate and SLA-day drift, drawn
once per seed by `DriftedSchedule` and never exposed to `predict_settlement`, which keeps calling the
exact same canonical `fee_and_tax`/`BASE_SLA_DAYS` it always has, genuinely blind to the drift. Scored
by reusing `run_backtest`'s own MAPE/interval-coverage logic unchanged (via a `SyntheticBatch` with
only orders/payments/settlements populated) -- but the two numbers are NOT a clean before/after on the
same population: this batch has no refunds and no timing anomalies at all (every transaction here is
a plain capture-then-settle, only schedule drift as a source of error), while the non-blind batch's
own error comes almost entirely from its ~27% of transactions WITH a refund, dispute, or timing
anomaly (see LIMITATIONS.md). Read the two numbers as answers to two different questions -- robustness
to a stale schedule vs. correct application of a known one -- not as the same metric measured twice."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.data_gen.fee_schedule import BASE_SLA_DAYS, FEE_PCT, GST_RATE
from app.data_gen.schemas import Order, Payment, Rail, Settlement, SyntheticBatch
from app.forecast.backtest import BacktestReport, run_backtest

RAIL_WEIGHTS: dict[Rail, float] = {"upi": 0.5, "card": 0.35, "netbanking": 0.15}
AMOUNTS_INR = [500, 1000, 1500, 2500, 4200, 7500, 12000, 25000, 50000]


class DriftedSchedule:
    """A per-rail fee-rate and SLA-day drift, drawn once per seed from a stream distinct from the
    main batch generator's own RNG usage, and never read by the predictor -- the whole point is
    that it stays hidden from `predict_settlement`, only used here to compute this batch's own
    "real" settlements."""

    def __init__(self, seed: int, max_fee_drift: float = 0.15, max_sla_drift_days: int = 2):
        rng = random.Random(seed * 104729 + 1)
        self.fee_pct: dict[Rail, float] = {
            rail: max(0.0001, FEE_PCT[rail] * (1 + rng.uniform(-max_fee_drift, max_fee_drift))) for rail in FEE_PCT
        }
        self.sla_days: dict[Rail, int] = {
            rail: max(1, BASE_SLA_DAYS[rail] + rng.randint(-max_sla_drift_days, max_sla_drift_days)) for rail in BASE_SLA_DAYS
        }


def generate_blind_batch(seed: int, n: int = 100, max_fee_drift: float = 0.15, max_sla_drift_days: int = 2) -> SyntheticBatch:
    """n captured, settled transactions whose real fee/tax/settlement-date were computed against a
    hidden, drifted schedule. `predict_settlement` never sees `DriftedSchedule` -- only the order and
    payment it produces, exactly as it would for genuinely in-flight money."""
    rng = random.Random(seed)
    drift = DriftedSchedule(seed, max_fee_drift, max_sla_drift_days)
    base_date = datetime(2026, 1, 1)
    rails, weights = zip(*RAIL_WEIGHTS.items())

    orders: list[Order] = []
    payments: list[Payment] = []
    settlements: list[Settlement] = []
    for i in range(n):
        rail: Rail = rng.choices(rails, weights=weights)[0]
        amount = rng.choice(AMOUNTS_INR) * 100
        created_at = base_date + timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        captured_at = created_at + timedelta(minutes=rng.randint(1, 45))
        real_fee = round(amount * drift.fee_pct[rail])
        real_tax = round(real_fee * GST_RATE)

        order_id = f"blind_order_{i:05d}"
        payment_id = f"blind_pay_{i:05d}"
        orders.append(Order(order_id=order_id, merchant_id="merchant_001", amount=amount, currency="INR", created_at=created_at, rail=rail))
        payments.append(
            Payment(
                payment_id=payment_id,
                order_id=order_id,
                status="captured",
                captured=True,
                captured_amount=amount,
                fee_amount=real_fee,
                tax_amount=real_tax,
                gateway="HDFC",
                captured_at=captured_at,
            )
        )
        settled_at = captured_at + timedelta(days=drift.sla_days[rail], hours=rng.randint(0, 10))
        settlements.append(
            Settlement(
                settlement_id=f"blind_stl_{i:05d}",
                payment_id=payment_id,
                settled_amount=amount - real_fee - real_tax,
                settlement_batch_id=f"blind_batch_{rail}_{settled_at.date().isoformat()}",
                utr=f"{i:012d}",
                rail=rail,
                settled_at=settled_at,
                sla_days=drift.sla_days[rail],
            )
        )
    return SyntheticBatch(orders=orders, payments=payments, refunds=[], settlements=settlements, ledger_entries=[], ground_truth=[])


def run_blind_backtest(seed: int, n: int = 100, max_fee_drift: float = 0.15, max_sla_drift_days: int = 2) -> BacktestReport:
    """The predictor's real backtest, run genuinely blind to the schedule its predictions were scored
    against. A nonzero MAPE and imperfect coverage here reflect real forecast error under schedule
    drift the predictor structurally cannot know about -- not the same reference data recomputed and
    compared to itself, which is what `run_backtest` alone measures."""
    batch = generate_blind_batch(seed, n, max_fee_drift, max_sla_drift_days)
    return run_backtest(batch)
