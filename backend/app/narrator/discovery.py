"""Category discovery: when the narrator would otherwise fail safe to `genuine_error` (today's dead
end -- an honest "I don't know" with nowhere further to go), one additional model call asks the same
question a human analyst would ask next: "what pattern might this actually be?" It answers grounded
in the exact tool-call evidence the narrator already gathered for that transaction (see
NarratorOutput.tool_calls) -- not a fresh, unstructured guess, and not a second round of tool calls.

This is NEVER auto-adopted into the real taxonomy (NARRATOR_CATEGORIES in app/narrator/agent.py) --
a proposal is tracked and surfaced for a human to review, the same "propose, don't silently act"
discipline as the rest of this project's calibration story (a category only ever earns auto-resolve
through CalibrationHistory's own accumulated evidence, never through this path). If discovery ever
graduated a proposed category into the real taxonomy, that would be a deliberate, separate, human
decision -- adding a string to NARRATOR_CATEGORIES and a case to the matching engine -- not something
this module does on its own.

Same provider dispatch (mock/groq/ollama) as the narrator and the Q&A agent, but a single completion
call, not a tool-calling loop: the evidence to reason over already exists (the narrator's own
tool_calls for this transaction), so there's nothing left to look up."""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field

from app.chain.builder import CausalChain
from app.narrator.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from app.narrator.tools import ToolContext

_groq_breaker = CircuitBreaker()
_ollama_breaker = CircuitBreaker()

SYSTEM_PROMPT = """You are helping a settlement reconciliation system that just gave up on a
transaction, classifying it as "genuine_error" -- an honest "no known pattern explains this," not a
wrong answer. Your job is different from the classifier's: propose a NAMED, SPECIFIC hypothesis for
what this pattern might actually be, grounded only in the evidence already gathered below -- never
invent a fact not present in it.

This is a proposal for a human to review, not a decision that gets acted on automatically. If the
evidence genuinely doesn't support any specific hypothesis beyond "unexplained," say so by setting
proposed_name to null rather than inventing a plausible-sounding label.

Respond with ONLY a JSON object, no prose and no markdown fences:
{"proposed_name": "snake_case_name_or_null", "hypothesis": "one line: what pattern this might be and why",
"supporting_evidence": ["...", "..."], "confidence": 0.0-1.0}
supporting_evidence must be short strings that each reference a specific fact already present in the
evidence below (a tool result, a hop delta) -- never a fact not shown to you."""


class CategoryProposal(BaseModel):
    transaction_id: str
    proposed_name: str | None
    hypothesis: str
    supporting_evidence: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    provider: str


def _describe_evidence(chain: CausalChain, tool_calls: list[dict]) -> str:
    calls = "\n".join(f"  {tc['tool']}({tc['arguments']}) -> {tc['result']}" for tc in tool_calls)
    return f"""Transaction: {chain.transaction_id}
Rail: {chain.rail}  Settlement delta: {chain.settlement_delta}  Ledger gap: {chain.ledger_gap}
Hop-by-hop trace:
{chr(10).join(f"  {h.name}: expected={h.expected}, actual={h.actual}, delta={h.delta}" for h in chain.hops)}

Tool calls the narrator already made on this transaction, and their real results:
{calls if calls else "  (none)"}

This was classified genuine_error -- propose a hypothesis using only the evidence above."""


def _parse_json_response(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def propose_category_mock(chain: CausalChain, tool_calls: list[dict], context: ToolContext) -> CategoryProposal:
    """Zero-cost, deterministic stand-in -- reasons over the exact same real evidence a live model
    would see (so it isn't a no-op), but the synthesis is a fixed rule, same posture as narrate_mock
    and answer_mock."""
    sla_result = next((tc["result"] for tc in tool_calls if tc["tool"] == "check_sla_window"), None)
    if sla_result is not None and not sla_result.get("within_tolerance", True):
        return CategoryProposal(
            transaction_id=chain.transaction_id,
            proposed_name="unexplained_settlement_delay",
            hypothesis="Settlement landed outside the normal SLA tolerance for this rail, and neither a duplicate refund nor a netting partner explains the amount delta.",
            supporting_evidence=[f"check_sla_window reported within_tolerance=False for rail={chain.rail}"],
            confidence=0.4,
            provider="mock",
        )
    return CategoryProposal(
        transaction_id=chain.transaction_id,
        proposed_name=None,
        hypothesis="No specific pattern is supported by the evidence gathered so far.",
        supporting_evidence=[],
        confidence=0.0,
        provider="mock",
    )


def propose_category_groq(chain: CausalChain, tool_calls: list[dict], context: ToolContext, model: str = "openai/gpt-oss-20b", breaker: CircuitBreaker | None = None) -> CategoryProposal:
    from groq import Groq, GroqError

    breaker = breaker if breaker is not None else _groq_breaker

    def _fail_safe(reason: str) -> CategoryProposal:
        return CategoryProposal(transaction_id=chain.transaction_id, proposed_name=None, hypothesis=reason, supporting_evidence=[], confidence=0.0, provider="groq")

    try:
        breaker.before_call()
    except CircuitBreakerOpenError as e:
        return _fail_safe(str(e))

    try:
        client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=60.0)
    except KeyError:
        return _fail_safe("Groq is selected but GROQ_API_KEY isn't configured in this environment.")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _describe_evidence(chain, tool_calls)}],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
    except GroqError as e:
        breaker.record_failure()
        return _fail_safe(f"API call failed ({type(e).__name__}: {e}).")

    try:
        parsed = _parse_json_response(content)
        if not isinstance(parsed, dict):
            raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
        evidence = parsed.get("supporting_evidence", [])
        if not isinstance(evidence, list):
            raise TypeError("supporting_evidence must be a list")
        proposal = CategoryProposal(
            transaction_id=chain.transaction_id,
            proposed_name=parsed.get("proposed_name") or None,
            hypothesis=str(parsed["hypothesis"]),
            supporting_evidence=[str(e) for e in evidence],
            confidence=max(0.0, min(1.0, float(parsed["confidence"]))),
            provider="groq",
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return _fail_safe(f"Response could not be parsed as a usable proposal ({type(e).__name__}: {e}): {content[:200]!r}")

    breaker.record_success()
    return proposal


def propose_category_ollama(chain: CausalChain, tool_calls: list[dict], context: ToolContext, model: str = "qwen2.5:7b-instruct", breaker: CircuitBreaker | None = None) -> CategoryProposal:
    from ollama import Client, RequestError, ResponseError

    breaker = breaker if breaker is not None else _ollama_breaker

    def _fail_safe(reason: str) -> CategoryProposal:
        return CategoryProposal(transaction_id=chain.transaction_id, proposed_name=None, hypothesis=reason, supporting_evidence=[], confidence=0.0, provider="ollama")

    try:
        breaker.before_call()
    except CircuitBreakerOpenError as e:
        return _fail_safe(str(e))

    client = Client(timeout=60.0)
    try:
        response = client.chat(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _describe_evidence(chain, tool_calls)}],
        )
        content = response["message"].get("content") or ""
    except (RequestError, ResponseError) as e:
        breaker.record_failure()
        return _fail_safe(f"Local call failed ({type(e).__name__}: {e}). Is `ollama serve` running and has `{model}` been pulled?")

    try:
        parsed = _parse_json_response(content)
        if not isinstance(parsed, dict):
            raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
        evidence = parsed.get("supporting_evidence", [])
        if not isinstance(evidence, list):
            raise TypeError("supporting_evidence must be a list")
        proposal = CategoryProposal(
            transaction_id=chain.transaction_id,
            proposed_name=parsed.get("proposed_name") or None,
            hypothesis=str(parsed["hypothesis"]),
            supporting_evidence=[str(e) for e in evidence],
            confidence=max(0.0, min(1.0, float(parsed["confidence"]))),
            provider="ollama",
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return _fail_safe(f"Response could not be parsed as a usable proposal ({type(e).__name__}: {e}): {content[:200]!r}")

    breaker.record_success()
    return proposal


def propose_category(chain: CausalChain, tool_calls: list[dict], context: ToolContext, provider: str | None = None) -> CategoryProposal:
    """Dispatch by provider, with the same orchestration-level backstop the narrator's narrate()
    and the Q&A agent's answer_question() use -- any exception a provider function doesn't already
    handle itself fails safe here rather than crashing the request."""
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    try:
        if provider == "mock":
            return propose_category_mock(chain, tool_calls, context)
        if provider == "groq":
            return propose_category_groq(chain, tool_calls, context)
        if provider == "ollama":
            return propose_category_ollama(chain, tool_calls, context)
        raise ValueError(f"unknown LLM_PROVIDER: {provider!r} (expected one of mock, groq, ollama)")
    except Exception as e:
        return CategoryProposal(
            transaction_id=chain.transaction_id,
            proposed_name=None,
            hypothesis=f"Category discovery crashed unexpectedly ({type(e).__name__}: {e}); no proposal available.",
            supporting_evidence=[],
            confidence=0.0,
            provider=provider,
        )
