"""Settlement Q&A agent: free-text natural-language questions over the reconciled ledger and
audit log, answered by walking the causal chain and citing the specific hop, with the tool trace
as evidence -- not a lookup, a real answer the model has to go find.

A genuinely SEPARATE agentic loop from the narrator (app/narrator/agent.py), not a reuse of it:
narrate() is scoped to one CausalChain per call with a rigid category/confidence/reasoning output
contract validated against NARRATOR_CATEGORIES -- there is no way to ask it a free-text question
about the whole batch. This loop reuses the exact same provider dispatch pattern (mock/groq/ollama),
the same circuit breaker and retry-with-backoff machinery, and the same fail-safe discipline
(every model-supplied value validated before use, every real API failure caught and reported, never
silently swallowed), under a different system prompt and a free-text answer contract.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, TypeVar

from pydantic import BaseModel

from app.chain.builder import CausalChain
from app.narrator.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from app.narrator.tools import ToolContext, check_batch_anomalies
from app.qa.tools import find_transactions_by_date, get_transaction_detail, list_flagged_transactions

_T = TypeVar("_T")

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-instruct"

_groq_breaker = CircuitBreaker()
_ollama_breaker = CircuitBreaker()

SYSTEM_PROMPT = """You are a settlement Q&A agent for a Razorpay merchant's reconciliation dashboard.
You answer a finance team's plain-English question about a batch of already-reconciled transactions,
using the tools provided to find the real answer -- never guess or make up a transaction id, an
amount, or a reason.

Available tools: find_transactions_by_date (search the batch by settlement date),
get_transaction_detail (full causal-chain detail for one transaction id), check_batch_anomalies
(cross-references the refund registry and same-batch transactions for a specific id),
list_flagged_transactions (scans the WHOLE batch for duplicate-refund/netting-trap anomalies --
use this one first for any question that isn't scoped to a specific id or date, e.g. "are there
any duplicate refunds in this batch").

Call at least one tool before answering anything that references specific transactions, dates, or
amounts. If nothing you check actually explains the answer, say so plainly rather than inventing a
plausible-sounding one.

When you're done, respond with ONLY a JSON object, no prose and no markdown fences:
{"answer": "...", "cited_transaction_ids": ["...", ...]}
cited_transaction_ids must be transaction ids you actually saw in a tool result, in this exact run --
never a transaction id you haven't looked up."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "find_transactions_by_date",
            "description": "Search this batch for transactions whose real settlement date matches the given date (YYYY-MM-DD).",
            "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transaction_detail",
            "description": "Get the full causal-chain detail (fee, tax, refunds, settlement delta, hop-by-hop trace) for one specific transaction id.",
            "parameters": {"type": "object", "properties": {"transaction_id": {"type": "string"}}, "required": ["transaction_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_batch_anomalies",
            "description": "Check whether a transaction's shortfall matches a duplicate refund or an exact offsetting delta elsewhere in the same settlement batch.",
            "parameters": {"type": "object", "properties": {"transaction_id": {"type": "string"}}, "required": ["transaction_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_flagged_transactions",
            "description": "Scan the entire batch for transactions with a duplicate-refund match or a netting-trap partner. Use this for any question not scoped to one transaction id or one date.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict
    result: dict


class QAAnswer(BaseModel):
    question: str
    answer: str
    cited_transaction_ids: list[str]
    tool_calls: list[ToolCallRecord]
    provider: str


def _execute_tool(name: str, arguments: dict, context: ToolContext, settled_at_by_transaction_id: dict) -> dict:
    if name == "find_transactions_by_date":
        return find_transactions_by_date(str(arguments.get("date", "")), context.chains, settled_at_by_transaction_id)
    if name == "get_transaction_detail":
        return get_transaction_detail(str(arguments.get("transaction_id", "")), context.chains)
    if name == "check_batch_anomalies":
        # check_batch_anomalies (app/narrator/tools.py) does an unchecked context.chains[transaction_id]
        # -- safe for the narrator, which only ever calls it with chain.transaction_id (its own,
        # guaranteed-valid chain), never with a model-supplied id. The Q&A agent's whole point is
        # letting the model name ANY transaction id, including a hallucinated one -- found live
        # against a real Ollama call, which did exactly that and crashed with a raw KeyError before
        # this check existed. Validated here rather than changing narrator's own function, which is
        # correctly designed for its actual caller.
        transaction_id = str(arguments.get("transaction_id", ""))
        if transaction_id not in context.chains:
            return {"error": f"no transaction {transaction_id!r} in this batch"}
        return check_batch_anomalies(transaction_id, context)
    if name == "list_flagged_transactions":
        return list_flagged_transactions(context)
    raise ValueError(f"unknown tool: {name}")


def _default_retry_exceptions() -> tuple[type[Exception], ...]:
    from groq import APIConnectionError, InternalServerError, RateLimitError

    return (RateLimitError, APIConnectionError, InternalServerError)


def _call_with_retry(fn: Callable[[], _T], max_retries: int = 4, base_delay: float = 5.0, retry_on: tuple[type[Exception], ...] | None = None) -> _T:
    exceptions = retry_on if retry_on is not None else _default_retry_exceptions()
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except exceptions as e:
            if attempt == max_retries:
                raise
            delay = base_delay * (2**attempt)
            response = getattr(e, "response", None)
            header_value = getattr(response, "headers", {}).get("retry-after") if response is not None else None
            if header_value is not None:
                try:
                    delay = float(header_value)
                except (ValueError, TypeError):
                    pass
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def _parse_json_response(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def answer_mock(question: str, context: ToolContext, settled_at_by_transaction_id: dict) -> QAAnswer:
    """Zero-cost, deterministic stand-in: calls the real tools (so a mock answer is grounded in the
    same real data a live model would see), but the final synthesis is a fixed rule rather than an
    LLM call -- same posture as narrate_mock, and for the same reason (cheap pipeline testing)."""
    tool_calls_log: list[ToolCallRecord] = []
    txn_ids = list(context.chains.keys())[:3]
    for txn_id in txn_ids:
        result = get_transaction_detail(txn_id, context.chains)
        tool_calls_log.append(ToolCallRecord(tool="get_transaction_detail", arguments={"transaction_id": txn_id}, result=result))
    return QAAnswer(
        question=question,
        answer=f"Mock provider: checked {len(txn_ids)} transaction(s) for detail; a real provider would synthesize an actual answer from these tool results.",
        cited_transaction_ids=txn_ids,
        tool_calls=tool_calls_log,
        provider="mock",
    )


def answer_groq(
    question: str,
    context: ToolContext,
    settled_at_by_transaction_id: dict,
    model: str = DEFAULT_GROQ_MODEL,
    max_rounds: int = 4,
    breaker: CircuitBreaker | None = None,
) -> QAAnswer:
    from groq import Groq, GroqError

    breaker = breaker if breaker is not None else _groq_breaker
    tool_calls_log: list[ToolCallRecord] = []

    def _fail_safe(reason: str) -> QAAnswer:
        return QAAnswer(question=question, answer=reason, cited_transaction_ids=[], tool_calls=tool_calls_log, provider="groq")

    try:
        breaker.before_call()
    except CircuitBreakerOpenError as e:
        return _fail_safe(str(e))

    try:
        client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=60.0)
    except KeyError:
        return _fail_safe("Groq is selected but GROQ_API_KEY isn't configured in this environment.")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]

    try:
        for _ in range(max_rounds):
            response = _call_with_retry(
                lambda: client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto", temperature=0.1)
            )
            msg = response.choices[0].message

            if msg.tool_calls:
                messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                try:
                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments or "{}")
                        result = _execute_tool(tc.function.name, args, context, settled_at_by_transaction_id)
                        tool_calls_log.append(ToolCallRecord(tool=tc.function.name, arguments=args, result=result))
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
                except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as e:
                    return _fail_safe(f"Could not execute a requested tool call ({type(e).__name__}: {e}).")
                continue

            try:
                parsed = _parse_json_response(msg.content or "")
            except (json.JSONDecodeError, KeyError):
                return _fail_safe(f"Final response could not be parsed as valid JSON: {(msg.content or '')[:200]!r}")

            try:
                if not isinstance(parsed, dict):
                    raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
                cited = parsed.get("cited_transaction_ids", [])
                if not isinstance(cited, list):
                    raise TypeError("cited_transaction_ids must be a list")
                answer = QAAnswer(
                    question=question,
                    answer=str(parsed["answer"]),
                    cited_transaction_ids=[str(c) for c in cited],
                    tool_calls=tool_calls_log,
                    provider="groq",
                )
            except (KeyError, TypeError, ValueError) as e:
                return _fail_safe(f"Final response was not a usable answer ({type(e).__name__}: {e}): {(msg.content or '')[:200]!r}")

            breaker.record_success()
            return answer
    except GroqError as e:
        breaker.record_failure()
        return _fail_safe(f"API call failed after retries ({type(e).__name__}: {e}).")

    return _fail_safe("Did not converge on an answer within the tool-call budget.")


def answer_ollama(
    question: str,
    context: ToolContext,
    settled_at_by_transaction_id: dict,
    model: str = DEFAULT_OLLAMA_MODEL,
    max_rounds: int = 4,
    breaker: CircuitBreaker | None = None,
) -> QAAnswer:
    from ollama import Client, RequestError, ResponseError

    breaker = breaker if breaker is not None else _ollama_breaker
    tool_calls_log: list[ToolCallRecord] = []

    def _fail_safe(reason: str) -> QAAnswer:
        return QAAnswer(question=question, answer=reason, cited_transaction_ids=[], tool_calls=tool_calls_log, provider="ollama")

    try:
        breaker.before_call()
    except CircuitBreakerOpenError as e:
        return _fail_safe(str(e))

    # ollama.Client() delegates to httpx.Client(**kwargs) with no kwargs supplied here, and the
    # ollama package silently defaults to NO timeout at all unless one is passed explicitly --
    # same real bug narrate_ollama's own timeout= fixes, applied here for the same reason.
    client = Client(timeout=60.0)
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]

    ollama_retry_exceptions: tuple[type[Exception], ...] = (RequestError, ResponseError)
    try:
        for _ in range(max_rounds):
            response = _call_with_retry(
                lambda: client.chat(model=model, messages=messages, tools=TOOL_SCHEMAS),
                retry_on=ollama_retry_exceptions,
            )
            msg = response["message"]

            tool_calls = msg.get("tool_calls")
            if tool_calls:
                messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tool_calls})
                try:
                    for tc in tool_calls:
                        fn = tc["function"]
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            args = json.loads(args or "{}")
                        result = _execute_tool(fn["name"], args, context, settled_at_by_transaction_id)
                        tool_calls_log.append(ToolCallRecord(tool=fn["name"], arguments=args, result=result))
                        messages.append({"role": "tool", "content": json.dumps(result)})
                except (json.JSONDecodeError, TypeError, ValueError, KeyError, AttributeError) as e:
                    return _fail_safe(f"Could not execute a requested tool call ({type(e).__name__}: {e}).")
                continue

            try:
                parsed = _parse_json_response(msg.get("content") or "")
            except (json.JSONDecodeError, KeyError):
                return _fail_safe(f"Final response could not be parsed as valid JSON: {(msg.get('content') or '')[:200]!r}")

            try:
                if not isinstance(parsed, dict):
                    raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
                cited = parsed.get("cited_transaction_ids", [])
                if not isinstance(cited, list):
                    raise TypeError("cited_transaction_ids must be a list")
                answer = QAAnswer(
                    question=question,
                    answer=str(parsed["answer"]),
                    cited_transaction_ids=[str(c) for c in cited],
                    tool_calls=tool_calls_log,
                    provider="ollama",
                )
            except (KeyError, TypeError, ValueError) as e:
                return _fail_safe(f"Final response was not a usable answer ({type(e).__name__}: {e}): {(msg.get('content') or '')[:200]!r}")

            breaker.record_success()
            return answer
    except ollama_retry_exceptions as e:
        breaker.record_failure()
        return _fail_safe(f"API call failed after retries ({type(e).__name__}: {e}).")

    return _fail_safe("Did not converge on an answer within the tool-call budget.")


def answer_question(question: str, context: ToolContext, settled_at_by_transaction_id: dict, provider: str | None = None) -> QAAnswer:
    """Dispatch by provider, with the same orchestration-level backstop the narrator's narrate()
    uses -- any exception a provider function doesn't already handle itself fails safe here rather
    than crashing the request."""
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    try:
        if provider == "mock":
            return answer_mock(question, context, settled_at_by_transaction_id)
        if provider == "groq":
            return answer_groq(question, context, settled_at_by_transaction_id)
        if provider == "ollama":
            return answer_ollama(question, context, settled_at_by_transaction_id)
        raise ValueError(f"unknown LLM_PROVIDER: {provider!r} (expected one of mock, groq, ollama)")
    except Exception as e:
        return QAAnswer(
            question=question,
            answer=f"Q&A agent crashed unexpectedly ({type(e).__name__}: {e}); no answer available.",
            cited_transaction_ids=[],
            tool_calls=[],
            provider=provider,
        )
