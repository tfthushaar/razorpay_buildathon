"""One deliberately hard test: give the LLM a task the shipped deterministic rule cannot do, and
measure it for real, rather than another case the rule already solves.

Why this exists: `check_batch_anomalies` (app/narrator/tools.py) detects a netting_trap only when
exactly ONE other transaction in the same settlement batch has the exact opposite delta -- a
pairwise, exact-match check. This is cheap and covers the common case, and this project's own
generator (`_gen_netting_trap`, app/data_gen/generate.py) only ever injects pairs, which is exactly
why `narrate_mock` -- a 20-line if/elif over that tool's output -- scores 100% on netting_trap:
the injector and the detector share the same author and the same exact-match logic, so there is no
real ambiguity for the "classifier" to resolve. Measured directly (seed sweep across 7 seeds, main +
stress batches, 519 real narration-queue cases): mock is 100.0% on all three LLM-routed categories.
The real Ollama/Groq narrator is NOT — 98.3% on netting_trap, 80.3% on genuine_error. On the
categories this project actually ships, the deterministic rule is a strict upgrade over the LLM, not
the other way around. See BUILD_LOG.md for the full disclosure.

This module tests one case the rule structurally cannot solve: THREE transactions in the same
settlement batch whose deltas cancel as a GROUP but where no PAIR among them cancels -- invisible to
`check_batch_anomalies`, which only ever checks one-to-one. Deliberately NOT "fixed" by writing a
combinatorial (subset-sum) version of the rule -- that's a real, available option (subset-sum over a
handful of same-batch transactions is cheap), and disclosed as such: the point of this experiment is
not "no rule could ever do this," it's "the rule this project actually shipped doesn't, and here is
whether the LLM's own reasoning, given the same raw data, closes that gap for free." `list_batch_deltas`
(app/narrator/tools.py) hands the model exactly what `check_batch_anomalies` already has internally
-- every other transaction's delta in the same batch -- without doing the summing itself."""

from __future__ import annotations

import json
import os
from datetime import timedelta

from pydantic import BaseModel

from app.chain.builder import CausalChain, build_chain
from app.data_gen.generate import SyntheticDataGenerator
from app.narrator.tools import ToolContext, check_batch_anomalies, list_batch_deltas

SYSTEM_PROMPT = """You are investigating one settlement transaction whose actual amount doesn't
match what the records (order, fee, tax, refunds) predict. You have one tool available:
list_batch_deltas(transaction_id) -- returns every OTHER transaction settled in the same batch as
this one, and each one's own delta (its actual amount minus what its own records predict).

A transaction's delta can be explained by a GROUP of other transactions in the same batch whose
deltas, added together with this one, sum to (approximately) zero -- even if no single other
transaction does it alone. Call the tool, then look for such a group.

Respond with ONLY a JSON object, no prose and no markdown fences:
{"explanation": "one line: which other transaction(s), if any, explain this delta and how",
"cited_transaction_ids": ["...", ...]}
cited_transaction_ids must be transaction ids you actually saw in the tool result -- never invented,
and empty if no group in the tool result explains it."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_batch_deltas",
            "description": "List every other transaction in the same settlement batch as the given transaction id, with each one's own settlement delta.",
            "parameters": {"type": "object", "properties": {"transaction_id": {"type": "string"}}, "required": ["transaction_id"]},
        },
    }
]


class ExperimentResult(BaseModel):
    provider: str
    target_transaction_id: str
    group_transaction_ids: list[str]  # the real, hand-constructed group that actually explains the delta
    rule_found_duplicate_refund: bool
    rule_found_netting_partner: bool
    rule_verdict: str  # what narrate_mock would output: "genuine_error" if neither fired
    llm_raw_response: str
    llm_cited_transaction_ids: list[str]
    llm_correctly_identified_the_group: bool  # cited ids are a superset of group_transaction_ids


def build_experiment_case(seed: int = 777) -> tuple[dict[str, CausalChain], ToolContext, str, list[str]]:
    """Hand-constructs 3 transactions in the same settlement batch with deltas +20000, +15000,
    -35000 (paise) -- sums to zero as a group, but no pair does (20000+15000=35000, not 0;
    20000-35000=-15000, not 0; 15000-35000=-20000, not 0). No refunds on any of them, so
    duplicate_refund_match can never fire either. Returns the target transaction (the -35000 one)
    and the other two transaction ids that together actually explain it."""
    gen = SyntheticDataGenerator(seed=seed)
    rail = "upi"
    created_at = gen._rand_created_at()
    shared_sla = gen._sla_days_for(rail)
    batch_id = f"batch_{rail}_multiway_experiment_{seed}"

    deltas = [20000, 15000, -35000]
    chains: dict[str, CausalChain] = {}
    txn_ids: list[str] = []
    for delta in deltas:
        order, payment, fee, tax = gen._build_order_and_payment(500_000, rail, "INR", created_at)
        net = 500_000 - fee - tax
        settlement = gen._build_settlement(
            payment.payment_id, rail, net + delta, payment.captured_at, sla_days=shared_sla, batch_id_override=batch_id
        )
        ledger = gen._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        chain = build_chain(order, payment, [], settlement, ledger)
        chains[chain.transaction_id] = chain
        txn_ids.append(chain.transaction_id)

    context = ToolContext(
        chains=chains,
        refund_amounts_by_payment={},
        transaction_ids_by_settlement_batch={batch_id: txn_ids},
        audit_log=[],
    )
    target_id = txn_ids[2]  # the -35000 one
    group_ids = [txn_ids[0], txn_ids[1]]
    return chains, context, target_id, group_ids


def _parse_json_response(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def run_experiment(seed: int = 777, provider: str = "ollama", model: str | None = None) -> ExperimentResult:
    chains, context, target_id, group_ids = build_experiment_case(seed)
    target = chains[target_id]

    rule_result = check_batch_anomalies(target_id, context)
    rule_found_dup = rule_result["duplicate_refund_match"] is not None
    rule_found_net = rule_result["netting_partner"] is not None
    rule_verdict = "duplicate_refund" if rule_found_dup else "netting_trap" if rule_found_net else "genuine_error"

    user_content = (
        f"Transaction {target_id} (rail {target.rail}): settlement delta is {target.settlement_delta}. "
        f"No duplicate-refund match and no single offsetting transaction were found by the standard check. "
        f"Investigate using list_batch_deltas."
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_content}]

    llm_raw_response = ""
    cited: list[str] = []

    try:
        if provider == "ollama":
            from ollama import Client

            client = Client(timeout=60.0)
            model = model or "qwen2.5:7b-instruct"
            for _ in range(3):
                response = client.chat(model=model, messages=messages, tools=TOOL_SCHEMAS)
                msg = response["message"]
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tool_calls})
                    for tc in tool_calls:
                        args = tc["function"].get("arguments") or {}
                        if isinstance(args, str):
                            args = json.loads(args or "{}")
                        result = list_batch_deltas(str(args.get("transaction_id", target_id)), context)
                        messages.append({"role": "tool", "content": json.dumps(result)})
                    continue
                llm_raw_response = msg.get("content") or ""
                break
        elif provider == "groq":
            from groq import Groq

            client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=60.0)
            model = model or "openai/gpt-oss-20b"
            for _ in range(3):
                response = client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto", temperature=0.1)
                msg = response.choices[0].message
                if msg.tool_calls:
                    messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments or "{}")
                        result = list_batch_deltas(str(args.get("transaction_id", target_id)), context)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
                    continue
                llm_raw_response = msg.content or ""
                break
        else:
            raise ValueError(f"unsupported provider for this experiment: {provider!r}")

        parsed = _parse_json_response(llm_raw_response)
        cited = [str(c) for c in parsed.get("cited_transaction_ids", [])] if isinstance(parsed, dict) else []
    except Exception as e:
        llm_raw_response = f"experiment call failed ({type(e).__name__}: {e})"

    correctly_identified = set(group_ids).issubset(set(cited))

    return ExperimentResult(
        provider=provider,
        target_transaction_id=target_id,
        group_transaction_ids=group_ids,
        rule_found_duplicate_refund=rule_found_dup,
        rule_found_netting_partner=rule_found_net,
        rule_verdict=rule_verdict,
        llm_raw_response=llm_raw_response,
        llm_cited_transaction_ids=cited,
        llm_correctly_identified_the_group=correctly_identified,
    )
