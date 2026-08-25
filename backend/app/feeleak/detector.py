"""Fee leak detector -- a second, independent axis of analysis alongside causal-chain
reconciliation, not a replacement for it and not folded into the matching engine's own
categories.

Why separate: a transaction can reconcile perfectly (ledger and settlement agree on the actual fee
deducted) while still being charged a fee inconsistent with the merchant's own contract --
standard reconciliation has no way to see this, because both sides of the reconciliation check
just reflect whatever was actually charged, correctly or not. In practice, without an
instrument-level audit against the rate card, a merchant's finance team has no signal at all that
anything is wrong -- the settlement "reconciles" and the overcharge is invisible. That's the real,
documented blind spot this module closes.

Framing note on the flagship pattern (a blended/flat rate charged on an instrument with a much
lower contracted rate, most visible on UPI): this detector checks the actual fee against THIS
MERCHANT'S OWN CONTRACTED rate (fee_schedule.py's FEE_PCT), not a blanket legal claim about what
MDR is "always" allowed to be on a given rail. That distinction is deliberate: the Payment and
Settlement Systems Act's zero-MDR mandate on UPI/RuPay debit (Section 10A, in force since January
2020) was amended by Parliament on 4 August 2026, replacing the blanket prohibition with a
government-notification framework under which the Centre can selectively exempt specific modes --
what's currently notified-exempt is no longer a fixed fact this system can safely assume. A
contract-vs-actual comparison is correct regardless of how that notification framework evolves; an
unconditional "UPI MDR is always illegal" claim would not be, and would have gone stale the week
this feature shipped.

No hidden ground truth is needed here (unlike the reconciliation pipeline's deliberately-hidden
true_label): the contracted rate is known reference data the system already has, the same way
narrator/tools.py's lookup_fee_schedule already exposes it to the agentic narrator. Detecting a
leak is a plain, deterministic arithmetic check against that reference data, not a classification
task.
"""

from pydantic import BaseModel, computed_field

from app.data_gen.fee_schedule import FEE_PCT, GST_RATE
from app.data_gen.schemas import Order, Payment

# Amounts at or below this are rounding noise, not a real leak -- mirrors chain/builder.py's own
# ROUNDING_EPSILON for the identical reason (FX/rounding drift isn't a finding worth surfacing).
LEAK_EPSILON = 100


class FeeLeakFinding(BaseModel):
    transaction_id: str
    rail: str
    pattern: str  # short machine-readable pattern id
    pattern_label: str  # human-readable, used in the dispute template
    contracted_fee: int
    actual_fee: int
    fee_variance: int  # actual - contracted; positive = overcharge
    contracted_gst: int  # 18% of the ACTUAL fee -- what GST should be, given whatever fee was really charged
    actual_gst: int
    gst_variance: int  # actual_gst - contracted_gst
    total_impact: int  # fee_variance + gst_variance -- the real rupee shortfall to the merchant
    dispute_template: str


def _dispute_template(transaction_id: str, rail: str, pattern_label: str, impact: int) -> str:
    return (
        f"Re: Fee discrepancy on transaction {transaction_id} ({rail}). {pattern_label}. "
        f"Contracted-vs-actual variance: Rs.{impact / 100:,.2f}. Requesting review and credit "
        f"against the contracted rate card for this instrument."
    )


def detect_fee_leak(order: Order, payment: Payment) -> FeeLeakFinding | None:
    contracted_fee = round(order.amount * FEE_PCT[order.rail])
    fee_variance = payment.fee_amount - contracted_fee
    # GST reference point is 18% of the fee actually charged, not the contracted fee -- isolates
    # "was GST computed correctly given whatever fee was really deducted" from "was the fee itself
    # correct", so a blended-rate overcharge (fee wrong, GST on that fee computed correctly) and a
    # GST-wrong-base error (fee correct, GST computed off gross instead) are distinguishable rather
    # than both collapsing into one muddled number.
    contracted_gst = round(payment.fee_amount * GST_RATE)
    gst_variance = payment.tax_amount - contracted_gst

    if abs(fee_variance) <= LEAK_EPSILON and abs(gst_variance) <= LEAK_EPSILON:
        return None

    if abs(fee_variance) > LEAK_EPSILON:
        pattern, label = (
            "blended_rate_overcharge",
            f"Fee deducted at a rate inconsistent with the contracted rate for {order.rail} "
            f"(a flat/blended rate appears to have been applied instead)",
        )
    else:
        pattern, label = (
            "gst_wrong_base",
            "GST appears to have been computed on the gross transaction amount instead of the gateway fee",
        )

    return FeeLeakFinding(
        transaction_id=order.order_id,
        rail=order.rail,
        pattern=pattern,
        pattern_label=label,
        contracted_fee=contracted_fee,
        actual_fee=payment.fee_amount,
        fee_variance=fee_variance,
        contracted_gst=contracted_gst,
        actual_gst=payment.tax_amount,
        gst_variance=gst_variance,
        total_impact=fee_variance + gst_variance,
        dispute_template=_dispute_template(order.order_id, order.rail, label, fee_variance + gst_variance),
    )


class FeeLeakReport(BaseModel):
    findings: list[FeeLeakFinding]

    # computed_field (not a bare @property) so these are included in .model_dump()/JSON output --
    # matching the identical pattern pipeline.py's BatchRunResult.transactions_per_second already
    # uses, for the same reason: a bare @property is invisible to Pydantic's own serialization.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_fee_recovery(self) -> int:
        """Sum of positive fee overcharges only -- an underpayment (fee_variance < 0) is a real
        finding worth surfacing but isn't 'recovery' in the same sense; kept out of this specific
        headline number rather than netted against it, which would understate genuine overcharges."""
        return sum(f.fee_variance for f in self.findings if f.fee_variance > 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_gst_correction(self) -> int:
        return sum(abs(f.gst_variance) for f in self.findings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def by_pattern(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.pattern] = counts.get(f.pattern, 0) + 1
        return counts


def run_fee_leak_detection(orders: list[Order], payments: list[Payment]) -> FeeLeakReport:
    payments_by_order = {p.order_id: p for p in payments}
    findings = []
    for order in orders:
        payment = payments_by_order.get(order.order_id)
        if payment is None:
            continue
        finding = detect_fee_leak(order, payment)
        if finding is not None:
            findings.append(finding)
    findings.sort(key=lambda f: -abs(f.total_impact))  # highest-impact findings first
    return FeeLeakReport(findings=findings)
