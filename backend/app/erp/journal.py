"""ERP journal line generator (Pillar 3): turns a resolved transaction's causal chain into
double-entry journal lines ready for import into an ERP.

Balances by construction from the chain's own already-computed values, not assumed: Revenue is
always credited at the gross captured amount (computed_expected_settlement + fee_amount +
tax_amount + refund_total, which is exactly captured_amount by chain/builder.py's own arithmetic);
Payment Gateway Charges, Input Tax Credit Receivable, and Refunds are debited at their recorded
amounts; Bank Account is debited at the actual settled amount; and a Reconciliation Suspense line
absorbs whatever's left over (== -settlement_delta by construction -- verified algebraically for
both delta >= 0 and delta < 0 in test_journal.py, not just spot-checked). For a fully-explained
transaction that suspense line is exactly zero and is omitted entirely -- it only ever appears,
non-zero, for a transaction whose gap the pipeline hasn't (yet) explained, which is the honest,
standard accounting treatment for an unresolved exception: post what's known, hold the rest in
suspense pending resolution, never silently force a balance that isn't real.

GST/ITC: the tax_amount line always posts to a distinct "Input Tax Credit Receivable" ledger,
never merged into the fee expense line -- this is what makes the fee and its GST separately
visible for GSTR-2B/ITC reclaim, the actual point of this pillar.

TDS (Section 393 of the Income-tax Act 2025, formerly Section 194O, effective 1 April 2026):
applies to e-commerce OPERATORS deducting on gross sales of PARTICIPANTS on their own platform --
NOT to a direct merchant being paid out by a payment gateway for its own sales, which is this
project's actual scenario. Included only as an optional, clearly-labeled informational note for a
merchant who is itself an e-commerce operator; never applied by default or posted as a journal
line automatically.
"""

from pydantic import BaseModel, computed_field

from app.chain.builder import CausalChain

TDS_RATE = 0.001  # Section 393(1), Income-tax Act 2025 (was Section 194O) -- 0.1% on gross, in force since 1 Oct 2024 under the old numbering


class JournalLine(BaseModel):
    account: str
    debit: int  # smallest currency unit; 0 if this is a credit line
    credit: int  # smallest currency unit; 0 if this is a debit line
    description: str
    transaction_id: str


class JournalEntry(BaseModel):
    transaction_id: str
    lines: list[JournalLine] = []
    finalized: bool = True  # False if pending human review -- not yet ready to post
    note: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_balanced(self) -> bool:
        return sum(l.debit for l in self.lines) == sum(l.credit for l in self.lines)


def generate_journal_entry(chain: CausalChain, finalized: bool = True) -> JournalEntry:
    txn = chain.transaction_id
    lines: list[JournalLine] = []

    gross_revenue = chain.computed_expected_settlement + chain.fee_amount + chain.tax_amount + chain.refund_total
    lines.append(JournalLine(account="Revenue", debit=0, credit=gross_revenue, description=f"Gross transaction value for {txn}", transaction_id=txn))
    lines.append(
        JournalLine(
            account="Bank Account", debit=chain.actual_settled_amount, credit=0, description=f"Net settlement received for {txn} ({chain.rail})", transaction_id=txn
        )
    )
    if chain.fee_amount:
        lines.append(JournalLine(account="Payment Gateway Charges", debit=chain.fee_amount, credit=0, description=f"Gateway fee, {chain.rail}", transaction_id=txn))
    if chain.tax_amount:
        lines.append(
            JournalLine(
                account="Input Tax Credit Receivable", debit=chain.tax_amount, credit=0, description=f"GST on gateway fee (ITC-eligible), {chain.rail}", transaction_id=txn
            )
        )
    if chain.refund_total:
        lines.append(
            JournalLine(
                account="Refunds / Sales Returns",
                debit=chain.refund_total,
                credit=0,
                description=f"Refund(s) on record: {', '.join(chain.refund_ids)}",
                transaction_id=txn,
            )
        )

    # By construction (see module docstring, verified in test_journal.py): suspense == -settlement_delta
    # for any chain, so the entry always balances, with a zero/omitted suspense line for anything
    # the pipeline has fully explained and a real, visible one for anything it hasn't.
    suspense = -chain.settlement_delta
    if suspense != 0:
        lines.append(
            JournalLine(
                account="Reconciliation Suspense",
                debit=max(suspense, 0),
                credit=max(-suspense, 0),
                description=f"Unexplained variance pending resolution (first diverges at: {chain.first_divergence_hop})",
                transaction_id=txn,
            )
        )

    note = None if finalized else "Pending human review before this journal entry is finalized — see the escalation queue."
    return JournalEntry(transaction_id=txn, lines=lines, finalized=finalized, note=note)


def generate_journal_entries(chains: dict[str, CausalChain], finalized_ids: set[str]) -> list[JournalEntry]:
    """`finalized_ids`: transaction ids the calibration/escalation pipeline has actually resolved
    (auto-resolved or human-confirmed) -- everything else posts with finalized=False, matching the
    real accounting practice of not finalizing a journal entry for a transaction still under
    review."""
    return [generate_journal_entry(chain, finalized=txn_id in finalized_ids) for txn_id, chain in chains.items()]


def tds_note(chain: CausalChain, is_ecommerce_operator: bool = False) -> str | None:
    """Informational only, never posted as a journal line automatically -- Section 393 TDS applies
    to e-commerce OPERATORS on participant sales, not to a direct merchant's own gateway
    settlement. Only meaningful if the merchant using this system is itself running a marketplace
    and this transaction is a participant sale, which this project's own data model doesn't
    represent -- `is_ecommerce_operator` defaults to False specifically so this never silently
    applies to the common case."""
    if not is_ecommerce_operator:
        return None
    tds = round(chain.computed_expected_settlement * TDS_RATE)
    return (
        f"If {chain.transaction_id} is a marketplace participant sale: TDS u/s 393(1) "
        f"@ 0.1% = Rs.{tds / 100:,.2f} (track for Form 26AS reconciliation, not withheld by this system)"
    )
