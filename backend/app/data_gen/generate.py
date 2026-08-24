"""Synthetic data generator with a hidden ground-truth answer key.

Spec: docs/track04-settlement-reconciliation-copilot.md §4, §6.1.

Everything downstream (chain builder, matching engine, calibration, dashboard) depends on
this being right, so it's built first and tested in isolation (see backend/tests/test_generate.py).

Distribution (documented, per spec §6.1): 60% clean, 25% explainable exceptions,
10% adversarial traps, 5% genuinely ambiguous. The ground truth is never read by anything
except the scoring/calibration code — matching and narration logic must not import this module's
labels.
"""

import random
import string
from datetime import datetime, timedelta

from app.data_gen.fee_schedule import BASE_SLA_DAYS, NETBANKING_SLA_RANGE, SLA_TOLERANCE_DAYS, fee_and_tax
from app.data_gen.schemas import (
    GroundTruthEntry,
    LedgerEntry,
    Order,
    Payment,
    Rail,
    Refund,
    Settlement,
    SyntheticBatch,
)

GATEWAYS = ["HDFC", "ICICI", "AXIS", "YESBANK", "KOTAK"]
AMOUNTS_INR = [500, 1000, 1500, 2500, 4200, 7500, 12000, 25000, 50000]  # rupees; stored as paise
RAIL_WEIGHTS = {"upi": 0.5, "card": 0.35, "netbanking": 0.15}


class SyntheticDataGenerator:
    def __init__(self, seed: int = 42, base_date: datetime | None = None):
        self.rng = random.Random(seed)
        self.base_date = base_date or datetime(2026, 1, 1)
        self.merchant_ids = [f"merchant_{i:03d}" for i in range(1, 11)]
        self._seq = 0

    # ---- id / primitive helpers -------------------------------------------------

    def _new_id(self, prefix: str, length: int = 12) -> str:
        self._seq += 1
        suffix = "".join(self.rng.choices(string.hexdigits.lower()[:16], k=length))
        return f"{prefix}_{suffix}"

    def _new_utr(self) -> str:
        return "".join(self.rng.choices(string.digits, k=12))

    def _pick_rail(self) -> Rail:
        rails, weights = zip(*RAIL_WEIGHTS.items())
        return self.rng.choices(rails, weights=weights)[0]

    def _sla_days_for(self, rail: Rail) -> int:
        if rail == "netbanking":
            return self.rng.randint(*NETBANKING_SLA_RANGE)
        return BASE_SLA_DAYS[rail]

    def _rand_amount(self, currency: str = "INR") -> int:
        rupees = self.rng.choice(AMOUNTS_INR)
        return rupees * 100  # paise (or cents-equivalent for the synthetic USD cases)

    def _rand_created_at(self) -> datetime:
        return self.base_date + timedelta(
            days=self.rng.randint(0, 20), hours=self.rng.randint(0, 23), minutes=self.rng.randint(0, 59)
        )

    # ---- shared chain-piece builders ---------------------------------------------

    def _build_order_and_payment(
        self, amount: int, rail: Rail, currency: str, created_at: datetime
    ) -> tuple[Order, Payment, int, int]:
        order_id = self._new_id("order")
        payment_id = self._new_id("pay")
        fee, tax = fee_and_tax(rail, amount)
        captured_at = created_at + timedelta(minutes=self.rng.randint(1, 45))
        order = Order(
            order_id=order_id,
            merchant_id=self.rng.choice(self.merchant_ids),
            amount=amount,
            currency=currency,
            created_at=created_at,
            rail=rail,
        )
        payment = Payment(
            payment_id=payment_id,
            order_id=order_id,
            status="captured",
            captured=True,
            captured_amount=amount,
            fee_amount=fee,
            tax_amount=tax,
            gateway=self.rng.choice(GATEWAYS),
            captured_at=captured_at,
        )
        return order, payment, fee, tax

    def _build_settlement(
        self,
        payment_id: str,
        rail: Rail,
        settled_amount: int,
        captured_at: datetime,
        sla_days: int | None = None,
        batch_id_override: str | None = None,
    ) -> Settlement:
        sla = sla_days if sla_days is not None else self._sla_days_for(rail)
        settled_at = captured_at + timedelta(days=sla, hours=self.rng.randint(0, 10))
        # batch_id_override exists so paired transactions (netting_trap) can be forced into the
        # exact same settlement batch deterministically, rather than hoping independently-computed
        # settled_at timestamps happen to land on the same calendar date.
        batch_id = batch_id_override or f"batch_{rail}_{settled_at.date().isoformat()}"
        return Settlement(
            settlement_id=self._new_id("stl"),
            payment_id=payment_id,
            settled_amount=settled_amount,
            settlement_batch_id=batch_id,
            utr=self._new_utr(),
            rail=rail,
            settled_at=settled_at,
            sla_days=sla,
        )

    def _build_ledger(self, order_id: str, expected_amount: int, recorded_at: datetime) -> LedgerEntry:
        return LedgerEntry(
            ledger_id=self._new_id("ldg"), order_id=order_id, expected_amount=expected_amount, recorded_at=recorded_at
        )

    # ---- category generators ------------------------------------------------------
    # Each returns (orders, payments, refunds, settlements, ledger_entries, ground_truth_entries)
    # as lists so netting_trap can emit two of everything from one call.

    def _gen_clean_match(self):
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        net = amount - fee - tax
        settlement = self._build_settlement(payment.payment_id, rail, net, payment.captured_at)
        ledger = self._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(transaction_id=order.order_id, true_label="clean_match", injected_by_you=False)
        return [order], [payment], [], [settlement], [ledger], [gt]

    def _gen_timing_lag(self):
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        net = amount - fee - tax
        # must unambiguously cross the matching engine's own tolerance line (SLA_TOLERANCE_DAYS),
        # not just the nominal base — otherwise a low-sampled netbanking base + a small stretch can
        # land back inside "normal variance" and get read as clean_match instead of timing_lag.
        stretched_sla = SLA_TOLERANCE_DAYS[rail] + self.rng.randint(1, 3)
        settlement = self._build_settlement(payment.payment_id, rail, net, payment.captured_at, sla_days=stretched_sla)
        ledger = self._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="timing_lag",
            injected_by_you=True,
            internal_note=f"settled at sla_days={stretched_sla}, tolerance_ceiling={SLA_TOLERANCE_DAYS[rail]}",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    def _gen_fee_deduction(self):
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        net = amount - fee - tax
        settlement = self._build_settlement(payment.payment_id, rail, net, payment.captured_at)
        # ledger wasn't updated for the fee — merchant's books still expect the full order amount
        ledger = self._build_ledger(order.order_id, amount, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="fee_deduction",
            injected_by_you=True,
            internal_note=f"ledger expects full amount, missing fee+tax={fee + tax}",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    def _gen_partial_refund(self):
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        net_before_refund = amount - fee - tax
        refund_amount = round(amount * self.rng.uniform(0.2, 0.5))
        refund = Refund(
            refund_id=self._new_id("rfnd"),
            payment_id=payment.payment_id,
            amount=refund_amount,
            created_at=payment.captured_at + timedelta(days=self.rng.randint(1, 3)),
            refund_type="partial",
        )
        net_after_refund = net_before_refund - refund_amount
        settlement = self._build_settlement(payment.payment_id, rail, net_after_refund, payment.captured_at)
        # ledger not yet updated for the refund
        ledger = self._build_ledger(order.order_id, net_before_refund, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="partial_refund",
            injected_by_you=True,
            internal_note=f"refund={refund_amount} not yet reflected in ledger",
        )
        return [order], [payment], [refund], [settlement], [ledger], [gt]

    def _gen_currency_rounding(self):
        rail = self._pick_rail()
        amount = self._rand_amount(currency="USD")
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "USD", created_at)
        net = amount - fee - tax
        # small FX-conversion rounding drift, a handful of smallest-currency-units either way
        drift = self.rng.choice([-3, -2, -1, 1, 2, 3])
        settlement = self._build_settlement(payment.payment_id, rail, net + drift, payment.captured_at)
        ledger = self._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="currency_rounding",
            injected_by_you=True,
            internal_note=f"fx rounding drift={drift}",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    def _gen_duplicate_refund(self):
        """The refund is legitimately issued once, but the settlement feed reflects it twice —
        a naive matcher that just nets totals could wrongly treat this as 'two valid refunds'
        and clean-match it. The trap: don't double-resolve a refund that only happened once."""
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        net_before_refund = amount - fee - tax
        refund_amount = round(amount * self.rng.uniform(0.2, 0.4))
        refund = Refund(
            refund_id=self._new_id("rfnd"),
            payment_id=payment.payment_id,
            amount=refund_amount,
            created_at=payment.captured_at + timedelta(days=1),
            refund_type="partial",
        )
        # settlement deducts the refund amount TWICE — the duplicate application
        net_after_refund = net_before_refund - (2 * refund_amount)
        settlement = self._build_settlement(payment.payment_id, rail, net_after_refund, payment.captured_at)
        ledger = self._build_ledger(order.order_id, net_before_refund - refund_amount, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="duplicate_refund",
            injected_by_you=True,
            internal_note=f"refund {refund.refund_id} of {refund_amount} applied twice in settlement",
        )
        return [order], [payment], [refund], [settlement], [ledger], [gt]

    def _gen_netting_trap(self):
        """Two independent transactions whose individual errors are +X and -X. Summed at the
        batch level they look perfectly reconciled; per-transaction (causal chain) matching is
        required to catch that each one is individually wrong. Directly validates the
        'causal chain matching, not row matching' pitch (spec §3.1)."""
        rail = self._pick_rail()
        created_at = self._rand_created_at()
        x = self.rng.choice([50, 100, 150, 200]) * 100  # paise
        # both halves must land in the *same* settlement batch — that's the entire premise of the
        # trap (they only look reconciled when netted at the batch level) — so sla_days and the
        # batch id are shared explicitly rather than left to independently-sampled settlement
        # timestamps that might coincidentally land on different calendar dates.
        shared_sla = self._sla_days_for(rail)
        shared_batch_id = f"batch_{rail}_{(created_at + timedelta(days=shared_sla)).date().isoformat()}"

        amount_a = self._rand_amount()
        order_a, payment_a, fee_a, tax_a = self._build_order_and_payment(amount_a, rail, "INR", created_at)
        net_a = amount_a - fee_a - tax_a
        settlement_a = self._build_settlement(
            payment_a.payment_id, rail, net_a - x, payment_a.captured_at, sla_days=shared_sla, batch_id_override=shared_batch_id
        )
        ledger_a = self._build_ledger(order_a.order_id, net_a, created_at + timedelta(minutes=5))

        amount_b = self._rand_amount()
        order_b, payment_b, fee_b, tax_b = self._build_order_and_payment(amount_b, rail, "INR", created_at)
        net_b = amount_b - fee_b - tax_b
        settlement_b = self._build_settlement(
            payment_b.payment_id, rail, net_b + x, payment_b.captured_at, sla_days=shared_sla, batch_id_override=shared_batch_id
        )
        ledger_b = self._build_ledger(order_b.order_id, net_b, created_at + timedelta(minutes=5))

        gt_a = GroundTruthEntry(
            transaction_id=order_a.order_id,
            true_label="netting_trap",
            injected_by_you=True,
            linked_transaction_id=order_b.order_id,
            internal_note=f"short by {x}, nets against {order_b.order_id}",
        )
        gt_b = GroundTruthEntry(
            transaction_id=order_b.order_id,
            true_label="netting_trap",
            injected_by_you=True,
            linked_transaction_id=order_a.order_id,
            internal_note=f"over by {x}, nets against {order_a.order_id}",
        )
        return (
            [order_a, order_b],
            [payment_a, payment_b],
            [],
            [settlement_a, settlement_b],
            [ledger_a, ledger_b],
            [gt_a, gt_b],
        )

    def _gen_genuine_error(self):
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        net = amount - fee - tax
        # an arbitrary delta that doesn't match any known fee/refund/rounding/timing pattern
        junk_delta = self.rng.choice([-1, 1]) * self.rng.randint(300, 900) * 100
        settlement = self._build_settlement(payment.payment_id, rail, net + junk_delta, payment.captured_at)
        ledger = self._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="genuine_error",
            injected_by_you=True,
            internal_note=f"unexplained delta={junk_delta}, no fee/refund/rounding/timing rationale fits",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    # ---- batch assembly -------------------------------------------------------

    def _merge(self, batches: list[tuple]) -> SyntheticBatch:
        orders, payments, refunds, settlements, ledgers, gts = [], [], [], [], [], []
        for o, p, r, s, l, g in batches:
            orders += o
            payments += p
            refunds += r
            settlements += s
            ledgers += l
            gts += g
        return SyntheticBatch(
            orders=orders, payments=payments, refunds=refunds, settlements=settlements, ledger_entries=ledgers, ground_truth=gts
        )

    def generate_main_batch(self, n: int = 120) -> SyntheticBatch:
        """Main batch: 60% clean, 25% explainable exceptions, 10% adversarial traps,
        5% genuinely ambiguous (spec §6.1). netting_trap consumes 2 slots per pair, so the
        adversarial-trap share is built from pairs + singles until the slot budget is used."""
        n_clean = round(n * 0.60)
        n_explainable = round(n * 0.25)
        n_adversarial = round(n * 0.10)
        n_ambiguous = n - n_clean - n_explainable - n_adversarial  # remainder, ~5%
        if n_ambiguous < 0:
            # independent per-share rounding can overshoot n at small n -- verified directly by
            # brute-forcing every n from 0-2000: n=6 is the only value where round(0.60n) +
            # round(0.25n) + round(0.10n) = 7 > 6 (4+2+1), which silently generated 7 transactions
            # for a requested batch of 6 instead of erroring, since range(-1) just yields nothing
            # rather than raising. Caught by an external audit 2026-08-24. Absorb the overflow into
            # n_clean (the least structurally-constrained category) so the batch always totals
            # exactly n, not silently more.
            n_clean += n_ambiguous
            n_ambiguous = 0

        batches = []
        batches += [self._gen_clean_match() for _ in range(n_clean)]

        explainable_gens = [self._gen_timing_lag, self._gen_fee_deduction, self._gen_partial_refund, self._gen_currency_rounding]
        for i in range(n_explainable):
            batches.append(explainable_gens[i % len(explainable_gens)]())

        remaining_adversarial = n_adversarial
        while remaining_adversarial > 0:
            if remaining_adversarial >= 2 and self.rng.random() < 0.5:
                batches.append(self._gen_netting_trap())
                remaining_adversarial -= 2
            else:
                batches.append(self._gen_duplicate_refund())
                remaining_adversarial -= 1

        batches += [self._gen_genuine_error() for _ in range(n_ambiguous)]

        self.rng.shuffle(batches)
        return self._merge(batches)

    def generate_stress_batch(self, n: int = 40) -> SyntheticBatch:
        """Dedicated 100%-adversarial stress batch (spec §4, "A separate 100%-adversarial
        stress batch"). Never blended into the main batch's reported accuracy — scored and
        reported on its own as a single clean stat."""
        trap_gens = [self._gen_duplicate_refund, self._gen_fee_deduction, self._gen_genuine_error]
        batches = []
        remaining = n
        while remaining > 0:
            if remaining >= 2 and self.rng.random() < 0.4:
                batches.append(self._gen_netting_trap())
                remaining -= 2
            else:
                batches.append(self.rng.choice(trap_gens)())
                remaining -= 1
        self.rng.shuffle(batches)
        return self._merge(batches)


def generate(seed: int = 42, main_n: int = 120, stress_n: int = 40) -> tuple[SyntheticBatch, SyntheticBatch]:
    gen = SyntheticDataGenerator(seed=seed)
    main_batch = gen.generate_main_batch(main_n)
    stress_gen = SyntheticDataGenerator(seed=seed + 1)  # distinct stream so the stress batch isn't a replay of the main one
    stress_batch = stress_gen.generate_stress_batch(stress_n)
    return main_batch, stress_batch
