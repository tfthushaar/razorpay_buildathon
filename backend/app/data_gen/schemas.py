"""Entity schemas for the reconciliation pipeline.

Field names deliberately mirror Razorpay's real public API shapes where practical
(`entity` tag, `utr` on Settlement, `fee`/`tax`/`captured` on Payment, amounts in the
smallest currency unit — paise for INR, cents for USD — not decimal rupees) rather than
generic finance field names. See docs/ARCHITECTURE.md.
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
    "multiway_netting_trap",
    "narration_explained",
    "compound_delta",
]


class TrueCause(BaseModel):
    """One component of the ground-truth explanation for a `compound_delta` transaction.

    Distinct from `TrueLabel` on purpose. Every label above names a single mechanism, which is what
    made each of them eventually collapse to a rule -- one mechanism, one arithmetic signature, one
    detector. A compound delta has no single label to guess; the ground truth IS a set of components,
    and scoring asks whether every component of an explanation checked out rather than whether one
    string matched. See app/resolver/__init__.py.

    Mirrors app.resolver.causes.CauseCandidate field-for-field but is declared here rather than
    imported from it, so the data layer keeps no dependency on the resolver that scores it.
    """

    cause: str
    amount: int
    evidence_ref: str


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
    # Free text, real bank settlement files carry something like this (a narration/remarks field on
    # the NEFT/RTGS/UPI record) -- optional and unstructured on purpose. None for every existing
    # pattern (nothing reads it); populated, deliberately messy, only for narration_explained
    # (app/data_gen/generate.py::_gen_narration_explained) -- the one pattern this project has where
    # resolving it genuinely requires reading text, not a structured lookup at any scale.
    bank_narration: str | None = None


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
    linked_transaction_ids: list[str] = []  # set for multiway_netting_trap groups (2+ other members)
    true_causes: list[TrueCause] = []  # set for compound_delta: the real decomposition, in full
    # What the remittance advice actually SAYS about each charge type: "applied", "not_applied", or
    # absent from the mapping entirely when the text does not mention it. Distinct from true_causes,
    # which is what really happened -- the advice is deliberately partial, so a cause can be true and
    # unmentioned. Recorded so the reading step can be scored on its own, separately from whether the
    # right decomposition was ultimately chosen: that isolates language understanding from the
    # arithmetic bookkeeping that follows it, which is the only way to say whether a model reads this
    # text better than a regex does. Scoring-only, like every other field here.
    advice_mentions: dict[str, str] = {}
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
