"""Causal chain builder.

For each order, stitches order -> payment -> fee/tax -> refund(s) -> settlement into one
structured object, and locates the *specific hop* where cumulative expected != actual first
diverges — this is what makes matching "causal chain matching, not row matching".

Every number here is computed only from that transaction's own records (order/payment/refund/
settlement/ledger) — never from the hidden ground truth. This module has no idea what the
"true" answer is; it only explains what the records say.
"""

from pydantic import BaseModel

from app.data_gen.fee_schedule import BASE_SLA_DAYS, SLA_TOLERANCE_DAYS
from app.data_gen.schemas import LedgerEntry, Order, Payment, Rail, Refund, Settlement, SyntheticBatch

# Deltas at or below this (in the smallest currency unit) are FX/rounding noise, not a real gap.
ROUNDING_EPSILON = 100


class Hop(BaseModel):
    name: str
    expected: int
    actual: int

    @property
    def delta(self) -> int:
        return self.actual - self.expected

    @property
    def diverges(self) -> bool:
        return self.actual != self.expected


class CausalChain(BaseModel):
    transaction_id: str  # == order_id
    rail: Rail
    merchant_id: str
    currency: str

    hops: list[Hop]

    computed_expected_settlement: int  # order -> capture -> fee -> tax -> refunds, all from records
    actual_settled_amount: int
    settlement_delta: int  # actual - computed; 0 means settlement is fully explained by known records

    ledger_expected_amount: int
    ledger_gap: int  # ledger_expected - actual_settled; 0 means merchant's books already match reality

    fee_amount: int
    tax_amount: int
    refund_total: int
    refund_ids: list[str]

    sla_nominal_days: int
    sla_actual_days: int
    within_sla: bool

    first_divergence_hop: str | None

    # source-row references, for audit-log drill-down and cross-transaction tool checks
    order_id: str
    payment_id: str
    settlement_id: str
    settlement_batch_id: str
    bank_narration: str | None = None  # the settlement's own free-text field, when present -- see narration_explained
    ledger_id: str


def _nominal_sla(rail: Rail) -> int:
    return BASE_SLA_DAYS[rail]


def build_chain(
    order: Order,
    payment: Payment,
    refunds: list[Refund],
    settlement: Settlement,
    ledger: LedgerEntry,
) -> CausalChain:
    refund_total = sum(r.amount for r in refunds)

    post_capture = payment.captured_amount
    post_fee = post_capture - payment.fee_amount
    post_tax = post_fee - payment.tax_amount
    post_refunds = post_tax - refund_total  # == computed_expected_settlement

    hops = [
        Hop(name="order_to_capture", expected=order.amount, actual=payment.captured_amount),
        Hop(name="capture_to_post_fee", expected=payment.captured_amount - payment.fee_amount, actual=post_fee),
        Hop(name="post_fee_to_post_tax", expected=post_fee - payment.tax_amount, actual=post_tax),
        Hop(name="post_tax_to_post_refunds", expected=post_tax - refund_total, actual=post_refunds),
        Hop(name="post_refunds_to_settlement", expected=post_refunds, actual=settlement.settled_amount),
    ]

    first_divergence = next((h.name for h in hops if h.diverges), None)

    nominal = _nominal_sla(order.rail)
    tolerance = SLA_TOLERANCE_DAYS[order.rail]

    return CausalChain(
        transaction_id=order.order_id,
        rail=order.rail,
        merchant_id=order.merchant_id,
        currency=order.currency,
        hops=hops,
        computed_expected_settlement=post_refunds,
        actual_settled_amount=settlement.settled_amount,
        settlement_delta=settlement.settled_amount - post_refunds,
        ledger_expected_amount=ledger.expected_amount,
        ledger_gap=ledger.expected_amount - settlement.settled_amount,
        fee_amount=payment.fee_amount,
        tax_amount=payment.tax_amount,
        refund_total=refund_total,
        refund_ids=[r.refund_id for r in refunds],
        sla_nominal_days=nominal,
        sla_actual_days=settlement.sla_days,
        within_sla=settlement.sla_days <= tolerance,
        first_divergence_hop=first_divergence,
        order_id=order.order_id,
        payment_id=payment.payment_id,
        settlement_id=settlement.settlement_id,
        settlement_batch_id=settlement.settlement_batch_id,
        ledger_id=ledger.ledger_id,
        bank_narration=settlement.bank_narration,
    )


def build_all_chains(batch: SyntheticBatch) -> dict[str, CausalChain]:
    payments_by_order = {p.order_id: p for p in batch.payments}
    refunds_by_payment: dict[str, list[Refund]] = {}
    for r in batch.refunds:
        refunds_by_payment.setdefault(r.payment_id, []).append(r)
    settlements_by_payment = {s.payment_id: s for s in batch.settlements}
    ledger_by_order = {l.order_id: l for l in batch.ledger_entries}

    chains: dict[str, CausalChain] = {}
    for order in batch.orders:
        payment = payments_by_order[order.order_id]
        refunds = refunds_by_payment.get(payment.payment_id, [])
        settlement = settlements_by_payment[payment.payment_id]
        ledger = ledger_by_order[order.order_id]
        chains[order.order_id] = build_chain(order, payment, refunds, settlement, ledger)
    return chains
