"""Defect shapes the matching engine was never designed for.

Every accuracy figure in RESULTS is scored against the categories in `generate.py`, and the matching
engine's rules were written knowing all of them. That makes those numbers a measure of
implementation rather than of generalisation: the same author wrote the exam and the student. The
`near_miss` variants already in the generator help, but they perturb shapes the rule KNOWS -- a
netting trap that misses by 40 paise is still a netting trap.

These four are absent from `TrueLabel` entirely. Each breaks an assumption the engine quietly relies
on:

    bank_fee_deduction     the settled amount is what the merchant contracted for. A correspondent
                           bank can levy its own charge, far outside any fee tolerance.
    stale_utr_reuse        a UTR identifies this settlement. Banks recycle reference strings, so a
                           real, well-formed UTR can name a batch that was already paid in full.
    post_dated_settlement  money never arrives before its own value date. Value-dating can put the
                           credit in the account first.
    double_settlement      a transaction settles at most once. A gateway bug can pay one
                           transaction inside two batches.

THE PASS CRITERION IS NOT ACCURACY. Escalating all of them passes, and should: refusing work it does
not understand is the designed behaviour. What fails is a single WRONG resolution, or a transaction
that appears in neither the resolved set nor the escalation queue. A system whose safety depends on
having anticipated every defect shape has not been shown to be safe at all.

`stale_utr_reuse` is the one that carries the weight. The other three make the amount or the date
look wrong, which is the easy direction to fail safely in. That one makes the evidence look right
while pointing at the wrong batch.
"""

from __future__ import annotations

from datetime import timedelta

from app.data_gen.fee_schedule import fee_and_tax
from app.data_gen.generate import SyntheticDataGenerator
from app.data_gen.schemas import SyntheticBatch

NOVEL_SHAPES = ("bank_fee_deduction", "stale_utr_reuse", "post_dated_settlement", "double_settlement")


def generate_novel_batch(seed: int = 909, per_shape: int = 8, controls: int = 12) -> tuple[SyntheticBatch, dict[str, str]]:
    """A batch of unfamiliar shapes plus in-distribution controls.

    The controls are the point of the second return value: a pipeline that simply escalated
    everything would trivially pass a no-wrong-match gate, so ordinary `clean_match` transactions
    are mixed in and are expected to resolve. Returns the batch and a map of transaction id to the
    shape that produced it, `clean_match` for the controls.
    """
    gen = SyntheticDataGenerator(seed=seed)
    orders, payments, refunds, settlements, ledgers, truths = [], [], [], [], [], []
    shape_of: dict[str, str] = {}

    def emit(parts, shape: str) -> None:
        o, p, r, s, l, g = parts
        orders.extend(o)
        payments.extend(p)
        refunds.extend(r)
        settlements.extend(s)
        ledgers.extend(l)
        truths.extend(g)
        for order in o:
            shape_of[order.order_id] = shape

    for _ in range(controls):
        emit(gen._gen_clean_match(), "clean_match")

    for _ in range(per_shape):
        emit(_bank_fee_deduction(gen), "bank_fee_deduction")
        emit(_stale_utr_reuse(gen), "stale_utr_reuse")
        emit(_post_dated_settlement(gen), "post_dated_settlement")
        emit(_double_settlement(gen), "double_settlement")

    batch = SyntheticBatch(
        orders=orders, payments=payments, refunds=refunds,
        settlements=settlements, ledger_entries=ledgers, ground_truth=truths,
    )
    return batch, shape_of


def _base(gen: SyntheticDataGenerator):
    rail = gen._pick_rail()
    amount = gen._rand_amount()
    created_at = gen._rand_created_at()
    order, payment, fee, tax = gen._build_order_and_payment(amount, rail, "INR", created_at)
    return rail, amount, created_at, order, payment, amount - fee - tax


def _bank_fee_deduction(gen):
    """A correspondent bank takes its own cut. The settlement is correct; the credit is not."""
    rail, amount, created_at, order, payment, net = _base(gen)
    wire_fee = gen.rng.randint(20_000, 60_000)  # far outside any fee-drift tolerance
    settlement = gen._build_settlement(payment.payment_id, rail, net - wire_fee, payment.captured_at)
    ledger = gen._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
    return [order], [payment], [], [settlement], [ledger], [_gt(order, "genuine_error", f"bank levied {wire_fee} paise")]


def _stale_utr_reuse(gen):
    """A real, well-formed UTR naming a batch that was already paid. The evidence looks right."""
    rail, amount, created_at, order, payment, net = _base(gen)
    settlement = gen._build_settlement(payment.payment_id, rail, net, payment.captured_at)
    settlement.utr = "UTR" + "".join(str(gen.rng.randint(0, 9)) for _ in range(9))
    settlement.settlement_batch_id = "batch_already_paid_in_full"
    ledger = gen._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
    return [order], [payment], [], [settlement], [ledger], [_gt(order, "genuine_error", "recycled UTR")]


def _post_dated_settlement(gen):
    """Value-dating puts the money in the account before the settlement's own date."""
    rail, amount, created_at, order, payment, net = _base(gen)
    settlement = gen._build_settlement(payment.payment_id, rail, net, payment.captured_at)
    settlement.settled_at = payment.captured_at - timedelta(days=2)
    ledger = gen._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
    return [order], [payment], [], [settlement], [ledger], [_gt(order, "genuine_error", "settled before capture")]


def _double_settlement(gen):
    """One transaction paid inside two batches. Each settlement is individually well formed."""
    rail, amount, created_at, order, payment, net = _base(gen)
    first = gen._build_settlement(payment.payment_id, rail, net, payment.captured_at)
    second = gen._build_settlement(payment.payment_id, rail, net, payment.captured_at + timedelta(days=1))
    ledger = gen._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
    return [order], [payment], [], [first, second], [ledger], [_gt(order, "genuine_error", "settled twice")]


def _gt(order, label, note):
    from app.data_gen.schemas import GroundTruthEntry

    return GroundTruthEntry(transaction_id=order.order_id, true_label=label, injected_by_you=True, internal_note=note)
