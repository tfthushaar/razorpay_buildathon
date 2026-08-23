"""Agentic discrepancy narrator (spec §6.4).

Only ever called for transactions the matching engine's deterministic Pass 1/2 could not explain
(duplicate_refund, netting_trap, genuine_error candidates — currency_rounding, fee_deduction,
partial_refund, timing_lag, and clean_match are already resolved before this module runs at all).

Two backends behind one `narrate()` entry point:
  - "groq": a real tool-calling loop against Groq's Llama 3.3 (OpenAI-compatible API, free tier —
    see BUILD_LOG.md for why this replaced the originally-planned Claude API).
  - "mock": zero-cost, deterministic. NOT a simulation of an LLM's reasoning — it calls the exact
    same real tool functions (so duplicate/netting detection is genuinely checked against data),
    and only stubs the final "turn tool results into a category+confidence+reasoning" step with a
    fixed rule. Used so the rest of the pipeline can be built and tested without an API key.
    `NarratorOutput.provider` always discloses which path produced a given result — real submission
    numbers should come from "groq", not "mock".
"""

import json
import os

from pydantic import BaseModel

from app.chain.builder import CausalChain
from app.narrator.tools import ToolContext, check_batch_anomalies, check_sla_window, lookup_fee_schedule, recall_similar_resolutions

NARRATOR_CATEGORIES = ("duplicate_refund", "netting_trap", "currency_rounding", "genuine_error")

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"  # cheap/free-tier; swap via narrate_groq(model=...)

SYSTEM_PROMPT = f"""You are a settlement reconciliation discrepancy narrator for Razorpay.
You are given one transaction's causal chain (order -> payment -> fee -> refund -> settlement).
Your job: explain exactly which hop broke and by how much, using the tools provided to check your
reasoning before answering. Do not guess a category without calling at least one relevant tool.

Categories you may output: {", ".join(NARRATOR_CATEGORIES)}.
(clean_match, fee_deduction, partial_refund, and timing_lag are already resolved deterministically
before a transaction ever reaches you — if you're looking at a transaction, none of those apply,
so don't output them.)

If nothing you check explains the delta, output genuine_error with low confidence rather than
inventing a plausible-sounding but unverified explanation — escalating an honest "I don't know" is
correct behavior here, not a failure.

When you're done checking, respond with ONLY a JSON object, no prose and no markdown fences:
{{"category": "...", "confidence": 0.0-1.0, "reasoning": "one line: which hop broke, by how much, and why"}}
"""

GROQ_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_fee_schedule",
            "description": "Look up the platform fee percentage and GST rate for a payment rail.",
            "parameters": {
                "type": "object",
                "properties": {"rail": {"type": "string", "enum": ["upi", "card", "netbanking"]}},
                "required": ["rail"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_sla_window",
            "description": "Check whether this settlement's observed delay is within normal SLA variance for its rail, or genuinely outside it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rail": {"type": "string", "enum": ["upi", "card", "netbanking"]},
                    "sla_actual_days": {"type": "integer"},
                },
                "required": ["rail", "sla_actual_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_batch_anomalies",
            "description": (
                "Check whether this transaction's shortfall exactly matches a refund already on "
                "record for the same payment (possible duplicate application), or whether another "
                "transaction in the same settlement batch has the exact offsetting delta (possible "
                "netting trap where two transactions look reconciled only in aggregate)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"transaction_id": {"type": "string"}},
                "required": ["transaction_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_similar_resolutions",
            "description": "Look up how confidently similar-category exceptions have already been resolved so far in this run.",
            "parameters": {
                "type": "object",
                "properties": {"category_guess": {"type": "string"}},
                "required": ["category_guess"],
            },
        },
    },
]


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict
    result: dict


class NarratorOutput(BaseModel):
    transaction_id: str
    category: str
    confidence: float
    reasoning: str
    tool_calls: list[ToolCallRecord]
    provider: str  # "mock" | "groq" — always disclosed, never silently swapped


def _execute_tool(name: str, arguments: dict, chain: CausalChain, context: ToolContext) -> dict:
    # transaction identity/rail/timing always come from the chain itself, never from
    # model-supplied arguments — a hallucinated id can't redirect a lookup to the wrong record.
    if name == "lookup_fee_schedule":
        return lookup_fee_schedule(chain.rail)
    if name == "check_sla_window":
        return check_sla_window(chain.rail, chain.sla_actual_days)
    if name == "check_batch_anomalies":
        return check_batch_anomalies(chain.transaction_id, context)
    if name == "recall_similar_resolutions":
        return recall_similar_resolutions(str(arguments.get("category_guess", "genuine_error")), context)
    raise ValueError(f"unknown tool: {name}")


def _describe_chain(chain: CausalChain) -> str:
    hops = "\n".join(f"  {h.name}: expected={h.expected}, actual={h.actual}, delta={h.delta}" for h in chain.hops)
    return f"""Transaction: {chain.transaction_id}
Rail: {chain.rail}  Currency: {chain.currency}
Fee: {chain.fee_amount}  Tax: {chain.tax_amount}
Refunds on record: {chain.refund_ids} totaling {chain.refund_total}
Computed expected settlement (from records): {chain.computed_expected_settlement}
Actual settled amount: {chain.actual_settled_amount}
Settlement delta (actual - computed): {chain.settlement_delta}
Ledger's expected amount: {chain.ledger_expected_amount}  Ledger gap: {chain.ledger_gap}
Settlement timing: {chain.sla_actual_days}d observed (nominal {chain.sla_nominal_days}d for {chain.rail})

Hop-by-hop trace:
{hops}

This was NOT resolved by the deterministic fee/refund/timing/rounding checks — something here
isn't explained by the records alone. Investigate with the tools, then classify it."""


def _parse_json_response(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def narrate_mock(chain: CausalChain, context: ToolContext) -> NarratorOutput:
    tool_calls: list[ToolCallRecord] = []

    anomaly_args = {"transaction_id": chain.transaction_id}
    anomaly_result = check_batch_anomalies(chain.transaction_id, context)
    tool_calls.append(ToolCallRecord(tool="check_batch_anomalies", arguments=anomaly_args, result=anomaly_result))

    if anomaly_result.get("duplicate_refund_match"):
        category, confidence = "duplicate_refund", 0.9
        reasoning = (
            f"Settlement shortfall of {abs(chain.settlement_delta)} exactly matches a refund already "
            f"on record for this payment — looks double-applied, not a second legitimate refund."
        )
    elif anomaly_result.get("netting_partner"):
        partner = anomaly_result["netting_partner"]["transaction_id"]
        category, confidence = "netting_trap", 0.85
        reasoning = (
            f"This transaction is off by {chain.settlement_delta}; transaction {partner} in the same "
            f"settlement batch is off by the exact opposite amount. Nets clean at the batch level, "
            f"but each transaction is individually wrong."
        )
    else:
        sla_check = check_sla_window(chain.rail, chain.sla_actual_days)
        tool_calls.append(
            ToolCallRecord(
                tool="check_sla_window",
                arguments={"rail": chain.rail, "sla_actual_days": chain.sla_actual_days},
                result=sla_check,
            )
        )
        category, confidence = "genuine_error", 0.3
        reasoning = (
            f"Settlement differs from the computed post-fee/refund amount by {chain.settlement_delta}. "
            f"Neither the duplicate-refund registry nor the batch-netting check explains it, and timing "
            f"is {'within' if sla_check['within_tolerance'] else 'outside'} normal variance either way. "
            f"No known pattern fits — flagging for review rather than guessing."
        )

    recall_args = {"category_guess": category}
    recall_result = recall_similar_resolutions(category, context)
    tool_calls.append(ToolCallRecord(tool="recall_similar_resolutions", arguments=recall_args, result=recall_result))

    output = NarratorOutput(
        transaction_id=chain.transaction_id,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        tool_calls=tool_calls,
        provider="mock",
    )
    context.audit_log.append({"transaction_id": chain.transaction_id, "category": category, "confidence": confidence})
    return output


def narrate_groq(chain: CausalChain, context: ToolContext, model: str = DEFAULT_GROQ_MODEL, max_rounds: int = 4) -> NarratorOutput:
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _describe_chain(chain)},
    ]
    tool_calls_log: list[ToolCallRecord] = []

    for _ in range(max_rounds):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=GROQ_TOOL_SCHEMAS, tool_choice="auto", temperature=0.1
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                }
            )
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = _execute_tool(tc.function.name, args, chain, context)
                tool_calls_log.append(ToolCallRecord(tool=tc.function.name, arguments=args, result=result))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
            continue

        parsed = _parse_json_response(msg.content or "")
        output = NarratorOutput(
            transaction_id=chain.transaction_id,
            category=parsed["category"],
            confidence=float(parsed["confidence"]),
            reasoning=parsed["reasoning"],
            tool_calls=tool_calls_log,
            provider="groq",
        )
        context.audit_log.append({"transaction_id": chain.transaction_id, "category": output.category, "confidence": output.confidence})
        return output

    output = NarratorOutput(
        transaction_id=chain.transaction_id,
        category="genuine_error",
        confidence=0.0,
        reasoning="Narrator did not converge within the tool-call budget; escalating rather than guessing.",
        tool_calls=tool_calls_log,
        provider="groq",
    )
    context.audit_log.append({"transaction_id": chain.transaction_id, "category": output.category, "confidence": output.confidence})
    return output


def narrate(chain: CausalChain, context: ToolContext, provider: str | None = None) -> NarratorOutput:
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    if provider == "mock":
        return narrate_mock(chain, context)
    if provider == "groq":
        return narrate_groq(chain, context)
    raise ValueError(f"unknown LLM_PROVIDER: {provider!r} (expected 'mock' or 'groq')")
