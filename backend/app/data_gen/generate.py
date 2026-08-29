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
    TrueCause,
)
from app.data_gen.subset_sum import find_other_subsets_that_cancel

# The rate sets `_gen_compound_delta` injects from. Imported from the resolver's own taxonomy rather
# than redeclared here on purpose: the point of a compound case is that its true decomposition is
# something the resolver could in principle have proposed, so the two sides must agree on which
# rates exist and how a citation to one is spelled. app/resolver/causes.py imports nothing from this
# package (only pydantic and typing), so this direction is safe.
from app.resolver.causes import PLAUSIBLE_FEE_RATES, STANDARD_GST_RATES, STANDARD_RESERVE_RATES, STANDARD_TDS_RATES

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
        bank_narration: str | None = None,
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
            bank_narration=bank_narration,
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

    def _gen_duplicate_refund_near_miss(self):
        """The same real pattern as `_gen_duplicate_refund` -- a refund legitimately issued once,
        applied twice in settlement -- but with one further, independent small perturbation on top
        (a realistic rounding/timing artifact, e.g. a paisa-level currency conversion residue),
        so the shortfall is CLOSE to the refund amount but not exactly equal to it.

        Exists to break the "shared author" problem the clean version can't test: `check_batch_
        anomalies`'s duplicate-refund check is `amount == abs(delta)`, an exact match, since the
        generator's own clean version never produces anything else -- the rule and the injector
        agree perfectly because the same author wrote both to the same exact-match definition.
        This variant is still, genuinely, a duplicate refund (same true_label) -- the rule's exact
        check will legitimately miss it; whether a real LLM's own arithmetic reasoning over the raw
        refund_total/settlement_delta numbers in its own prompt (see agent.py's `_describe_chain`,
        which exposes both directly) can recognize an approximate match the tool itself can't
        confirm is the entire point -- measured, not assumed, in scripts/generate_held_out_variant_
        evidence.py."""
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
        # same double-application as the clean version, PLUS a small independent perturbation --
        # small enough that a reader would still recognize "this is basically the refund amount",
        # large enough that it never coincidentally lands on exactly 0 (which would make it
        # indistinguishable from the clean version) or on another real refund amount by chance.
        near_miss_epsilon = self.rng.choice([-1, 1]) * self.rng.randint(20, 150)
        net_after_refund = net_before_refund - (2 * refund_amount) + near_miss_epsilon
        settlement = self._build_settlement(payment.payment_id, rail, net_after_refund, payment.captured_at)
        ledger = self._build_ledger(order.order_id, net_before_refund - refund_amount, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="duplicate_refund",
            injected_by_you=True,
            internal_note=(
                f"refund {refund.refund_id} of {refund_amount} applied twice in settlement, "
                f"plus a {near_miss_epsilon} near-miss perturbation -- the rule's exact-match check "
                f"structurally cannot confirm this one"
            ),
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

    def _gen_netting_trap_near_miss(self):
        """The same real pattern as `_gen_netting_trap` -- two transactions individually wrong,
        batch-level near-cancellation -- but B's delta is `+x + epsilon`, not exactly `+x`, so
        `check_batch_anomalies`'s exact-opposite check (`other.settlement_delta == -delta`) fails
        by construction. Still genuinely a netting_trap (same true_label, same cross-link) -- the
        held-out variant this project's own "shared author" limitation needed: the clean version's
        rule and injector agree perfectly because the same author wrote both to the same exact-match
        definition, so this variant is the one that actually tests whether a real LLM's own
        arithmetic reasoning generalizes past that brittleness, not just repeats it."""
        rail = self._pick_rail()
        created_at = self._rand_created_at()
        x = self.rng.choice([50, 100, 150, 200]) * 100  # paise
        near_miss_epsilon = self.rng.choice([-1, 1]) * self.rng.randint(20, 150)
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
            payment_b.payment_id, rail, net_b + x + near_miss_epsilon, payment_b.captured_at, sla_days=shared_sla, batch_id_override=shared_batch_id
        )
        ledger_b = self._build_ledger(order_b.order_id, net_b, created_at + timedelta(minutes=5))

        gt_a = GroundTruthEntry(
            transaction_id=order_a.order_id,
            true_label="netting_trap",
            injected_by_you=True,
            linked_transaction_id=order_b.order_id,
            internal_note=f"short by {x}, near-nets against {order_b.order_id} (off by {near_miss_epsilon})",
        )
        gt_b = GroundTruthEntry(
            transaction_id=order_b.order_id,
            true_label="netting_trap",
            injected_by_you=True,
            linked_transaction_id=order_a.order_id,
            internal_note=f"over by {x + near_miss_epsilon}, near-nets against {order_a.order_id} (off by {near_miss_epsilon})",
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

    # A real bank settlement file's narration/remarks field, deliberately varied -- different
    # abbreviation styles, word orders, and separators, so no single fixed keyword or regex reliably
    # catches all of them, the way `_gen_genuine_error`'s templates for previous patterns never
    # needed to defeat a hypothetical rule (mock never reads this field at all, so it fails
    # structurally regardless -- the variety here is about making the reading task itself
    # realistic, not about tricking a rule that was never built).
    _NARRATION_TEMPLATES = [
        "FEE WAIVED - PROMO {code} APPLIED",
        "PROMO{code}-NOFEECHG-SETTLEMENT",
        "chgs waived promo ref {code} pls ignore fee diff",
        "Settlement adj: merchant fee exempted (campaign {code})",
        "FEEEXEMPT/{code}/AUTOAPPLIED",
        "Note: fee not deducted this cycle - promo code {code} - contact support if discrepancy",
        "{code}-FEEWAIVER-Q{quarter}-PROCESSED",
        "waived chgs ({code}) refer promo terms",
    ]

    def _gen_narration_explained(self):
        """A settlement delta that looks exactly like an unexplained genuine_error from the
        structured data alone (fee+tax were not deducted, for a real business reason: a promotional
        fee waiver) -- but the bank's own settlement narration field, read as free text, actually
        explains it. No structured field anywhere records "this transaction had its fee waived" --
        the only place that fact exists is this messy text, so no rule at any scale (not even the
        combinatorial multiway_netting_trap machinery, which only ever looks at deltas) can resolve
        this; only genuine reading comprehension can. `narrate_mock` never calls `read_bank_
        narration` at all, so it fails structurally here, the same posture as multiway_netting_trap
        -- see scripts/generate_narration_explained_evidence.py for the real, measured comparison
        against a provider that actually reads the text."""
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        net = amount - fee - tax
        # fee+tax not deducted this cycle -- the promo waiver's real, arithmetic effect
        delta = fee + tax
        code = f"PR{self.rng.randint(1000, 9999)}"
        template = self.rng.choice(self._NARRATION_TEMPLATES)
        narration = template.format(code=code, quarter=self.rng.randint(1, 4))
        settlement = self._build_settlement(payment.payment_id, rail, net + delta, payment.captured_at, bank_narration=narration)
        ledger = self._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="narration_explained",
            injected_by_you=True,
            internal_note=f"delta={delta} (fee {fee} + tax {tax} waived), explained only by bank_narration={narration!r}",
        )
        return [order], [payment], [], [settlement], [ledger], [gt]

    # Remittance-advice phrasing, per cause, in two registers. POSITIVE phrases assert a component
    # really was applied this cycle; NEGATIVE phrases mention the same component and its vocabulary
    # while saying it was NOT applied -- denied, reversed, exempted, deferred to a later cycle, or
    # merely proposed. Both registers use the same keywords on purpose.
    #
    # This is what makes the narration a genuine reading task rather than a lookup. A keyword scan
    # sees "TDS" and "RSV HOLD" in both registers and cannot tell them apart; resolving them needs
    # negation ("nil deduction"), scope ("50% partial"), and tense ("effective next cycle") to be
    # read as what they are. app/resolver/keyword_baseline.py implements that keyword scan as an
    # honest, standing comparator rather than leaving "a rule couldn't do this" as an assertion --
    # and it is a real rule written to win, not a strawman.
    _CAUSE_PHRASES: dict[str, dict[str, list[str]]] = {
        "fee_rate_mismatch": {
            "pos": ["MDR applied @ {rate}pct", "chgs levied at {rate}% this cycle", "mdr {rate} pct debited"],
            "neg": ["MDR revision to {rate}pct effective next cycle", "rate change to {rate}% NOT applied this cycle", "proposed mdr {rate}pct - pending approval"],
        },
        "gst_on_fee_mismatch": {
            "pos": ["GST @{rate}pct on chgs", "tax on fee computed {rate}%"],
            "neg": ["GST slab revision pending - no change this cycle", "gst @{rate}pct queried, not adjusted"],
        },
        "duplicate_refund": {
            "pos": ["RFND {ref} redebited", "refund {ref} reapplied in error", "rfnd{ref} deducted again"],
            "neg": ["duplicate refund {ref} reversal CANCELLED", "rfnd {ref} already netted - not re-deducted", "refund {ref} flagged, no second debit"],
        },
        "tds_deduction": {
            "pos": ["TDS @{rate}pct withheld u/s 194O", "tds {rate}% deducted at source"],
            "neg": ["TDS exemption cert on file - nil deduction", "tds {rate}pct to commence next FY", "TDS NOT withheld - lower deduction certificate"],
        },
        "rolling_reserve": {
            "pos": ["RSV HOLD {rate}pct APPLIED", "rolling reserve {rate}% withheld", "resv {rate}pct retained"],
            "neg": ["rolling reserve released - no hold this cycle", "reserve {rate}pct applies from next settlement", "RSV HOLD WAIVED FOR THIS BATCH"],
        },
        "fx_rounding": {
            "pos": ["FX rnd adj", "conv rounding applied"],
            "neg": ["no fx adj this cycle", "fx rounding suppressed"],
        },
        "promotional_waiver": {
            "pos": ["FEE WAIVED - PROMO {ref} APPLIED", "chgs waived promo ref {ref}", "FEEEXEMPT/{ref}/AUTOAPPLIED"],
            "neg": [
                "fee waiver request DENIED - standard charges applied",
                "promo {ref} expired, no exemption this cycle",
                "waiver applies from next settlement cycle",
                "partial waiver 50pct + GST adj ref {ref} - not applied pending review",
            ],
        },
    }
    # A SECOND phrase bank the keyword baseline's negation-cue list has never seen.
    #
    # This exists because the keyword rule reads the bank above at 96.1%, and it is worth being blunt
    # about why: I wrote both the phrases and the cue list that parses them. That is the shared-author
    # problem this project has already been caught by once, in its purest form -- a rule scoring
    # against text its own author wrote is measuring authorship, not reading.
    #
    # So the fair test is held-out phrasing, and the design of it matters. The CAUSE-identifying
    # vocabulary is deliberately kept recognisable (TDS, RSV, GST, MDR, refund, waiver are domain
    # terms that appear however the sentence is built) -- changing those too would let the rule fail
    # merely by not knowing a synonym, which is a cheap win and not the interesting question. What
    # changes is only how "applied" and "not applied" are EXPRESSED: abeyance, rescinded, held over,
    # zero-rated, struck off, stood down, lapsed, contra -- real settlement-advice idiom, none of it
    # matching any cue in app/resolver/keyword_baseline.py. That isolates negation, scope and tense
    # comprehension, which is the actual hard part and the only part a language model should be
    # expected to be better at.
    _CAUSE_PHRASES_HELDOUT: dict[str, dict[str, list[str]]] = {
        "fee_rate_mismatch": {
            "pos": ["MDR {rate}pct raised on this txn", "comm. debited mdr @{rate}pct", "svc chg mdr {rate}pct posted"],
            "neg": ["MDR {rate}pct held over to the next run", "mdr revision stood down for this batch", "MDR {rate}pct in abeyance"],
        },
        "gst_on_fee_mismatch": {
            "pos": ["GST {rate}pct raised on chgs", "gst @{rate}pct posted contra"],
            "neg": ["GST {rate}pct zero-rated for the period", "gst revision lapsed", "GST {rate}pct struck off prior to posting"],
        },
        "duplicate_refund": {
            "pos": ["rfnd {ref} re-raised against this settlement", "refund {ref} debited a second time in error"],
            "neg": ["refund {ref} re-debit rescinded", "rfnd {ref} second posting struck off", "refund {ref} contra stood down"],
        },
        "tds_deduction": {
            "pos": ["TDS {rate}pct effected u/s 194O", "tds withholding {rate}pct posted"],
            "neg": ["TDS nil for this run", "tds {rate}pct zero-rated for the period", "TDS {rate}pct lapsed", "tds in abeyance"],
        },
        "rolling_reserve": {
            "pos": ["RSV {rate}pct retained at source", "reserve {rate}pct booked contra", "resv {rate}pct raised"],
            "neg": ["RSV HOLD in abeyance", "reserve {rate}pct rescinded", "resv held over to the next run", "reserve stood down for this batch"],
        },
        "fx_rounding": {
            "pos": ["fx conv adj raised", "fx rounding posted"],
            "neg": ["fx adj in abeyance", "fx rounding stood down"],
        },
        "promotional_waiver": {
            "pos": ["fee waiver {ref} effected in full", "promo {ref} chgs abated", "waiver {ref} posted contra"],
            "neg": ["fee waiver {ref} rescinded", "promo {ref} lapsed", "waiver {ref} held over to the next run", "promo {ref} struck off prior to posting"],
        },
    }
    _NARRATION_SEPARATORS = [" | ", "; ", " / ", " -- ", ", "]

    @staticmethod
    def _advice_tokens_from_ref(evidence_ref: str) -> tuple[str, str]:
        """The (rate, ref) text a remittance-advice phrase should quote for a given citation.

        A positive phrase has to quote the component's REAL rate/identifier, or the text carries no
        information and the whole channel is decorative. Everything needed is already encoded in the
        evidence_ref (`tds:0.0100`, `fee_schedule:upi@0.0045`, `refund:rfnd_x`), so it is read back
        out here rather than threaded separately and risking the two drifting apart."""
        kind, _, rest = evidence_ref.partition(":")
        if kind in ("tds", "reserve", "gst"):
            return f"{float(rest) * 100:g}", ""
        if kind == "fee_schedule":
            _, _, rate = rest.partition("@")
            return f"{float(rate) * 100:g}", ""
        if kind == "refund":
            return "", rest
        return "", ""

    def _compose_remittance_advice(
        self,
        true_causes: list[TrueCause],
        absent_causes: list[str],
        promo_code: str,
        mentions: dict[str, str] | None = None,
        held_out: bool = False,
    ) -> str:
        """Messy free-text remittance advice: some true components asserted with their REAL rates,
        some absent components mentioned with a plausible-but-wrong rate only to be denied, deferred,
        or left pending -- in arbitrary order, with inconsistent separators and casing.

        Deliberately PARTIAL: only about 60% of true components get a positive mention, so the text
        is never a complete oracle. Reading it well narrows the problem; it does not remove the
        arithmetic. That is the honest shape of a real remittance advice, and it keeps this from
        becoming a second lookup task wearing a free-text costume."""
        fragments: list[str] = []
        for c in true_causes:
            if self.rng.random() < 0.6:
                rate, ref = self._advice_tokens_from_ref(c.evidence_ref)
                if c.cause == "promotional_waiver":
                    ref = promo_code
                fragments.append(self._phrase_for(c.cause, "pos", rate, ref, held_out=held_out))
                if mentions is not None:
                    mentions[c.cause] = "applied"
        for cause in absent_causes:
            if self.rng.random() < 0.6:
                if mentions is not None:
                    mentions[cause] = "not_applied"
                # a plausible rate that is NOT this transaction's true one -- so a rule that scrapes
                # any rate out of the text and believes it lands on a wrong number, not merely a
                # missing one
                wrong_rate = f"{self.rng.choice([0.18, 0.25, 0.3, 0.45, 0.5, 1.0, 2.0, 5.0]):g}"
                fragments.append(self._phrase_for(cause, "neg", wrong_rate, f"rfnd_{self.rng.randrange(16**8):08x}", held_out=held_out))
        if not fragments:
            fragments.append("settlement processed - see statement")
        self.rng.shuffle(fragments)
        return self.rng.choice(self._NARRATION_SEPARATORS).join(fragments)

    def _phrase_for(self, cause: str, register: str, rate: str, ref: str, held_out: bool = False) -> str:
        bank = self._CAUSE_PHRASES_HELDOUT if held_out else self._CAUSE_PHRASES
        template = self.rng.choice(bank[cause][register])
        return template.format(rate=rate or "0.5", ref=ref or f"PR{self.rng.randint(1000, 9999)}")

    def _gen_compound_delta(self, n_causes: int | None = None, rounding_noise: int = 3, held_out_phrasing: bool = False):
        """A settlement delta produced by SEVERAL causes at once, the way real settlement arithmetic
        actually works -- a fee charged at the wrong contracted rate, plus a refund applied a second
        time, plus a rolling reserve withheld, plus FX rounding, all landing in one net number.

        This is the generator the residual architecture needs, and it exists because every
        single-mechanism category in this file eventually collapsed to a rule. The reason is
        structural: one mechanism means one arithmetic signature, and an exact-match search over one
        signature has exactly one answer, so a hash table finds it. Compounding breaks that in a way
        that is not a trick -- it is just what a real settlement line is.

        Two properties do the work, and both are deliberate:

        1. `n_causes` >= 2. The observed delta is a SUM, so recovering it means partitioning a number
           rather than looking it up, and the number of ways to partition grows combinatorially.
        2. `rounding_noise`. Real percentage withholdings are rounded to the paise independently at
           several steps, so the components never sum to the observed delta *exactly* and an honest
           resolver has to search with a tolerance. The default is 3 paise -- genuinely
           rounding-scale, deliberately not a large fudge factor, because a large one would make the
           ambiguity this generator produces an artefact of the fudge rather than of the task.

        It is worth being precise about which of those two is actually doing the work, because the
        obvious objection to this whole design is "you manufactured the ambiguity with a tolerance
        knob." Measured directly (scripts/generate_residual_evidence.py), at `rounding_noise=0` and
        tolerance 0 -- exact integer arithmetic, no tolerance whatsoever -- 45 of 60 compound cases
        are STILL under-determined, at a median of 3.5 valid decompositions each. Compositionality
        alone is sufficient; tolerance amplifies it (median k rises to ~19 at a 10-paise tolerance)
        but is not the cause. The full curve is published rather than a single flattering row.

        Given its own dedicated settlement batch, the same bounding `_gen_multiway_netting_trap`
        already uses, so the resolver's netting hypotheses stay limited to this case's own small
        group rather than every transaction in the run -- a real choice that makes the measured
        ambiguity counts SMALLER (and so the model's job harder), disclosed rather than quiet.
        """
        rail = self._pick_rail()
        amount = self._rand_amount()
        created_at = self._rand_created_at()
        order, payment, fee, tax = self._build_order_and_payment(amount, rail, "INR", created_at)
        batch_id = f"batch_compound_{self._new_id('cmp', 6)}"

        n_causes = n_causes if n_causes is not None else self.rng.randint(2, 4)
        contracted_rate = FEE_PCT[rail]
        refunds: list[Refund] = []
        narration: str | None = None
        causes: list[TrueCause] = []

        # Which mechanisms are available to compose. Sampled without replacement so the same
        # mechanism never appears twice in one transaction -- the same physical-exclusivity rule the
        # resolver's enumerator applies (app/resolver/enumerate.py), kept consistent on both sides so
        # ground truth is always something the resolver could in principle have proposed.
        menu = ["fee_rate_mismatch", "gst_on_fee_mismatch", "duplicate_refund", "tds_deduction", "rolling_reserve", "fx_rounding", "promotional_waiver"]
        chosen = self.rng.sample(menu, k=min(n_causes, len(menu)))
        absent = [c for c in menu if c not in chosen]
        promo_code = f"PR{self.rng.randint(1000, 9999)}"

        for cause in chosen:
            if cause == "fee_rate_mismatch":
                rate = self.rng.choice([r for r in PLAUSIBLE_FEE_RATES if abs(r - contracted_rate) > 1e-9])
                contribution = round(amount * contracted_rate) - round(amount * rate)
                causes.append(TrueCause(cause=cause, amount=contribution, evidence_ref=f"fee_schedule:{rail}@{rate:.4f}"))
            elif cause == "gst_on_fee_mismatch":
                rate = self.rng.choice([r for r in STANDARD_GST_RATES if abs(r - GST_RATE) > 1e-9])
                contribution = round(fee * GST_RATE) - round(fee * rate)
                causes.append(TrueCause(cause=cause, amount=contribution, evidence_ref=f"gst:{rate:.2f}"))
            elif cause == "duplicate_refund":
                refund_amount = round(amount * self.rng.choice([0.15, 0.2, 0.25, 0.3]))
                refund = Refund(
                    refund_id=self._new_id("rfnd"),
                    payment_id=payment.payment_id,
                    amount=refund_amount,
                    created_at=payment.captured_at + timedelta(hours=self.rng.randint(2, 40)),
                    refund_type="partial",
                )
                refunds.append(refund)
                causes.append(TrueCause(cause=cause, amount=-refund_amount, evidence_ref=f"refund:{refund.refund_id}"))
            elif cause == "tds_deduction":
                rate = self.rng.choice(STANDARD_TDS_RATES)
                causes.append(TrueCause(cause=cause, amount=-round(amount * rate), evidence_ref=f"tds:{rate:.4f}"))
            elif cause == "rolling_reserve":
                rate = self.rng.choice(STANDARD_RESERVE_RATES)
                causes.append(TrueCause(cause=cause, amount=-round(amount * rate), evidence_ref=f"reserve:{rate:.4f}"))
            elif cause == "fx_rounding":
                causes.append(TrueCause(cause=cause, amount=self.rng.choice([-3, -2, -1, 1, 2, 3]), evidence_ref="fx:INR"))
            elif cause == "promotional_waiver":
                causes.append(TrueCause(cause=cause, amount=fee + tax, evidence_ref="narration:PLACEHOLDER"))

        # Every compound settlement carries a remittance advice, whether or not a waiver is among its
        # causes -- if the text appeared only when a waiver was real, its mere PRESENCE would be the
        # giveaway and a one-line rule would win again. It is always there, and always partly about
        # things that did not happen.
        advice_mentions: dict[str, str] = {}
        narration = self._compose_remittance_advice(causes, absent, promo_code, mentions=advice_mentions, held_out=held_out_phrasing)

        # net expected from the records alone: order - fee - tax - any refund actually on file
        net = amount - fee - tax - sum(r.amount for r in refunds)
        noise = self.rng.randint(-rounding_noise, rounding_noise) if rounding_noise else 0
        delta = sum(c.amount for c in causes) + noise

        settlement = self._build_settlement(
            payment.payment_id, rail, net + delta, payment.captured_at, batch_id_override=batch_id, bank_narration=narration
        )
        # the waiver's evidence_ref can only be written once the settlement it cites exists
        for c in causes:
            if c.evidence_ref == "narration:PLACEHOLDER":
                c.evidence_ref = f"narration:{settlement.settlement_id}"

        ledger = self._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        gt = GroundTruthEntry(
            transaction_id=order.order_id,
            true_label="compound_delta",
            injected_by_you=True,
            true_causes=causes,
            advice_mentions=advice_mentions,
            internal_note=(
                f"delta={delta} from {len(causes)} causes "
                f"({', '.join(f'{c.cause}={c.amount}' for c in causes)}) + rounding noise {noise}"
            ),
        )
        return [order], [payment], refunds, [settlement], [ledger], [gt]

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
        enable_held_out_variants: bool = False,
        enable_narration_explained: bool = False,
        enable_compound_delta: bool = False,
        held_out_advice_phrasing: bool = False,
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
        preserves.

        `enable_held_out_variants` defaults False, same reasoning. When on, a fraction of
        netting_trap/duplicate_refund instances become the near-miss versions (`_gen_netting_trap_
        near_miss`/`_gen_duplicate_refund_near_miss`) -- same true_label, same slot cost as their
        clean counterparts, but with a small perturbation `check_batch_anomalies`'s own exact-match
        logic cannot confirm. This exists to break the "shared author" problem the clean versions
        can't test (the rule and the injector agree perfectly on those because the same author wrote
        both to the same exact-match definition) -- see scripts/generate_held_out_variant_evidence.py
        for the real, measured comparison.

        `enable_narration_explained` defaults False, same reasoning. When on, a fraction of the
        ambiguous share's genuine_error slots become `_gen_narration_explained` instead -- a
        different kind of genuine judgment from every other pattern in this generator: the delta is
        explained only by free text (a bank settlement narration field), never by any structured
        field or delta-arithmetic a rule could check at any scale, not even the combinatorial
        multiway_netting_trap machinery."""
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
                use_near_miss = enable_held_out_variants and self.rng.random() < 0.5
                batches.append(self._gen_netting_trap_near_miss() if use_near_miss else self._gen_netting_trap())
                remaining_adversarial -= 2
            else:
                use_near_miss = enable_held_out_variants and self.rng.random() < 0.5
                batches.append(self._gen_duplicate_refund_near_miss() if use_near_miss else self._gen_duplicate_refund())
                remaining_adversarial -= 1

        for _ in range(remaining_ambiguous):
            roll = self.rng.random()
            if enable_compound_delta and roll < 0.5:
                batches.append(self._gen_compound_delta(held_out_phrasing=held_out_advice_phrasing))
            elif enable_narration_explained and roll < 0.67:
                batches.append(self._gen_narration_explained())
            else:
                batches.append(self._gen_genuine_error())

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
    seed: int = 42,
    main_n: int = 120,
    stress_n: int = 40,
    clean_ratio: float = 0.60,
    enable_multiway_netting: bool = False,
    enable_held_out_variants: bool = False,
    enable_narration_explained: bool = False,
    enable_compound_delta: bool = False,
    held_out_advice_phrasing: bool = False,
) -> tuple[SyntheticBatch, SyntheticBatch]:
    gen = SyntheticDataGenerator(seed=seed)
    main_batch = gen.generate_main_batch(
        main_n,
        clean_ratio=clean_ratio,
        enable_multiway_netting=enable_multiway_netting,
        enable_held_out_variants=enable_held_out_variants,
        enable_narration_explained=enable_narration_explained,
        enable_compound_delta=enable_compound_delta,
        held_out_advice_phrasing=held_out_advice_phrasing,
    )
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
