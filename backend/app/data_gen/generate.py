"""Synthetic data generator with a hidden ground-truth answer key.

Spec: docs/ARCHITECTURE.md.

Everything downstream (chain builder, matching engine, calibration, dashboard) depends on
this being right, so it's built first and tested in isolation (see backend/tests/test_generate.py).

Distribution (documented): 60% clean, 25% explainable exceptions,
10% adversarial traps, 5% genuinely ambiguous. The ground truth is never read by anything
except the scoring/calibration code — matching and narration logic must not import this module's
labels.
"""

import random
import string
from datetime import datetime, timedelta

from app.data_gen.fee_schedule import BASE_SLA_DAYS, FEE_PCT, GST_RATE, NETBANKING_SLA_RANGE, SLA_TOLERANCE_DAYS, fee_and_tax
from app.data_gen.schemas import (
    GroundTruthEntry,
    LedgerEntry,
    Order,
    Payment,
    PendingBatch,
    Rail,
    Refund,
    Settlement,
    SyntheticBatch,
)
from app.data_gen.subset_sum import find_other_subsets_that_cancel

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
        'causal chain matching, not row matching' pitch."""
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

    def _gen_multiway_netting_trap(self, group_size: int = 3, n_distractors: int = 3):
        """`group_size` (>=3 -- 2 is just the existing pairwise `_gen_netting_trap`) transactions in
        the same settlement batch whose deltas sum to exactly zero together. Individually, each
        looks like an unexplained genuine_error; jointly they net out. Invisible to
        `check_batch_anomalies`'s pairwise-only check by construction -- it only ever looks for ONE
        other transaction with the exact opposite delta, never a combination.

        `n_distractors` additional, unrelated transactions share the same batch so the real group
        isn't the only non-trivial candidate -- a real search, not a forced move (the same design
        lesson app/narrator/multiway_netting_experiment.py's own docstring documents learning the
        hard way). Verified by brute force (app/data_gen/subset_sum.py) from every group member's own
        perspective that no OTHER subset up to size 4 also cancels its delta -- raises if
        construction is ever ambiguous rather than silently shipping a bad case."""
        if group_size < 3:
            raise ValueError("group_size must be >= 3 -- 2 is just the existing pairwise netting_trap")
        rail = self._pick_rail()
        created_at = self._rand_created_at()
        shared_sla = self._sla_days_for(rail)
        # A dedicated batch id, not the ambient batch_{rail}_{date} scheme _gen_netting_trap reuses --
        # keeps the uniqueness proof below bounded to a fully-known set of transactions, rather than
        # having to reason about whatever else coincidentally lands on the same rail+date.
        batch_id = f"batch_{rail}_multiway_{self._new_id('grp', 8)}"

        # group_size deltas summing to exactly zero, drawn from a wide, fine-grained (paise-level,
        # not round-hundred) range -- the side experiment this pattern is based on drew from round
        # values once and hit a real accidental subset-sum collision, caught by the uniqueness check
        # below, not shipped unnoticed.
        def _draw_group() -> list[int]:
            deltas: list[int] = []
            seen_local: set[int] = set()
            while len(deltas) < group_size - 1:
                d = self.rng.randint(-999_931, 999_931)
                if d != 0 and d not in seen_local:
                    deltas.append(d)
                    seen_local.add(d)
            last = -sum(deltas)
            if last == 0 or last in seen_local:
                return _draw_group()  # vanishingly rare exact collision -- redraw the whole group
            deltas.append(last)
            return deltas

        group_deltas = _draw_group()
        seen = set(group_deltas)

        distractor_deltas: list[int] = []
        while len(distractor_deltas) < n_distractors:
            d = self.rng.randint(-999_931, 999_931)
            if d != 0 and d not in seen:
                distractor_deltas.append(d)
                seen.add(d)

        orders, payments, settlements, ledgers, gts = [], [], [], [], []

        def _build_one(delta: int) -> str:
            amount = self._rand_amount()
            order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
            net = amount - fee - tax
            settlement = self._build_settlement(
                payment.payment_id, rail, net + delta, payment.captured_at, sla_days=shared_sla, batch_id_override=batch_id
            )
            ledger = self._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
            orders.append(order)
            payments.append(payment)
            settlements.append(settlement)
            ledgers.append(ledger)
            return order.order_id

        group_order_ids = [_build_one(d) for d in group_deltas]
        distractor_order_ids = [_build_one(d) for d in distractor_deltas]

        # Verify uniqueness for real, from every group member's own perspective -- not assumed. A
        # failure here means this seed's construction is ambiguous and must not ship.
        all_deltas_by_id = dict(zip(group_order_ids, group_deltas))
        all_deltas_by_id.update(dict(zip(distractor_order_ids, distractor_deltas)))
        for i, focal_id in enumerate(group_order_ids):
            others = {k: v for k, v in all_deltas_by_id.items() if k != focal_id}
            correct = set(group_order_ids) - {focal_id}
            stray = find_other_subsets_that_cancel(group_deltas[i], others, correct)
            if stray:
                raise AssertionError(f"multiway_netting_trap construction is ambiguous for {focal_id}: other subsets also cancel: {stray}")

        for i, order_id in enumerate(group_order_ids):
            others = [oid for oid in group_order_ids if oid != order_id]
            gts.append(
                GroundTruthEntry(
                    transaction_id=order_id,
                    true_label="multiway_netting_trap",
                    injected_by_you=True,
                    linked_transaction_ids=others,
                    internal_note=f"delta={group_deltas[i]}, nets against {others} in batch {batch_id}",
                )
            )
        for i, order_id in enumerate(distractor_order_ids):
            gts.append(
                GroundTruthEntry(
                    transaction_id=order_id,
                    true_label="genuine_error",
                    injected_by_you=True,
                    internal_note=f"unexplained delta={distractor_deltas[i]}, a same-batch distractor for a multiway_netting_trap case -- doesn't cancel with anything",
                )
            )

        return orders, payments, [], settlements, ledgers, gts

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

    def generate_main_batch(
        self,
        n: int = 120,
        clean_ratio: float = 0.60,
        enable_multiway_netting: bool = False,
        multiway_group_size: int = 3,
        multiway_n_distractors: int = 3,
    ) -> SyntheticBatch:
        """Main batch: `clean_ratio` clean (default 60%), remaining share split
        25:10:5 (explainable:adversarial:ambiguous -- the spec's original relative proportions
        among non-clean records) as `clean_ratio` moves away from the default. netting_trap
        consumes 2 slots per pair, so the adversarial-trap share is built from pairs + singles
        until the slot budget is used.

        `clean_ratio` exists for a realistically-sparse large-scale benchmark (see BUILD_LOG.md's
        Merkle pre-filter integration): a real settlement batch is overwhelmingly clean, unlike
        this project's own default demo density (deliberately denser than reality so every
        category class is reliably exercised even at small N).

        `enable_multiway_netting` defaults False -- every already-committed evidence file and
        BUILD_LOG number was measured against the generator without it; flipping the default is a
        separate, deliberate decision made only after this category is measured on its own. When on,
        `_gen_multiway_netting_trap` consumes `multiway_group_size` adversarial slots and
        `multiway_n_distractors` ambiguous slots per call (its group members are adversarial-labeled,
        its distractors are honestly genuine_error-labeled) -- both shares are tracked independently
        below so the batch always totals exactly `n`, the same invariant the pairwise trap already
        preserves."""
        if clean_ratio == 0.60:
            # the exact original literal expressions, kept byte-for-byte instead of derived from
            # clean_ratio -- checked directly (not assumed) that the mathematically-equivalent
            # generalized formula below (non_clean_ratio * 0.625 etc.) produces a DIFFERENT integer
            # than round(n*0.25) at 62 different values of n between 0 and 2000, purely from
            # floating-point rounding through a different expression. Reusing the general formula
            # here would have silently changed this project's own extensively-tested default demo
            # distribution for no reason.
            n_clean = round(n * 0.60)
            n_explainable = round(n * 0.25)
            n_adversarial = round(n * 0.10)
        else:
            # preserves the spec's original 25:10:5 relative split among non-clean records --
            # 0.25/0.40, 0.10/0.40, 0.05/0.40 of whatever the non-clean share is. Only reached for
            # a non-default clean_ratio (the sparse-divergence large-scale benchmark), so the
            # floating-point discrepancy above never touches the default path.
            non_clean_ratio = 1 - clean_ratio
            n_clean = round(n * clean_ratio)
            n_explainable = round(n * non_clean_ratio * 0.625)  # 0.25/0.40
            n_adversarial = round(n * non_clean_ratio * 0.25)  # 0.10/0.40
        n_ambiguous = n - n_clean - n_explainable - n_adversarial  # remainder
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
        remaining_ambiguous = n_ambiguous
        while remaining_adversarial > 0:
            if (
                enable_multiway_netting
                and remaining_adversarial >= multiway_group_size
                and remaining_ambiguous >= multiway_n_distractors
                and self.rng.random() < 0.34
            ):
                batches.append(self._gen_multiway_netting_trap(multiway_group_size, multiway_n_distractors))
                remaining_adversarial -= multiway_group_size
                remaining_ambiguous -= multiway_n_distractors
            elif remaining_adversarial >= 2 and self.rng.random() < 0.5:
                batches.append(self._gen_netting_trap())
                remaining_adversarial -= 2
            else:
                batches.append(self._gen_duplicate_refund())
                remaining_adversarial -= 1

        batches += [self._gen_genuine_error() for _ in range(remaining_ambiguous)]

        self.rng.shuffle(batches)
        return self._merge(batches)

    def _gen_fee_leak_blended_rate(self):
        """Fee-leak pattern (app/feeleak/detector.py): the merchant's contracted rate for this
        instrument (fee_schedule.py's FEE_PCT) is not what was actually deducted -- the actual fee
        was computed at a higher, blended rate instead, as if a flat card-grade rate were applied
        regardless of instrument. Unlike every other category in this generator, this does NOT
        create a reconciliation exception: the ledger and settlement both consistently reflect the
        (overcharged) actual fee, so run_pass1 sees ledger_gap == 0 and calls it clean -- the leak
        is invisible to standard reconciliation, exactly the real-world blind spot the fee-leak
        detector exists to close (it compares the actual fee against the contract, a check no
        reconciliation-only pipeline performs at all).

        Deliberately UPI-weighted: UPI's contracted rate (0.3%) is the furthest from the blended
        card rate (2%), producing the largest, clearest leaks to detect. Framed as a contract-vs-
        actual comparison specifically, NOT a blanket "UPI MDR is illegal" claim -- the August 2026
        PSS Act amendment replaced the blanket zero-MDR mandate with a government-notification
        framework, so the legally safe, permanently-correct check is against what THIS merchant's
        OWN contract says, not an assumption about the current regulatory notification state."""
        rail: Rail = self.rng.choice(["upi", "upi", "netbanking"])
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order_id = self._new_id("order")
        payment_id = self._new_id("pay")
        overcharge_rate = FEE_PCT["card"]  # the blended rate mistakenly applied instead of the contracted one
        fee = round(amount * overcharge_rate)
        tax = round(fee * GST_RATE)
        captured_at = created_at + timedelta(minutes=self.rng.randint(1, 45))
        order = Order(order_id=order_id, merchant_id=self.rng.choice(self.merchant_ids), amount=amount, currency="INR", created_at=created_at, rail=rail)
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
        net = amount - fee - tax
        settlement = self._build_settlement(payment_id, rail, net, captured_at)
        ledger = self._build_ledger(order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order_id,
            true_label="clean_match",
            injected_by_you=True,
            internal_note=f"fee_leak:blended_rate rail={rail} contracted_rate={FEE_PCT[rail]} actual_rate={overcharge_rate}",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    def _gen_fee_leak_gst_wrong_base(self):
        """Fee-leak pattern: GST computed on the gross transaction amount instead of the fee
        itself -- correct GST law taxes the service (the gateway fee), not the transaction value.
        Like the blended-rate pattern above, this reconciles cleanly (ledger/settlement agree on
        the actual, wrongly-computed numbers) and is only visible by checking tax_amount against
        fee_amount, exactly what the fee-leak detector does and standard reconciliation doesn't."""
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order_id = self._new_id("order")
        payment_id = self._new_id("pay")
        fee = round(amount * FEE_PCT[rail])  # the fee itself is correctly contracted
        tax = round(amount * GST_RATE)  # but GST is wrongly based on gross amount, not the fee
        captured_at = created_at + timedelta(minutes=self.rng.randint(1, 45))
        order = Order(order_id=order_id, merchant_id=self.rng.choice(self.merchant_ids), amount=amount, currency="INR", created_at=created_at, rail=rail)
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
        net = amount - fee - tax
        settlement = self._build_settlement(payment_id, rail, net, captured_at)
        ledger = self._build_ledger(order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order_id,
            true_label="clean_match",
            injected_by_you=True,
            internal_note=f"fee_leak:gst_wrong_base rail={rail} fee={fee} correct_gst={round(fee * GST_RATE)} actual_gst={tax}",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    def _gen_fee_leak_gst_wrong_rate(self):
        """Fee-leak pattern: GST correctly based on the gateway fee itself (unlike gst_wrong_base
        above, which uses the wrong base entirely) but computed at the wrong RATE -- 0% instead of
        the 18% that actually applies to payment gateway/financial services. 0% is a real GST slab
        (zero-rated/exempt services), so "an exemption meant for a different service got applied
        here" is a plausible, realistic error, not an invented one -- see app/feeleak/detector.py's
        OTHER_GST_SLABS for the full set this project's detector checks against.

        Restricted to `card` specifically, not `self._pick_rail()` -- verified directly (not
        assumed): even a full 18-percentage-point GST-rate error produces a rupee delta small
        enough to fall under LEAK_EPSILON's rounding-noise threshold for UPI (0.3% fee) and
        netbanking (1% fee) at this generator's smaller amounts (e.g. UPI/₹500: fee=150,
        delta=27 paise). Only `card`'s 2% contracted fee produces a delta that clears the epsilon
        threshold across this generator's entire amount range (worst case, ₹500: fee=1000,
        delta=180 paise) -- narrower than the other two patterns' rail coverage, disclosed here
        rather than silently shipping a pattern that sometimes can't be detected by construction.

        Reconciles cleanly, same as the other two fee-leak patterns; only visible by checking
        tax_amount against fee_amount at the correct rate, exactly what standard reconciliation
        doesn't do."""
        rail: Rail = "card"
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order_id = self._new_id("order")
        payment_id = self._new_id("pay")
        fee = round(amount * FEE_PCT[rail])  # fee itself is correctly contracted
        wrong_gst_rate = 0.0  # a real other GST slab (zero-rated), mistakenly applied instead of 18%
        tax = round(fee * wrong_gst_rate)  # correctly based on the fee, but at the wrong rate
        captured_at = created_at + timedelta(minutes=self.rng.randint(1, 45))
        order = Order(order_id=order_id, merchant_id=self.rng.choice(self.merchant_ids), amount=amount, currency="INR", created_at=created_at, rail=rail)
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
        net = amount - fee - tax
        settlement = self._build_settlement(payment_id, rail, net, captured_at)
        ledger = self._build_ledger(order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order_id,
            true_label="clean_match",
            injected_by_you=True,
            internal_note=f"fee_leak:gst_wrong_rate rail={rail} fee={fee} correct_gst={round(fee * GST_RATE)} actual_gst={tax} (at {wrong_gst_rate:.0%})",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    def generate_fee_leak_batch(self, n: int = 20) -> SyntheticBatch:
        """A separate, additional batch of transactions that reconcile perfectly cleanly (no
        ledger/settlement exception at all) but were charged fees inconsistent with the merchant's
        own contract -- the blind spot standard reconciliation can't see, and the reason this is a
        genuinely different axis of analysis from everything else this generator produces. Never
        blended into the main/stress batches' reported accuracy, same convention as
        generate_stress_batch. Three patterns, cycled evenly."""
        batches = []
        for i in range(n):
            if i % 3 == 0:
                batches.append(self._gen_fee_leak_blended_rate())
            elif i % 3 == 1:
                batches.append(self._gen_fee_leak_gst_wrong_base())
            else:
                batches.append(self._gen_fee_leak_gst_wrong_rate())
        self.rng.shuffle(batches)
        return self._merge(batches)

    def generate_pending_batch(self, n: int = 10) -> PendingBatch:
        """Orders + captured payments with NO settlement -- genuinely in-flight money, unlike
        every other batch this generator produces (all of which are "closed" by construction,
        since build_all_chains() requires a Settlement to exist). Reuses
        _build_order_and_payment exactly as-is; the only difference from a normal transaction is
        that the settlement step this generator would normally take next is simply never taken.

        Deliberately does NOT use _rand_created_at() -- that spreads captures across a full 20-day
        window, appropriate for a batch of already-resolved transactions but wrong for "still in
        flight right now": nearly every one of them would already look overdue against a 1-5 day
        SLA window purely from the spread, not from anything genuinely wrong. Captures here are
        clustered in the last 0-2 days instead, the way an actual snapshot of unsettled payments
        would look."""
        orders: list[Order] = []
        payments: list[Payment] = []
        recent_base = self.base_date + timedelta(days=self.rng.randint(18, 20))
        for _ in range(n):
            rail = self._pick_rail()
            amount = self._rand_amount()
            created_at = recent_base - timedelta(hours=self.rng.randint(0, 48))
            order, payment, _fee, _tax = self._build_order_and_payment(amount, rail, "INR", created_at)
            orders.append(order)
            payments.append(payment)
        return PendingBatch(orders=orders, payments=payments)

    def generate_stress_batch(self, n: int = 40) -> SyntheticBatch:
        """Dedicated 100%-adversarial stress batch ("A separate 100%-adversarial
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


def generate(
    seed: int = 42, main_n: int = 120, stress_n: int = 40, clean_ratio: float = 0.60, enable_multiway_netting: bool = False
) -> tuple[SyntheticBatch, SyntheticBatch]:
    gen = SyntheticDataGenerator(seed=seed)
    main_batch = gen.generate_main_batch(main_n, clean_ratio=clean_ratio, enable_multiway_netting=enable_multiway_netting)
    stress_gen = SyntheticDataGenerator(seed=seed + 1)  # distinct stream so the stress batch isn't a replay of the main one
    stress_batch = stress_gen.generate_stress_batch(stress_n)
    return main_batch, stress_batch


def generate_fee_leak_batch(seed: int = 42, n: int = 20) -> SyntheticBatch:
    """Independent of generate()'s main/stress batches -- a distinct rng stream (seed+2) so it's
    never a replay of either, and never mixed into their reported reconciliation accuracy, since
    fee-leak detection is a genuinely separate axis of analysis (see generate_fee_leak_batch on
    SyntheticDataGenerator, and app/feeleak/detector.py)."""
    gen = SyntheticDataGenerator(seed=seed + 2)
    return gen.generate_fee_leak_batch(n)


def generate_pending_batch(seed: int = 42, n: int = 10) -> PendingBatch:
    """Independent stream (seed+3) -- in-flight transactions for the forward settlement
    predictor, distinct from every other batch this generator produces since it's the only one
    without a Settlement at all (see generate_pending_batch on SyntheticDataGenerator, and
    app/forecast/predictor.py)."""
    gen = SyntheticDataGenerator(seed=seed + 3)
    return gen.generate_pending_batch(n)
