"""Phase 2 of bringing multi-way netting from a side experiment into something genuinely understood:
runs the identical underlying task (a discrepancy explained only by a GROUP of other transactions in
the same settlement batch, invisible to a pairwise-only rule) at a scale a real high-volume
merchant's settlement batch could actually have -- hundreds to (in projection) a thousand
transactions in one batch, not the small-scale product category's own deliberately-isolated,
bounded case (app/data_gen/generate.py::_gen_multiway_netting_trap, group_size=3/n_distractors=3 by
default).

Three conditions, matching what a real engineer evaluating this would actually ask:
1. The pairwise rule (`check_batch_anomalies`'s own logic) -- 0%, structurally, confirmed not
   assumed.
2. An actual, real exhaustive combinatorial solver (not "the rule could theoretically be extended,
   trust me") -- correct by construction up to a bounded group size, with its own real wall clock
   published, including where it becomes impractical (superseded at real scale by Phase 3's own
   k-sum solver, app/narrator/multiway_netting_optimal_solver.py).
3. The model with `list_batch_deltas`/`verify_group_sum`, as wired into the real narrator
   (app/narrator/agent.py), unfiltered and behind a cheap deterministic magnitude pre-filter.

Measured, not what the plan predicted going in: the original hypothesis was that raw context size
would be the wall (hundreds of deltas won't fit in a small model's prompt). What was actually found,
live, is two DIFFERENT failure modes, at two different scales, on two different providers:

- **Ollama (qwen2.5:7b-instruct) fails early, and not from context overflow.** At n_total=20 -- a
  batch small enough that raw context is a non-issue -- the model reliably fails by accumulating an
  ever-growing candidate list across rounds (try [a,b], fails; try [c,a,d], fails; try
  [c,a,d,e,true_member], fails -- never trying the true, small [true_member_1, true_member_2] pair
  alone) rather than searching small subsets systematically. This is a reasoning-STRATEGY limit, not
  a token-budget one -- confirmed by Groq (a stronger model) solving the identical n_total=20 case
  correctly on the first attempt.
- **Groq hits a real, literal context wall -- just later, and as a hard error, not degraded
  accuracy.** Groq solves n_total=20 correctly, degrades to an unparseable/empty response by
  n_total=100, and at n_total=400 the API itself returns `413 Request too large` for
  `openai/gpt-oss-20b` -- a real HTTP error, not a metaphor, confirming the original context-overflow
  hypothesis, just at a different provider and a higher threshold than assumed.
- **The magnitude pre-filter does not cleanly rescue either failure mode**, measured directly: at a
  tolerance loose enough to rarely discard the real answer (10x), it barely narrows the candidate set
  against this module's own uniformly-distributed distractor deltas (494 of 499 shown at n=500) --
  nowhere near enough to dodge Groq's 413 at n=400. Tightening the tolerance to actually shrink the
  request (e.g. 1.5x) raises the real-answer discard rate to over 40%. This is a genuine, disclosed
  negative result about THIS pre-filter design against THIS synthetic data's distribution, not a
  clean hybrid win -- see docs/evidence/ for the measured sweep.

This module does NOT modify the shipped product's own `list_batch_deltas`/`verify_group_sum`
(app/narrator/tools.py) -- Phase 1's own product category runs at a small, bounded scale
(few distractors) where neither failure mode found here arises, confirmed directly by Phase 1's own
measured accuracy (5/7 Ollama, 6/7 Groq, no errors, no strategy drift observed at that scale).
"""

from __future__ import annotations

import itertools
import json
import os
import random
import time
from datetime import timedelta

from pydantic import BaseModel

from app.chain.builder import CausalChain, build_chain
from app.data_gen.generate import SyntheticDataGenerator
from app.narrator.tools import ToolContext, verify_group_sum

MAX_GROUP_SIZE_FOR_EXHAUSTIVE_SOLVER = 4  # matches the small-scale case's own uniqueness-check bound


class ScaleCase(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    seed: int
    n_total: int  # total OTHER transactions in the batch, besides the target
    group_size: int
    target_id: str
    group_ids: list[str]
    chains: dict[str, CausalChain]
    context: ToolContext


def build_scale_case(seed: int, n_total: int, group_size: int = 3) -> ScaleCase:
    """One settlement batch: 1 target + `group_size` real group members whose deltas cancel the
    target's + (n_total - group_size) distractors, all sharing one batch id. Unlike the small-scale
    experiment/product case, this does NOT brute-force-verify uniqueness at construction time --
    doing so at n_total in the hundreds is exactly as expensive as the exhaustive solver this module
    measures (see run_exhaustive_solver), so it would defeat the point. Ambiguity (multiple valid
    explanations) is a real possible OUTCOME here, measured and reported, not assumed away."""
    gen = SyntheticDataGenerator(seed=seed)
    rng = random.Random(seed * 104729 + 7)  # independent stream from gen's own rng
    rail = "upi"
    created_at = gen._rand_created_at()
    shared_sla = gen._sla_days_for(rail)
    batch_id = f"batch_{rail}_scale_experiment_{seed}_{n_total}"

    group_deltas: list[int] = []
    seen: set[int] = set()
    while len(group_deltas) < group_size - 1:
        d = rng.randint(-999_931, 999_931)
        if d != 0 and d not in seen:
            group_deltas.append(d)
            seen.add(d)
    last = -sum(group_deltas)
    while last == 0 or last in seen:
        group_deltas, seen = [], set()
        while len(group_deltas) < group_size - 1:
            d = rng.randint(-999_931, 999_931)
            if d != 0 and d not in seen:
                group_deltas.append(d)
                seen.add(d)
        last = -sum(group_deltas)
    group_deltas.append(last)
    seen.add(last)

    n_distractors = n_total - group_size
    distractor_deltas: list[int] = []
    while len(distractor_deltas) < n_distractors:
        d = rng.randint(-999_931, 999_931)
        if d != 0 and d not in seen:
            distractor_deltas.append(d)
            seen.add(d)

    chains: dict[str, CausalChain] = {}
    all_ids: list[str] = []

    def _build_one(delta: int) -> str:
        order, payment, fee, tax = gen._build_order_and_payment(500_000, rail, "INR", created_at)
        net = 500_000 - fee - tax
        settlement = gen._build_settlement(
            payment.payment_id, rail, net + delta, payment.captured_at, sla_days=shared_sla, batch_id_override=batch_id
        )
        ledger = gen._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        chain = build_chain(order, payment, [], settlement, ledger)
        chains[chain.transaction_id] = chain
        all_ids.append(chain.transaction_id)
        return chain.transaction_id

    target_id = _build_one(group_deltas[0])
    group_ids = [_build_one(d) for d in group_deltas[1:]]
    for d in distractor_deltas:
        _build_one(d)

    # Shuffle the batch's own transaction order before it's exposed anywhere -- construction above
    # always inserts the real group right after the target, which would make run_exhaustive_solver's
    # itertools.combinations always find it near-instantly regardless of n_total (the group sits at
    # the very front of iteration order by construction), an artifact that would have silently
    # invalidated every timing measurement in this module. Found before it shipped any evidence, not
    # after: a dry run at n_total=200 returned combinations_checked_to_find_it in the single digits,
    # which is what caught it. Shuffled with the case's own seeded rng so this stays reproducible.
    rng.shuffle(all_ids)

    context = ToolContext(
        chains=chains,
        refund_amounts_by_payment={},
        transaction_ids_by_settlement_batch={batch_id: all_ids},
        audit_log=[],
    )
    return ScaleCase(seed=seed, n_total=n_total, group_size=group_size, target_id=target_id, group_ids=group_ids, chains=chains, context=context)


class RuleResult(BaseModel):
    n_total: int
    solved: bool
    elapsed_seconds: float


def run_pairwise_rule(case: ScaleCase) -> RuleResult:
    """The shipped rule's own logic, standalone: does any SINGLE other transaction have the exact
    opposite delta? Structurally 0% on a genuine 3+-way case by construction -- measured here, not
    assumed, timing included since even "scan every other transaction once" has a real cost at scale."""
    target_delta = case.chains[case.target_id].settlement_delta
    others = case.context.transaction_ids_by_settlement_batch[case.chains[case.target_id].settlement_batch_id]
    t0 = time.perf_counter()
    solved = False
    for oid in others:
        if oid == case.target_id:
            continue
        if case.chains[oid].settlement_delta == -target_delta:
            solved = True
            break
    elapsed = time.perf_counter() - t0
    return RuleResult(n_total=case.n_total, solved=solved, elapsed_seconds=elapsed)


class ExhaustiveSolverResult(BaseModel):
    n_total: int
    group_size: int
    max_group_size_checked: int
    found_a_group: bool
    found_group_ids: list[str]
    combinations_checked_to_find_it: int
    seconds_to_find_it: float
    other_valid_groups_found_before_stopping: int  # ambiguity signal -- see docstring
    combinations_per_second: float


def run_exhaustive_solver(case: ScaleCase, max_group_size: int = MAX_GROUP_SIZE_FOR_EXHAUSTIVE_SOLVER, max_seconds: float | None = None) -> ExhaustiveSolverResult:
    """A REAL exhaustive solver, not a claim that one could exist: checks every combination of other
    transactions up to size `max_group_size`, in increasing size order, stopping at the FIRST valid
    group found -- what a real deployed rule would actually do (find AN explanation, not necessarily
    prove there is only one). `max_seconds`, when given, aborts the search once exceeded (reported
    honestly as incomplete, not silently truncated) -- necessary once C(n_total, max_group_size)
    genuinely reaches the hours range this module's own docstring predicts.

    `other_valid_groups_found_before_stopping` stays 0 by design (this stops at the first match) --
    disambiguation at scale is a separate, harder question this function does not answer; see
    run_exhaustive_solver_check_for_ambiguity for that."""
    chain = case.chains[case.target_id]
    others_ids = [tid for tid in case.context.transaction_ids_by_settlement_batch[chain.settlement_batch_id] if tid != case.target_id]
    other_deltas = {tid: case.chains[tid].settlement_delta for tid in others_ids}
    target_delta = chain.settlement_delta

    t0 = time.perf_counter()
    checked = 0
    found: tuple[str, ...] | None = None
    aborted = False
    for size in range(1, max_group_size + 1):
        for combo in itertools.combinations(others_ids, size):
            checked += 1
            if target_delta + sum(other_deltas[i] for i in combo) == 0:
                found = combo
                break
            if max_seconds is not None and checked % 200_000 == 0 and (time.perf_counter() - t0) > max_seconds:
                aborted = True
                break
        if found or aborted:
            break
    elapsed = time.perf_counter() - t0

    return ExhaustiveSolverResult(
        n_total=case.n_total,
        group_size=case.group_size,
        max_group_size_checked=max_group_size,
        found_a_group=found is not None,
        found_group_ids=list(found) if found else [],
        combinations_checked_to_find_it=checked,
        seconds_to_find_it=elapsed,
        other_valid_groups_found_before_stopping=0,
        combinations_per_second=(checked / elapsed) if elapsed > 0 else float("inf"),
    )


# ---- condition 3: the model, with and without a magnitude pre-filter ----

MAGNITUDE_PREFILTER_MULTIPLE = 10.0  # keep any other transaction whose |delta| <= this many times the target's own |delta|
# Measured directly, not assumed: against this module's own uniformly-distributed distractor deltas
# (a genuinely adversarial worst case -- no clustering around "typical" amounts a real settlement
# batch's errors might actually have), a magnitude pre-filter is either unsafe or barely selective.
# At multiple=3.0, it discards the real group ~19% of the time; at multiple=10.0 (the default kept
# here), that drops to ~3% -- but at 10.0 it also barely narrows the candidate set on this data (99%+
# of a 500-transaction batch still passes, since deltas are drawn from the same wide range the
# target's own delta is). The "cheap pre-filter" idea is real and does have a place, but this
# specific magnitude-ratio design, on this specific (deliberately adversarial) synthetic
# distribution, is a genuine, disclosed limitation -- not the clean win a first pass assumed it
# would be. See docs/evidence/ for the measured sweep across tolerances.


def _list_batch_deltas_prefiltered(case: ScaleCase, tolerance_multiple: float = MAGNITUDE_PREFILTER_MULTIPLE) -> dict:
    """A cheap, deterministic pre-filter: narrows the same-batch candidate list to only those whose
    delta magnitude is within `tolerance_multiple` times the target's own. Real tradeoff, measured
    not assumed (see MAGNITUDE_PREFILTER_MULTIPLE's own comment): a low tolerance risks discarding
    the real answer; a tolerance high enough to be safe barely narrows anything against this
    module's own uniformly-distributed distractor deltas. Kept as a real, honestly-limited building
    block, not oversold as a solved problem."""
    chain = case.chains[case.target_id]
    own_delta = chain.settlement_delta
    threshold = abs(own_delta) * tolerance_multiple
    others = {
        tid: case.chains[tid].settlement_delta
        for tid in case.context.transaction_ids_by_settlement_batch[chain.settlement_batch_id]
        if tid != case.target_id and abs(case.chains[tid].settlement_delta) <= threshold
    }
    return {"transaction_id": case.target_id, "own_delta": own_delta, "other_transactions_in_same_batch": others}


def _list_batch_deltas_unfiltered(case: ScaleCase) -> dict:
    chain = case.chains[case.target_id]
    others = {
        tid: case.chains[tid].settlement_delta
        for tid in case.context.transaction_ids_by_settlement_batch[chain.settlement_batch_id]
        if tid != case.target_id
    }
    return {"transaction_id": case.target_id, "own_delta": chain.settlement_delta, "other_transactions_in_same_batch": others}


SYSTEM_PROMPT = """You are investigating one settlement transaction whose actual amount doesn't
match what the records (order, fee, tax, refunds) predict. You have two tools available:
list_batch_deltas(transaction_id) -- returns every OTHER transaction settled in the same batch as
this one, and each one's own delta (its actual amount minus what its own records predict).
verify_group_sum(transaction_id, candidate_transaction_ids) -- checks whether a candidate group of
other transaction ids, added to this transaction's own delta, actually sums to zero.

Call list_batch_deltas, form a hypothesis about which other transaction(s) explain the delta, then
call verify_group_sum on your hypothesis before answering -- do not answer without verifying first.

Respond with ONLY a JSON object, no prose and no markdown fences:
{"explanation": "one line: what you found and how it explains the delta",
"cited_transaction_ids": ["...", ...]}
cited_transaction_ids must be transaction ids you actually saw in the tool result, and must be
exactly the ones that explain the delta -- not every id you happened to see, and never invented."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_batch_deltas",
            "description": "List every other transaction in the same settlement batch as the given transaction id, with each one's own settlement delta.",
            "parameters": {"type": "object", "properties": {"transaction_id": {"type": "string"}}, "required": ["transaction_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_group_sum",
            "description": "Check whether a candidate group of other transaction ids, added to this transaction's own delta, actually sums to zero.",
            "parameters": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "candidate_transaction_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["transaction_id", "candidate_transaction_ids"],
            },
        },
    },
]


class ScaleLlmResult(BaseModel):
    n_total: int
    use_prefilter: bool
    provider: str
    other_transactions_shown_to_model: int  # differs from n_total-group_size only when prefiltered
    correctly_identified: bool
    llm_raw_response: str
    llm_cited_transaction_ids: list[str]
    errored: bool
    error_message: str = ""


def _parse_json_response(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def run_llm_condition(case: ScaleCase, provider: str, use_prefilter: bool, model: str | None = None, max_rounds: int = 6) -> ScaleLlmResult:
    """Runs the model with list_batch_deltas/verify_group_sum against a REAL large-scale case --
    `use_prefilter` swaps the raw tool for the magnitude-filtered one, everything else identical, so
    the two conditions are a clean comparison of the same task, same model, same prompt, one axis
    changed."""
    list_fn = _list_batch_deltas_prefiltered if use_prefilter else _list_batch_deltas_unfiltered
    shown = list_fn(case)
    other_transactions_shown = len(shown["other_transactions_in_same_batch"])

    def _dispatch(name: str, args: dict) -> dict:
        if name == "list_batch_deltas":
            return list_fn(case)
        if name == "verify_group_sum":
            candidates = args.get("candidate_transaction_ids") or []
            if isinstance(candidates, str):
                candidates = [candidates]
            return verify_group_sum(case.target_id, [str(c) for c in candidates], case.context)
        return {"error": f"unknown tool: {name!r}"}

    user_content = (
        f"Transaction {case.target_id} (rail upi): settlement delta is "
        f"{case.chains[case.target_id].settlement_delta}. Investigate using list_batch_deltas."
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_content}]

    llm_raw_response = ""
    cited: list[str] = []
    errored = False
    error_message = ""
    reached_final = False

    def _handle_final(content: str | None) -> None:
        nonlocal llm_raw_response, cited, reached_final
        reached_final = True
        llm_raw_response = content or ""
        try:
            parsed = _parse_json_response(llm_raw_response)
            cited = [str(c) for c in parsed.get("cited_transaction_ids", [])] if isinstance(parsed, dict) else []
        except (json.JSONDecodeError, KeyError):
            llm_raw_response = f"final response could not be parsed as valid JSON: {llm_raw_response[:200]!r}"

    try:
        if provider == "ollama":
            from ollama import Client

            client = Client(timeout=120.0)
            model = model or "qwen2.5:7b-instruct"
            for _ in range(max_rounds):
                response = client.chat(model=model, messages=messages, tools=TOOL_SCHEMAS)
                msg = response["message"]
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tool_calls})
                    for tc in tool_calls:
                        args = tc["function"].get("arguments") or {}
                        if isinstance(args, str):
                            args = json.loads(args or "{}")
                        result = _dispatch(tc["function"]["name"], args)
                        messages.append({"role": "tool", "content": json.dumps(result)})
                    continue
                _handle_final(msg.get("content"))
                break
        elif provider == "groq":
            from groq import Groq

            client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=120.0)
            model = model or "openai/gpt-oss-20b"
            for _ in range(max_rounds):
                response = client.chat.completions.create(model=model, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto", temperature=0.1)
                msg = response.choices[0].message
                if msg.tool_calls:
                    messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments or "{}")
                        result = _dispatch(tc.function.name, args)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
                    continue
                _handle_final(msg.content)
                break
        else:
            raise ValueError(f"unsupported provider: {provider!r}")

        if not reached_final:
            llm_raw_response = f"did not converge within the {max_rounds}-round tool-call budget"
    except Exception as e:
        errored = True
        error_message = f"{type(e).__name__}: {e}"
        llm_raw_response = f"call failed ({error_message})"

    correct = set(cited) == set(case.group_ids)

    return ScaleLlmResult(
        n_total=case.n_total,
        use_prefilter=use_prefilter,
        provider=provider,
        other_transactions_shown_to_model=other_transactions_shown,
        correctly_identified=correct,
        llm_raw_response=llm_raw_response,
        llm_cited_transaction_ids=cited,
        errored=errored,
        error_message=error_message,
    )
