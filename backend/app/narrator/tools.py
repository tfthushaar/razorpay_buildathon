"""Tools available to the agentic discrepancy narrator (spec §6.4).

Every tool here does a real lookup against actual data — the fee/SLA tools consult the same
reference tables the generator used to inject deltas in the first place (app.data_gen.fee_schedule),
and the batch/recall tools cross-reference the actual batch and the audit log built up so far in
this run. None of this is decorative or hand-waved; a tool call is a genuine check, which is the
whole point of an agentic narrator instead of a one-shot classifier.

Design note: the original sketch had a separate `check_duplicate_registry` tool with no way to
detect netting_trap at all — a transaction whose delta is explained only by a *paired* transaction
elsewhere in the batch is invisible to a tool that only looks at one payment's own refunds. Folded
duplicate-refund detection and batch-netting detection into one `check_batch_anomalies` tool since
both require the same kind of cross-transaction lookup, rather than adding a 5th tool. See
BUILD_LOG.md, 2026-08-23.
"""

from pydantic import BaseModel

from app.chain.builder import CausalChain
from app.data_gen.fee_schedule import BASE_SLA_DAYS, FEE_PCT, GST_RATE, SLA_TOLERANCE_DAYS
from app.data_gen.schemas import Rail, SyntheticBatch


class ToolContext(BaseModel):
    """Everything a tool call might need to cross-reference, built once per batch and threaded
    through every transaction's narration in that run."""

    model_config = {"arbitrary_types_allowed": True}

    chains: dict[str, CausalChain]
    refund_amounts_by_payment: dict[str, list[int]]
    transaction_ids_by_settlement_batch: dict[str, list[str]]
    audit_log: list[dict] = []  # grows as the run progresses; read by recall_similar_resolutions


def build_tool_context(batch: SyntheticBatch, chains: dict[str, CausalChain]) -> ToolContext:
    refund_amounts_by_payment: dict[str, list[int]] = {}
    for r in batch.refunds:
        refund_amounts_by_payment.setdefault(r.payment_id, []).append(r.amount)

    transaction_ids_by_settlement_batch: dict[str, list[str]] = {}
    for txn_id, chain in chains.items():
        transaction_ids_by_settlement_batch.setdefault(chain.settlement_batch_id, []).append(txn_id)

    return ToolContext(
        chains=chains,
        refund_amounts_by_payment=refund_amounts_by_payment,
        transaction_ids_by_settlement_batch=transaction_ids_by_settlement_batch,
        audit_log=[],
    )


def lookup_fee_schedule(rail: Rail) -> dict:
    return {"rail": rail, "fee_pct": FEE_PCT[rail], "gst_rate_on_fee": GST_RATE}


def check_sla_window(rail: Rail, sla_actual_days: int) -> dict:
    ceiling = SLA_TOLERANCE_DAYS[rail]
    return {
        "rail": rail,
        "sla_actual_days": sla_actual_days,
        "nominal_days": BASE_SLA_DAYS[rail],
        "tolerance_ceiling_days": ceiling,
        "within_tolerance": sla_actual_days <= ceiling,
    }


def check_batch_anomalies(transaction_id: str, context: ToolContext) -> dict:
    chain = context.chains[transaction_id]
    result: dict = {"duplicate_refund_match": None, "netting_partner": None}
    delta = chain.settlement_delta
    if delta == 0:
        return result

    for amount in context.refund_amounts_by_payment.get(chain.payment_id, []):
        if amount == abs(delta):
            result["duplicate_refund_match"] = {
                "refund_amount": amount,
                "note": "the shortfall exactly equals a refund already on record for this payment — possible double application",
            }
            break

    for other_id in context.transaction_ids_by_settlement_batch.get(chain.settlement_batch_id, []):
        if other_id == transaction_id:
            continue
        other = context.chains[other_id]
        if other.settlement_delta == -delta:
            result["netting_partner"] = {
                "transaction_id": other_id,
                "delta": other.settlement_delta,
                "note": "another transaction in the same settlement batch has the exact offsetting delta",
            }
            break

    return result


def list_batch_deltas(transaction_id: str, context: ToolContext) -> dict:
    """Every OTHER transaction in the same settlement batch as `transaction_id`, with its own
    settlement_delta -- the raw material `check_batch_anomalies` itself only searches over
    (pairwise, exact-offset-only), never exposed to a caller directly. Not part of the narrator's
    own TOOL_SCHEMAS/production tool-calling loop: `check_batch_anomalies` covers the pairwise case
    deterministically and cheaply, which is the common real case, and this project's narrator is
    deliberately kept to bounded, cheap checks. This tool exists for
    app/narrator/multiway_netting_experiment.py, which tests whether an LLM given raw access to
    the same-batch deltas can find a netting pattern spanning MORE than two transactions -- a
    pattern check_batch_anomalies structurally cannot find, since it never checks combinations."""
    chain = context.chains.get(transaction_id)
    if chain is None:
        return {"error": f"no transaction {transaction_id!r} in this batch"}
    others = {
        other_id: context.chains[other_id].settlement_delta
        for other_id in context.transaction_ids_by_settlement_batch.get(chain.settlement_batch_id, [])
        if other_id != transaction_id
    }
    return {"transaction_id": transaction_id, "own_delta": chain.settlement_delta, "other_transactions_in_same_batch": others}


def verify_group_sum(transaction_id: str, candidate_transaction_ids: list[str], context: ToolContext) -> dict:
    """Checks whether a CANDIDATE group of other transactions' deltas, added to `transaction_id`'s
    own delta, actually sum to zero -- lets a caller verify its own hypothesis arithmetically instead
    of asserting a plausible-sounding one. Built for app/narrator/multiway_netting_experiment.py
    after measuring that both providers' wrong answers shared one shape: citing a transaction with a
    large, plausible-looking delta and asserting it "explains" the target without the cited numbers
    actually summing to anything -- confident prose over arithmetic that doesn't check out. This
    tool doesn't suggest which transactions to check (that's still the caller's own hypothesis); it
    only confirms or refutes one, the same way a human analyst would re-add the numbers before
    signing off rather than trusting that a plausible story must be correct."""
    chain = context.chains.get(transaction_id)
    if chain is None:
        return {"error": f"no transaction {transaction_id!r} in this batch"}
    candidate_deltas = {}
    for cid in candidate_transaction_ids:
        other = context.chains.get(cid)
        if other is None:
            return {"error": f"no transaction {cid!r} in this batch"}
        candidate_deltas[cid] = other.settlement_delta
    total = chain.settlement_delta + sum(candidate_deltas.values())
    return {
        "transaction_id": transaction_id,
        "own_delta": chain.settlement_delta,
        "candidate_deltas": candidate_deltas,
        "sum_including_own_delta": total,
        "cancels_exactly": total == 0,
    }


def recall_similar_resolutions(category_guess: str, context: ToolContext) -> dict:
    matches = [entry for entry in context.audit_log if entry["category"] == category_guess]
    if not matches:
        return {"category": category_guess, "prior_count": 0, "note": "no prior resolutions of this category yet in this run"}
    avg_confidence = sum(m["confidence"] for m in matches) / len(matches)
    return {"category": category_guess, "prior_count": len(matches), "avg_confidence": round(avg_confidence, 3)}
