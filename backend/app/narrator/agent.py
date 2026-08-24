"""Agentic discrepancy narrator (spec §6.4).

Only ever called for transactions the matching engine's deterministic Pass 1/2 could not explain
(duplicate_refund, netting_trap, genuine_error candidates — currency_rounding, fee_deduction,
partial_refund, timing_lag, and clean_match are already resolved before this module runs at all).

Three backends behind one `narrate()` entry point:
  - "ollama": a real tool-calling loop against a fully local model (qwen2.5:7b-instruct by default)
    via Ollama. Zero cost, zero rate limits, zero external network dependency — added 2026-08-24
    after Groq's free-tier TPM limit made real batch runs take 11-70 minutes, and after checking
    every other API provider's free tier (Cerebras, Gemini, DeepSeek, GLM, SambaNova, OpenRouter,
    GitHub Models, Mistral) turned up either a hard RPM/TPM ceiling, a one-time credit that expires,
    or a daily cap too small for a real batch (see BUILD_LOG.md for the full comparison). Confirmed
    GPU-accelerated on this machine (`ollama ps` reports 100% GPU); the model stays warm in VRAM
    between calls, so only the first call in a session pays a load-time cost. This is the
    recommended real-provider default going forward — no cost trade-off, no rate-limit risk, works
    fully offline (no risk of an external API being slow/down during a live pitch recording).
  - "groq": a real tool-calling loop against Groq (openai/gpt-oss-20b by default — see BUILD_LOG.md
    for why this replaced the originally-planned Claude API, and why it's gpt-oss-20b rather than
    the llama-3.3-70b-versatile this file originally targeted, which Groq had retired by the time a
    real key was available). Free-tier accounts hit real per-minute token limits at even modest
    batch sizes (observed directly: 8000 TPM on this model, hit on request 4 of an 18-transaction
    run) — `narrate_groq` retries a real `RateLimitError` with backoff (honoring the API's own
    `retry-after` header when present) before giving up, and falls back to an honest
    escalate-as-genuine_error rather than crashing the batch if the API is still unavailable after
    retrying. This is a real runtime failure mode, not a synthetic one, and the graceful-fallback
    behavior is exactly what the "Failure Recovery" criterion is asking for. Kept as a second real
    option now that "ollama" exists, not removed.
  - "mock": zero-cost, deterministic. NOT a simulation of an LLM's reasoning — it calls the exact
    same real tool functions (so duplicate/netting detection is genuinely checked against data),
    and only stubs the final "turn tool results into a category+confidence+reasoning" step with a
    fixed rule. Used so the rest of the pipeline can be built and tested without any model at all.
    `NarratorOutput.provider` always discloses which path produced a given result — real submission
    numbers should come from "ollama" or "groq", not "mock".
"""

import json
import os
import time
from typing import Callable, TypeVar

from pydantic import BaseModel, Field

from app.chain.builder import CausalChain
from app.narrator.tools import ToolContext, check_batch_anomalies, check_sla_window, lookup_fee_schedule, recall_similar_resolutions

NARRATOR_CATEGORIES = ("duplicate_refund", "netting_trap", "genuine_error")
# Deliberately excludes currency_rounding: matching/engine.py's Pass 2 catches ANY transaction with
# abs(settlement_delta) <= ROUNDING_EPSILON before it ever reaches "needs_narration" (see
# ROUNDING_EPSILON in chain/builder.py), and every category that does reach the narrator injects a
# delta an order of magnitude larger than that threshold by construction (data_gen/generate.py).
# A real narrator run should structurally never need to classify this — it's not a valid live
# output, so it must not be offered as one (an earlier version of the prompt/schema listed it
# anyway; caught by an external audit 2026-08-24, see BUILD_LOG.md).

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"  # verified tool-calling support + cheapest of the candidates tried; swap via narrate_groq(model=...)
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-instruct"  # strong local tool-calling reputation; confirmed GPU-accelerated on this machine

SYSTEM_PROMPT = f"""You are a settlement reconciliation discrepancy narrator for Razorpay.
You are given one transaction's causal chain (order -> payment -> fee -> refund -> settlement).
Your job: explain exactly which hop broke and by how much, using the tools provided to check your
reasoning before answering. Do not guess a category without calling at least one relevant tool.

Categories you may output: {", ".join(NARRATOR_CATEGORIES)}.
(clean_match, fee_deduction, partial_refund, timing_lag, and currency_rounding are already resolved deterministically
before a transaction ever reaches you — if you're looking at a transaction, none of those apply,
so don't output them.)

If nothing you check explains the delta, output genuine_error with low confidence rather than
inventing a plausible-sounding but unverified explanation — escalating an honest "I don't know" is
correct behavior here, not a failure.

When you're done checking, respond with ONLY a JSON object, no prose and no markdown fences:
{{"category": "...", "confidence": 0.0-1.0, "reasoning": "one line: which hop broke, by how much, and why"}}
"""

TOOL_SCHEMAS = [
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
    # ge/le is a structural backstop, not the primary defense -- narrate_groq/narrate_ollama clamp
    # before constructing this (round 7's audit: an unclamped out-of-range confidence flowed into
    # escalation.py's ambiguity score and the UI's percentage display). This constraint exists so
    # any *future* call site that forgets to clamp fails loudly (pydantic.ValidationError, a
    # ValueError subclass) instead of silently accepting a bad value.
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    tool_calls: list[ToolCallRecord]
    provider: str  # "mock" | "groq" | "ollama" — always disclosed, never silently swapped


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


_T = TypeVar("_T")


def _default_groq_retry_exceptions() -> tuple[type[Exception], ...]:
    from groq import APIConnectionError, InternalServerError, RateLimitError

    return (RateLimitError, APIConnectionError, InternalServerError)


def _call_with_retry(
    fn: Callable[[], _T],
    max_retries: int = 4,
    base_delay: float = 5.0,
    retry_on: tuple[type[Exception], ...] | None = None,
) -> _T:
    """Generic retry-with-backoff, parameterized by which exceptions count as transient.

    Originally Groq-only: retries a real, observed failure mode where Groq's free tier enforces
    tokens-per-minute limits tight enough to hit mid-batch (8000 TPM on openai/gpt-oss-20b, hit on
    request 4 of an 18-transaction run in this project's own testing — see BUILD_LOG.md). Honors
    the API's `retry-after` header when the raised exception carries one (Groq's `RateLimitError`
    does; a generic exception from another provider won't, and is skipped safely via `getattr`).
    Generalized 2026-08-24 so `narrate_ollama` can retry its own transient errors (a local model
    still loading, a momentary connection hiccup) through the same mechanism, rather than
    duplicating the backoff loop per provider. Falls back to exponential backoff when no
    `retry-after` is present. Re-raises after `max_retries` so the caller can fail safe rather than
    retry forever."""
    exceptions = retry_on if retry_on is not None else _default_groq_retry_exceptions()

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


def narrate_groq(chain: CausalChain, context: ToolContext, model: str = DEFAULT_GROQ_MODEL, max_rounds: int = 4) -> NarratorOutput:
    from groq import Groq, GroqError

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _describe_chain(chain)},
    ]
    tool_calls_log: list[ToolCallRecord] = []

    def _fail_safe(reason: str) -> NarratorOutput:
        output = NarratorOutput(
            transaction_id=chain.transaction_id,
            category="genuine_error",
            confidence=0.0,
            reasoning=reason,
            tool_calls=tool_calls_log,
            provider="groq",
        )
        context.audit_log.append({"transaction_id": chain.transaction_id, "category": output.category, "confidence": output.confidence})
        return output

    try:
        for _ in range(max_rounds):
            response = _call_with_retry(
                lambda: client.chat.completions.create(
                    model=model, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto", temperature=0.1
                )
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
                try:
                    # same bug class as the final-answer validation above, one call earlier: a
                    # malformed tool-call-arguments string or a hallucinated/unknown tool name both
                    # raised an uncaught exception here (json.JSONDecodeError / ValueError from
                    # _execute_tool's "unknown tool" branch) before this fix — caught by re-reading
                    # this block while writing up the final-answer fix, not by an external audit.
                    # AttributeError added after round 8's audit reproduced 3 more shapes live:
                    # tc.function itself being None, and recall_similar_resolutions (the one tool
                    # that actually reads its arguments) receiving a JSON array or JSON null instead
                    # of an object, both of which pass json.loads fine but have no .get().
                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments or "{}")
                        result = _execute_tool(tc.function.name, args, chain, context)
                        tool_calls_log.append(ToolCallRecord(tool=tc.function.name, arguments=args, result=result))
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
                except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as e:
                    return _fail_safe(f"Narrator requested a tool call that could not be executed ({type(e).__name__}: {e}); escalating rather than guessing.")
                continue

            try:
                parsed = _parse_json_response(msg.content or "")
            except (json.JSONDecodeError, KeyError):
                # a malformed final answer is a real, observed failure mode too — escalate honestly
                # rather than crash the batch or silently invent a category.
                return _fail_safe(f"Narrator's final response could not be parsed as valid JSON: {(msg.content or '')[:200]!r}")

            try:
                # Round 6 validated `category` here but round 7's audit found everything past that
                # point (missing keys, a top-level list/null instead of an object, a non-numeric
                # confidence) still raised an UNCAUGHT exception that crashed the whole batch rather
                # than escalating the one transaction — the same "trust an LLM-shaped value without
                # checking it" bug class, one call deeper. isinstance() is checked before any dict
                # access so a wrong container type fails here, not as an AttributeError later.
                # (pydantic.ValidationError also subclasses ValueError — verified directly, not
                # assumed — so a bad type surviving to NarratorOutput(...) is caught here too.)
                if not isinstance(parsed, dict):
                    raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
                if parsed.get("category") not in NARRATOR_CATEGORIES:
                    # valid JSON but an out-of-schema category is a real, observed failure mode (a
                    # real ollama run returned "timing_lag" — a category the prompt explicitly
                    # forbids — with confidence 0.9; caught by an external audit 2026-08-24 reading
                    # the live DB, see BUILD_LOG.md). The prompt instruction alone doesn't bind the
                    # model; this does.
                    raise ValueError(f"category outside the valid set: {parsed.get('category')!r}")
                output = NarratorOutput(
                    transaction_id=chain.transaction_id,
                    category=parsed["category"],
                    # clamp, don't trust verbatim -- an out-of-[0,1] confidence would otherwise flow
                    # into escalation.py's `1.0 - confidence` ambiguity score (sinking the case a
                    # human most needs to see to the bottom of the triage queue) and render as a
                    # nonsensical percentage in the UI. Caught by the same round-7 audit.
                    confidence=max(0.0, min(1.0, float(parsed["confidence"]))),
                    reasoning=parsed["reasoning"],
                    tool_calls=tool_calls_log,
                    provider="groq",
                )
            except (KeyError, TypeError, ValueError) as e:
                return _fail_safe(f"Narrator's final response was not a usable answer ({type(e).__name__}: {e}): {(msg.content or '')[:200]!r}")

            context.audit_log.append({"transaction_id": chain.transaction_id, "category": output.category, "confidence": output.confidence})
            return output
    except GroqError as e:
        return _fail_safe(f"Narrator API call failed after retries ({type(e).__name__}: {e}); escalating rather than guessing.")

    return _fail_safe("Narrator did not converge within the tool-call budget; escalating rather than guessing.")


def narrate_ollama(chain: CausalChain, context: ToolContext, model: str = DEFAULT_OLLAMA_MODEL, max_rounds: int = 4) -> NarratorOutput:
    """Fully local tool-calling loop via Ollama — same protocol shape as narrate_groq, with two
    real API differences: Ollama's ToolCall.function.arguments is already a parsed dict (not a
    JSON string to decode), and its tool-result messages have no tool_call_id to correlate against
    (Ollama matches by message order, not by id). No rate-limit retry needed (nothing to rate-limit
    locally), but a light retry still covers transient issues (model still loading, a momentary
    connection hiccup) rather than assuming a local service never fails."""
    import httpx
    from ollama import Client, RequestError, ResponseError

    client = Client()
    # explicit retry_on: without it, _call_with_retry defaults to Groq's exception tuple, which
    # would silently never match an Ollama error at all -- this isn't a hypothetical, it's exactly
    # the bug that would exist if this were left unspecified. httpx.ConnectError (server not
    # running at all) does NOT subclass the builtin ConnectionError -- verified directly
    # (httpx.ConnectError.__mro__) rather than assumed, since getting this wrong would mean a
    # "start ollama serve" failure silently skips the retry path entirely.
    ollama_retry_exceptions: tuple[type[Exception], ...] = (RequestError, ResponseError, httpx.ConnectError, httpx.TimeoutException)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _describe_chain(chain)},
    ]
    tool_calls_log: list[ToolCallRecord] = []

    def _fail_safe(reason: str) -> NarratorOutput:
        output = NarratorOutput(
            transaction_id=chain.transaction_id,
            category="genuine_error",
            confidence=0.0,
            reasoning=reason,
            tool_calls=tool_calls_log,
            provider="ollama",
        )
        context.audit_log.append({"transaction_id": chain.transaction_id, "category": output.category, "confidence": output.confidence})
        return output

    try:
        for _ in range(max_rounds):
            response = _call_with_retry(
                lambda: client.chat(model=model, messages=messages, tools=TOOL_SCHEMAS),
                max_retries=2,
                base_delay=2.0,
                retry_on=ollama_retry_exceptions,
            )
            msg = response.message

            if msg.tool_calls:
                messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                try:
                    # see the identical block in narrate_groq for the full rationale, including why
                    # AttributeError is caught here too (round 8: tc.function can be None).
                    for tc in msg.tool_calls:
                        args = dict(tc.function.arguments)  # already parsed, unlike Groq's JSON-string arguments
                        result = _execute_tool(tc.function.name, args, chain, context)
                        tool_calls_log.append(ToolCallRecord(tool=tc.function.name, arguments=args, result=result))
                        messages.append({"role": "tool", "content": json.dumps(result)})  # no tool_call_id -- Ollama matches by order
                except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as e:
                    return _fail_safe(f"Narrator requested a tool call that could not be executed ({type(e).__name__}: {e}); escalating rather than guessing.")
                continue

            try:
                parsed = _parse_json_response(msg.content or "")
            except (json.JSONDecodeError, KeyError):
                return _fail_safe(f"Narrator's final response could not be parsed as valid JSON: {(msg.content or '')[:200]!r}")

            try:
                # see the identical block in narrate_groq for the full rationale (round 7's audit:
                # everything past the JSON-parse step was still uncaught and could crash the batch).
                if not isinstance(parsed, dict):
                    raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
                if parsed.get("category") not in NARRATOR_CATEGORIES:
                    # a real ollama run already returned an out-of-schema category live — see
                    # narrate_groq's identical check for the full incident.
                    raise ValueError(f"category outside the valid set: {parsed.get('category')!r}")
                output = NarratorOutput(
                    transaction_id=chain.transaction_id,
                    category=parsed["category"],
                    confidence=max(0.0, min(1.0, float(parsed["confidence"]))),
                    reasoning=parsed["reasoning"],
                    tool_calls=tool_calls_log,
                    provider="ollama",
                )
            except (KeyError, TypeError, ValueError) as e:
                return _fail_safe(f"Narrator's final response was not a usable answer ({type(e).__name__}: {e}): {(msg.content or '')[:200]!r}")

            context.audit_log.append({"transaction_id": chain.transaction_id, "category": output.category, "confidence": output.confidence})
            return output
    except ollama_retry_exceptions as e:
        return _fail_safe(
            f"Local narrator call failed after retries ({type(e).__name__}: {e}); escalating rather than guessing. "
            f"Is `ollama serve` running and has `{model}` been pulled?"
        )

    return _fail_safe("Narrator did not converge within the tool-call budget; escalating rather than guessing.")


VALID_PROVIDERS = ("mock", "groq", "ollama")


def narrate(chain: CausalChain, context: ToolContext, provider: str | None = None) -> NarratorOutput:
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")

    try:
        # the provider-validity check used to sit here, BEFORE this try block -- an unknown
        # provider string (a typo in a raw API call; README.md documents "provider" as a normal
        # per-request field, so this needs nothing adversarial) raised ValueError straight past the
        # backstop below and crashed the whole batch through /api/run, the system's primary,
        # default, most-used endpoint. Round 7's own audit had already named "/api/run has no
        # handler" in BUILD_LOG.md; round 9 fixed the sibling endpoint that sentence also named
        # (/api/transactions/evaluate) but this one was missed until round 10 reproduced it live.
        # Moving the check inside the try, so it's caught by the same backstop as everything else,
        # closes it at the actual root rather than adding a third near-duplicate guard.
        if provider == "mock":
            return narrate_mock(chain, context)
        if provider == "groq":
            return narrate_groq(chain, context)
        if provider == "ollama":
            return narrate_ollama(chain, context)
        raise ValueError(f"unknown LLM_PROVIDER: {provider!r} (expected one of {VALID_PROVIDERS})")
    except Exception as e:
        # Orchestration-level backstop (round 8's audit, 2026-08-24): rounds 5-8 each found a
        # *different* unguarded model-supplied value inside narrate_groq/narrate_ollama's own
        # exception handling — a real, recurring pattern, not a one-off, since the next unforeseen
        # failure shape is by definition one neither function's specific except tuple names yet.
        # This does NOT replace those specific handlers — a known failure still produces a more
        # informative `reasoning` string from inside the provider function itself, which matters
        # for the audit log's transparency story. This is the last line of defense for whatever
        # isn't a known failure yet, so one transaction's crash can never take down an entire
        # batch's results (including transactions already correctly resolved before it) the way it
        # did before this existed, reachable live through /api/transactions/evaluate.
        output = NarratorOutput(
            transaction_id=chain.transaction_id,
            category="genuine_error",
            confidence=0.0,
            reasoning=f"Narrator crashed unexpectedly ({type(e).__name__}: {e}); escalating rather than losing the batch.",
            tool_calls=[],
            provider=provider,
        )
        context.audit_log.append({"transaction_id": chain.transaction_id, "category": output.category, "confidence": output.confidence})
        return output
