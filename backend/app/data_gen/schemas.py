"""Entity schemas for the reconciliation pipeline.

Field names deliberately mirror Razorpay's real public API shapes where practical
(`entity` tag, `utr` on Settlement, `fee`/`tax`/`captured` on Payment, amounts in the
smallest currency unit — paise for INR, cents for USD — not decimal rupees) rather than
generic finance field names. See docs/track04-settlement-reconciliation-copilot.md §4.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

Rail = Literal["upi", "card", "netbanking"]

TrueLabel = Literal[
    "clean_match",
    "timing_lag",
    "fee_deduction",
    "partial_refund",
    "duplicate_refund",
    "netting_trap",
    "currency_rounding",
    "genuine_error",
]


class Order(BaseModel):
    order_id: str
    merchant_id: str
    amount: int  # smallest currency unit
    currency: str = "INR"
    created_at: datetime
    rail: Rail


class Payment(BaseModel):
    payment_id: str
    entity: Literal["payment"] = "payment"
    order_id: str
    status: Literal["captured", "failed", "pending", "partial"]
    captured: bool
    captured_amount: int
    fee_amount: int
    tax_amount: int
    gateway: str
    captured_at: datetime


class Refund(BaseModel):
    refund_id: str
    payment_id: str
    amount: int
    status: Literal["processed", "pending"] = "processed"
    created_at: datetime
    refund_type: Literal["full", "partial"]


class Settlement(BaseModel):
    settlement_id: str
    entity: Literal["settlement"] = "settlement"
    payment_id: str
    settled_amount: int
    settlement_batch_id: str
    utr: str
    rail: Rail
    settled_at: datetime
    sla_days: int


class LedgerEntry(BaseModel):
    ledger_id: str
    order_id: str
    expected_amount: int
    recorded_at: datetime


class GroundTruthEntry(BaseModel):
    """Hidden from the matching/narrator/calibration logic — used only to score decisions."""

    transaction_id: str  # == order_id
    true_label: TrueLabel
    injected_by_you: bool
    linked_transaction_id: Optional[str] = None  # set for netting_trap pairs
    internal_note: Optional[str] = None  # debugging aid only, never read by scoring code paths


class SyntheticBatch(BaseModel):
    orders: list[Order]
    payments: list[Payment]
    refunds: list[Refund]
    settlements: list[Settlement]
    ledger_entries: list[LedgerEntry]
    ground_truth: list[GroundTruthEntry]

    def transaction_ids(self) -> list[str]:
        return [o.order_id for o in self.orders]


class PendingBatch(BaseModel):
    """Orders + captured payments with deliberately NO settlement yet -- genuinely in-flight
    money, not the "closed" transactions every other batch in this project produces (which all
    have a Settlement by construction, since build_all_chains() requires one). This is what a
    forward settlement prediction actually predicts over; there is nothing to compare it against
    yet, by definition, until it's no longer pending."""

    orders: list[Order]
    payments: list[Payment]
