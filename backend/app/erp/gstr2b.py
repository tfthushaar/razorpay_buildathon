"""GSTR-2B tax-line matcher (upgrade build Phase 6, "tax-line matcher" track direction, completed):
GSTR-2B's real structure was verified via a live search before writing this (not assumed) --
eligible vs. ineligible ITC sections, invoice-level fields (document type, document number,
document date), a blocked-credit reason under Section 17(5) when ineligible, and supplier-wise
detail. This module has two distinct jobs, matching that structure:

1. `to_gstr2b_format`: reshapes THIS project's own books (the GST-on-fee ITC already separated onto
   its own ledger line by app/erp/journal.py) into that same eligible/ineligible-ITC schema -- a
   structured export, not a comparison.
2. `match_against_gstr2b`: compares our own books against a SIMULATED GSTR-2B, as if auto-drafted
   from the payment gateway's own GSTR-1/IFF filing -- the same "compare against an independent
   reference" pattern app/feeleak/detector.py already established, applied to tax reconciliation
   instead of fee reconciliation. A real reconciliation always needs two independent sides; this
   project only has one (its own books), which is exactly why the counterpart is simulated rather
   than assumed to always agree.

The single supplier this project's own data model represents is the payment gateway itself (e.g.
Razorpay) -- there is no multi-supplier scenario here, so `SUPPLIER_GSTIN` is one fictitious,
syntactically-shaped placeholder (never a real registered GSTIN), not a real lookup.

Honesty note on the "Ineligible" / Section 17(5) path: gateway processing fees are an ordinary
input service and are, in the overwhelming realistic case, ITC-eligible -- Section 17(5)'s blocked
categories (motor vehicles, employee benefits, and similar) don't apply to this project's actual
scenario. The synthetic generator below injects a small, clearly-labeled synthetic Ineligible case
purely so the matcher's blocked-credit code path is real and exercised, not to claim this is a
realistic outcome for a payment-gateway fee specifically.
"""

from __future__ import annotations

import random
from datetime import date
from typing import Literal

from pydantic import BaseModel, computed_field

from app.chain.builder import CausalChain

SUPPLIER_GSTIN = "27AAAAA0000A1Z5"  # fictitious, syntactically-shaped placeholder -- never a real registered GSTIN
ITC_MATCH_EPSILON = 100  # smallest-currency-unit tolerance, mirrors feeleak/detector.py's LEAK_EPSILON

# Deliberately small and disjoint -- these sum to 16%, leaving 84% of eligible-ITC transactions to
# match cleanly, a realistic "mostly matches, a real minority of exceptions" shape rather than either
# a suspiciously perfect match rate or an implausibly high exception rate.
_NOT_YET_FILED_RATE = 0.08
_AMOUNT_MISMATCH_RATE = 0.05
_BLOCKED_CREDIT_RATE = 0.03


class OwnItcEntry(BaseModel):
    """One of our own books' ITC-eligible lines, reshaped into GSTR-2B's own field names."""

    transaction_id: str
    supplier_gstin: str
    document_type: Literal["Invoice"]
    document_number: str
    document_date: str  # ISO date
    taxable_value: int  # the fee amount ITC is charged on
    itc_amount: int  # our own tax_amount


class Gstr2bEntry(BaseModel):
    """One line of a SIMULATED GSTR-2B statement -- the government's own record of what this
    merchant is entitled to claim, independent of what our books say."""

    transaction_id: str  # simulation-only join key; a real GSTR-2B is matched by GSTIN + document
    # number, not a payment-gateway-internal transaction id, which doesn't appear on a real filing
    supplier_gstin: str
    document_type: Literal["Invoice"]
    document_number: str
    document_date: str
    taxable_value: int
    itc_amount: int
    itc_availability: Literal["Eligible", "Ineligible"]
    ineligible_reason: str | None = None  # Section 17(5), only when Ineligible


class Gstr2bException(BaseModel):
    transaction_id: str
    kind: Literal["missing_in_gstr2b", "amount_mismatch", "blocked_credit"]
    our_itc_amount: int
    gstr2b_itc_amount: int | None
    detail: str


class Gstr2bMatchReport(BaseModel):
    matched_count: int
    matched_itc_amount: int
    exceptions: list[Gstr2bException]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def exception_itc_amount(self) -> int:
        return sum(e.our_itc_amount for e in self.exceptions)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.exceptions:
            counts[e.kind] = counts.get(e.kind, 0) + 1
        return counts


def _document_number(transaction_id: str) -> str:
    return f"INV-{transaction_id[-8:].upper()}"


def to_gstr2b_format(chains: dict[str, CausalChain], document_date: date | None = None) -> dict:
    """Our own books, reshaped into GSTR-2B's own eligible/ineligible-ITC schema. `document_date`
    defaults to today, same convention as erp/exporters.py's voucher_date/journal_date -- individual
    transactions don't carry a real calendar date on CausalChain (only day-counts), so one filing-
    period date is applied across the batch, matching how the existing Tally/Zoho exporters already
    handle this exact gap."""
    document_date = document_date or date.today()
    date_str = document_date.isoformat()

    entries = [
        OwnItcEntry(
            transaction_id=chain.transaction_id,
            supplier_gstin=SUPPLIER_GSTIN,
            document_type="Invoice",
            document_number=_document_number(chain.transaction_id),
            document_date=date_str,
            taxable_value=chain.fee_amount,
            itc_amount=chain.tax_amount,
        )
        for chain in chains.values()
        if chain.tax_amount > 0
    ]

    return {
        "supplier_gstin": SUPPLIER_GSTIN,
        "part_a_eligible_itc": [e.model_dump() for e in entries],
        # Our own books never record an ITC line as ineligible by construction (see module
        # docstring) -- always empty, present for schema completeness against a real GSTR-2B export.
        "part_b_ineligible_itc": [],
        "total_eligible_itc": sum(e.itc_amount for e in entries),
    }


def generate_simulated_gstr2b(chains: dict[str, CausalChain], seed: int = 0, document_date: date | None = None) -> list[Gstr2bEntry]:
    """A simulated counterparty statement -- deliberately injects a few realistic mismatches against
    our own books (see the module docstring for why a real reconciliation needs an independent
    second side, and the rate constants above for exactly how often each kind fires)."""
    document_date = document_date or date.today()
    date_str = document_date.isoformat()
    rng = random.Random(seed)

    entries: list[Gstr2bEntry] = []
    for chain in chains.values():
        if chain.tax_amount <= 0:
            continue
        roll = rng.random()

        if roll < _NOT_YET_FILED_RATE:
            # the supplier hasn't filed this invoice in their own GSTR-1/IFF yet -- a real, common
            # timing gap, simulated by simply omitting this transaction from GSTR-2B entirely.
            continue

        itc_amount = chain.tax_amount
        availability: Literal["Eligible", "Ineligible"] = "Eligible"
        reason = None

        if roll < _NOT_YET_FILED_RATE + _AMOUNT_MISMATCH_RATE:
            # the supplier reported a slightly different ITC amount than what we recorded -- their
            # own rounding, or a correction filed after we posted our own books.
            itc_amount = chain.tax_amount + rng.choice([-500, -300, 300, 500])
        elif roll < _NOT_YET_FILED_RATE + _AMOUNT_MISMATCH_RATE + _BLOCKED_CREDIT_RATE:
            availability = "Ineligible"
            reason = "Section 17(5) — blocked credit (synthetic, illustrative only; not a realistic outcome for a payment-gateway fee, see module docstring)"

        entries.append(
            Gstr2bEntry(
                transaction_id=chain.transaction_id,
                supplier_gstin=SUPPLIER_GSTIN,
                document_type="Invoice",
                document_number=_document_number(chain.transaction_id),
                document_date=date_str,
                taxable_value=chain.fee_amount,
                itc_amount=itc_amount,
                itc_availability=availability,
                ineligible_reason=reason,
            )
        )
    return entries


def match_against_gstr2b(chains: dict[str, CausalChain], simulated_gstr2b: list[Gstr2bEntry]) -> Gstr2bMatchReport:
    by_txn = {e.transaction_id: e for e in simulated_gstr2b}
    matched_count = 0
    matched_itc_amount = 0
    exceptions: list[Gstr2bException] = []

    for chain in chains.values():
        if chain.tax_amount <= 0:
            continue
        entry = by_txn.get(chain.transaction_id)

        if entry is None:
            exceptions.append(
                Gstr2bException(
                    transaction_id=chain.transaction_id,
                    kind="missing_in_gstr2b",
                    our_itc_amount=chain.tax_amount,
                    gstr2b_itc_amount=None,
                    detail="No corresponding entry in GSTR-2B — the supplier may not have filed this invoice yet; ITC claim is at risk until it appears.",
                )
            )
            continue

        if entry.itc_availability == "Ineligible":
            exceptions.append(
                Gstr2bException(
                    transaction_id=chain.transaction_id,
                    kind="blocked_credit",
                    our_itc_amount=chain.tax_amount,
                    gstr2b_itc_amount=entry.itc_amount,
                    detail=entry.ineligible_reason or "Marked ineligible in GSTR-2B.",
                )
            )
            continue

        if abs(entry.itc_amount - chain.tax_amount) > ITC_MATCH_EPSILON:
            exceptions.append(
                Gstr2bException(
                    transaction_id=chain.transaction_id,
                    kind="amount_mismatch",
                    our_itc_amount=chain.tax_amount,
                    gstr2b_itc_amount=entry.itc_amount,
                    detail=f"Our books show Rs.{chain.tax_amount / 100:,.2f} ITC; GSTR-2B shows Rs.{entry.itc_amount / 100:,.2f}.",
                )
            )
            continue

        matched_count += 1
        matched_itc_amount += chain.tax_amount

    return Gstr2bMatchReport(matched_count=matched_count, matched_itc_amount=matched_itc_amount, exceptions=exceptions)
