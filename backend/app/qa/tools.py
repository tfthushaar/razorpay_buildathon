"""Tools for the settlement Q&A agent (app/qa/agent.py) -- batch-scoped lookups, unlike the
narrator's tools (app/narrator/tools.py), which are always scoped to one specific transaction's
own chain. A free-text question like "why was Tuesday's payout short?" needs to search across
every transaction first, then drill into the ones that match -- the narrator never needs to do
that, since it's always handed the one transaction it's explaining.

Reuses `check_batch_anomalies` from app/narrator/tools.py directly, unmodified -- it already takes
a transaction_id + the shared ToolContext, and its cross-referencing logic (duplicate-refund /
netting-trap detection) is exactly as useful for answering a question about a transaction as it is
for narrating one.
"""

from __future__ import annotations

from datetime import datetime

from app.chain.builder import CausalChain
from app.data_gen.schemas import SyntheticBatch
from app.narrator.tools import ToolContext, check_batch_anomalies


def build_settled_at_index(batch: SyntheticBatch) -> dict[str, datetime]:
    """Maps transaction_id (== order_id) -> its real settlement timestamp, for transactions that
    have one. Built once per Q&A session from the raw batch (chains alone don't carry a real
    calendar timestamp, only day-counts like sla_actual_days) and passed alongside the chains
    dict to the tools below rather than folded into narrator's own ToolContext, which has no
    reason to carry this for its own (single-transaction, timestamp-agnostic) use case."""
    payment_id_to_order_id = {p.payment_id: p.order_id for p in batch.payments}
    index: dict[str, datetime] = {}
    for settlement in batch.settlements:
        order_id = payment_id_to_order_id.get(settlement.payment_id)
        if order_id is not None:
            index[order_id] = settlement.settled_at
    return index


def find_transactions_by_date(date_str: str, chains: dict[str, CausalChain], settled_at_by_transaction_id: dict[str, datetime]) -> dict:
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"error": f"could not parse date {date_str!r}; expected YYYY-MM-DD"}

    matches = []
    for transaction_id, settled_at in settled_at_by_transaction_id.items():
        if settled_at.date() != target:
            continue
        chain = chains.get(transaction_id)
        if chain is None:
            continue
        matches.append(
            {
                "transaction_id": transaction_id,
                "rail": chain.rail,
                "settlement_delta": chain.settlement_delta,
                "ledger_gap": chain.ledger_gap,
                "first_divergence_hop": chain.first_divergence_hop,
            }
        )
    return {"date": date_str, "count": len(matches), "matches": matches}


def list_flagged_transactions(context: ToolContext) -> dict:
    """Batch-wide scan for duplicate-refund / netting-trap anomalies -- the one thing neither
    check_batch_anomalies (scoped to a single transaction_id) nor find_transactions_by_date (scoped
    to one calendar date) can answer. A question like "are there any duplicate refunds in this
    batch" has no single id or date to hand a tool, so without this the model has nothing to call
    and has to say it can't answer -- found live via Playwright driving the real Q&A panel with
    exactly that question. Cheap to run in full: check_batch_anomalies is O(1) dict lookups plus a
    same-settlement-batch scan, and a batch here tops out in the low hundreds of transactions."""
    flagged = []
    for transaction_id, chain in context.chains.items():
        result = check_batch_anomalies(transaction_id, context)
        if result["duplicate_refund_match"] or result["netting_partner"]:
            flagged.append({"transaction_id": transaction_id, **result})
    return {"count": len(flagged), "flagged": flagged}


def get_transaction_detail(transaction_id: str, chains: dict[str, CausalChain]) -> dict:
    chain = chains.get(transaction_id)
    if chain is None:
        return {"error": f"no transaction {transaction_id!r} in this batch"}
    return {
        "transaction_id": chain.transaction_id,
        "rail": chain.rail,
        "fee_amount": chain.fee_amount,
        "tax_amount": chain.tax_amount,
        "refund_ids": chain.refund_ids,
        "refund_total": chain.refund_total,
        "computed_expected_settlement": chain.computed_expected_settlement,
        "actual_settled_amount": chain.actual_settled_amount,
        "settlement_delta": chain.settlement_delta,
        "ledger_gap": chain.ledger_gap,
        "within_sla": chain.within_sla,
        "first_divergence_hop": chain.first_divergence_hop,
        "hops": [{"name": h.name, "expected": h.expected, "actual": h.actual, "delta": h.delta} for h in chain.hops],
    }
