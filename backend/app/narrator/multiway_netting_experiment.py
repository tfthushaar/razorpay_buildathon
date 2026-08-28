"""One deliberately hard test: give the LLM a task the shipped deterministic rule cannot do, and
measure it for real, rather than another case the rule already solves.

Why this exists: `check_batch_anomalies` (app/narrator/tools.py) detects a netting_trap only when
exactly ONE other transaction in the same settlement batch has the exact opposite delta -- a
pairwise, exact-match check. This is cheap and covers the common case, and this project's own
generator (`_gen_netting_trap`, app/data_gen/generate.py) only ever injects pairs, which is exactly
why `narrate_mock` -- a 20-line if/elif over that tool's output -- scores 100% on netting_trap: the
injector and the detector share the same author and the same exact-match logic, so there is no real
ambiguity for the "classifier" to resolve. See scripts/measure_mock_narrator_accuracy.py and
BUILD_LOG.md for the full, committed measurement of that finding.

This module tests one case the rule structurally cannot solve: a small GROUP of transactions in the
same settlement batch whose deltas cancel together, where no PAIR among them cancels -- invisible to
`check_batch_anomalies`, which only ever checks one-to-one. Deliberately NOT "fixed" by writing a
combinatorial (subset-sum) version of the rule -- that's a real, available option, disclosed as such:
the point is not "no rule could ever do this," it's "the rule this project actually shipped doesn't,
and here is whether the LLM's own reasoning, given the same raw data, closes that gap."

Design notes, tightened after a first version had three real methodological holes (found by re-reading
the evidence, not assumed away -- see BUILD_LOG.md):

1. **Varied arithmetic, not the same puzzle relabeled.** The first version hardcoded the same three
   deltas (+20000, +15000, -35000) for every seed -- only ids and timestamps varied, so 8 "trials"
   were 8 correlated samples of one fixed sum, not independent evidence. `MIN_DISTINCT_TRANSACTIONS_
   FOR_AUTO_RESOLVE` (app/calibration/calibrator.py) exists specifically to reject this exact pattern
   elsewhere in this project; this module now derives a genuinely different target delta and group
   split per seed.
2. **Real distractors.** The first version put exactly 3 transactions in the batch, so "the other 2"
   was the only non-trivial candidate group -- not a search, a forced move. This version adds
   `N_DISTRACTORS` unrelated transactions to the same settlement batch, verified (by brute-force
   subset-sum over every non-empty subset up to size 4) to contain no OTHER subset that also cancels
   the target's delta, so there is exactly one correct answer among many wrong ones.
3. **The prompt no longer names the strategy.** The first version's system prompt said outright
   "look for a GROUP... that sum to zero" -- handing over the solution method, not just the tool.
   This version describes only what the tool returns, not what pattern to look for.
4. **Exact-match grading, not subset-containment.** The first version scored `set(group_ids).issubset
   (set(cited))` -- with only 2 real "other" transactions in the batch, citing everything seen
   trivially passed. With real distractors now present, a model that cites everyone it saw fails;
   only the exact right subset counts.
"""

from __future__ import annotations

import itertools
import json
import os
import random
from datetime import timedelta

from pydantic import BaseModel

from app.chain.builder import CausalChain, build_chain
from app.data_gen.generate import SyntheticDataGenerator
from app.narrator.tools import ToolContext, check_batch_anomalies, list_batch_deltas

N_DISTRACTORS = 8  # unrelated transactions sharing the same settlement batch
MAX_SUBSET_SIZE_CHECKED_FOR_UNIQUENESS = 4  # brute-force cap; N_DISTRACTORS+2 choose this is cheap

SYSTEM_PROMPT = """You are investigating one settlement transaction whose actual amount doesn't
match what the records (order, fee, tax, refunds) predict. You have one tool available:
list_batch_deltas(transaction_id) -- returns every OTHER transaction settled in the same batch as
this one, and each one's own delta (its actual amount minus what its own records predict).

Call the tool, then use the results to explain the transaction's own delta.

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
    }
]


class ExperimentResult(BaseModel):
    provider: str
    target_transaction_id: str
    group_transaction_ids: list[str]  # the real, hand-constructed group that actually explains the delta
    n_other_transactions_in_batch: int  # group + distractors -- the real search space size
    rule_found_duplicate_refund: bool
    rule_found_netting_partner: bool
    rule_verdict: str  # what narrate_mock would output: "genuine_error" if neither fired
    llm_raw_response: str
    llm_cited_transaction_ids: list[str]
    llm_correctly_identified_the_group: bool  # cited ids are EXACTLY the group -- not superset, not subset


def _find_other_subsets_that_cancel(target_delta: int, other_deltas: dict[str, int], correct_group: set[str]) -> list[set[str]]:
    """Brute-force every non-empty subset of `other_deltas` up to size
    MAX_SUBSET_SIZE_CHECKED_FOR_UNIQUENESS that also cancels target_delta -- used only to verify the
    hand-constructed case has exactly one right answer, not left to chance."""
    ids = list(other_deltas.keys())
    matches = []
    for size in range(1, MAX_SUBSET_SIZE_CHECKED_FOR_UNIQUENESS + 1):
        for combo in itertools.combinations(ids, size):
            if set(combo) == correct_group:
                continue
            if target_delta + sum(other_deltas[i] for i in combo) == 0:
                matches.append(set(combo))
    return matches


def build_experiment_case(seed: int = 777) -> tuple[dict[str, CausalChain], ToolContext, str, list[str]]:
    """Hand-constructs a settlement batch of 1 target + 2 group members + N_DISTRACTORS unrelated
    transactions. The group's two deltas cancel the target's exactly; no distractor, and no OTHER
    subset up to size 4, does (verified, not assumed -- raises if construction is ever wrong).
    Deltas are derived from `seed` so each seed is a genuinely different arithmetic puzzle, not the
    same one relabeled."""
    gen = SyntheticDataGenerator(seed=seed)
    rng = random.Random(seed * 7919 + 1)  # independent stream from gen's own rng, seed-derived
    rail = "upi"
    created_at = gen._rand_created_at()
    shared_sla = gen._sla_days_for(rail)
    batch_id = f"batch_{rail}_multiway_experiment_{seed}"

    target_delta = -rng.choice([25000, 30000, 35000, 40000, 45000])
    split_a = rng.randint(1, abs(target_delta) - 1)
    split_a = round(split_a / 500) * 500 or 500  # round to a plausible paise increment, never 0
    split_b = -target_delta - split_a
    if split_b == 0 or split_a == split_b:
        split_b = -target_delta - split_a - 500  # nudge apart; still sums correctly with split_a adjusted below
        split_a += 500

    group_deltas = [split_a, split_b]

    # Distractors: drawn from a wide, fine-grained (paise-level, not round-hundred) range so an
    # accidental subset-sum collision with the group's total is vanishingly unlikely -- a first
    # version drew from only 8 fixed round values and hit real accidental collisions (verified via
    # the uniqueness check below, which exists for exactly this reason: don't trust the arithmetic,
    # check it).
    distractor_deltas = []
    seen = {group_deltas[0], group_deltas[1]}
    while len(distractor_deltas) < N_DISTRACTORS:
        d = rng.randint(-999_931, 999_931)  # deliberately not a round number or multiple of 100
        if d != 0 and d not in seen:
            distractor_deltas.append(d)
            seen.add(d)

    all_deltas = [target_delta, *group_deltas, *distractor_deltas]
    chains: dict[str, CausalChain] = {}
    txn_ids: list[str] = []
    for delta in all_deltas:
        order, payment, fee, tax = gen._build_order_and_payment(500_000, rail, "INR", created_at)
        net = 500_000 - fee - tax
        settlement = gen._build_settlement(
            payment.payment_id, rail, net + delta, payment.captured_at, sla_days=shared_sla, batch_id_override=batch_id
        )
        ledger = gen._build_ledger(order.order_id, net, created_at + timedelta(minutes=5))
        chain = build_chain(order, payment, [], settlement, ledger)
        chains[chain.transaction_id] = chain
        txn_ids.append(chain.transaction_id)

    target_id = txn_ids[0]
    group_ids = [txn_ids[1], txn_ids[2]]

    # Verify uniqueness for real -- not assumed. A failure here means this seed's construction is
    # ambiguous (more than one valid explanation exists) and must not be used as an experiment case.
    other_ids = txn_ids[1:]
    other_deltas_by_id = {tid: chains[tid].settlement_delta for tid in other_ids}
    stray_matches = _find_other_subsets_that_cancel(chains[target_id].settlement_delta, other_deltas_by_id, set(group_ids))
    if stray_matches:
        raise AssertionError(f"seed {seed}: construction is ambiguous, other subsets also cancel the target: {stray_matches}")

    context = ToolContext(
        chains=chains,
        refund_amounts_by_payment={},
        transaction_ids_by_settlement_batch={batch_id: txn_ids},
        audit_log=[],
    )
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
    n_other = len(context.transaction_ids_by_settlement_batch[target.settlement_batch_id]) - 1

    rule_result = check_batch_anomalies(target_id, context)
    rule_found_dup = rule_result["duplicate_refund_match"] is not None
    rule_found_net = rule_result["netting_partner"] is not None
    rule_verdict = "duplicate_refund" if rule_found_dup else "netting_trap" if rule_found_net else "genuine_error"

    user_content = (
        f"Transaction {target_id} (rail {target.rail}): settlement delta is {target.settlement_delta}. "
        f"Investigate using list_batch_deltas."
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_content}]

    llm_raw_response = ""
    cited: list[str] = []
    converged = False  # distinguishes "never gave a final answer within budget" from "gave one"
    max_rounds = 4  # matches app/narrator/agent.py's own default -- not extended further to chase a result

    try:
        if provider == "ollama":
            from ollama import Client

            client = Client(timeout=60.0)
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
                        result = list_batch_deltas(str(args.get("transaction_id", target_id)), context)
                        messages.append({"role": "tool", "content": json.dumps(result)})
                    continue
                llm_raw_response = msg.get("content") or ""
                converged = True
                break
        elif provider == "groq":
            from groq import Groq

            client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=60.0)
            model = model or "openai/gpt-oss-20b"
            for _ in range(max_rounds):
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
                converged = True
                break
        else:
            raise ValueError(f"unsupported provider for this experiment: {provider!r}")

        if not converged:
            # Exhausted the tool-call budget without ever producing a final answer -- a real,
            # distinct failure mode from "gave a malformed final answer", and the production
            # narrator (app/narrator/agent.py) reports it as its own distinct fail-safe message
            # rather than attempting to parse empty content as JSON. Same posture here.
            llm_raw_response = f"did not converge within the {max_rounds}-round tool-call budget"
        else:
            parsed = _parse_json_response(llm_raw_response)
            cited = [str(c) for c in parsed.get("cited_transaction_ids", [])] if isinstance(parsed, dict) else []
    except Exception as e:
        llm_raw_response = f"experiment call failed ({type(e).__name__}: {e})"

    correctly_identified = set(cited) == set(group_ids)

    return ExperimentResult(
        provider=provider,
        target_transaction_id=target_id,
        group_transaction_ids=group_ids,
        n_other_transactions_in_batch=n_other,
        rule_found_duplicate_refund=rule_found_dup,
        rule_found_netting_partner=rule_found_net,
        rule_verdict=rule_verdict,
        llm_raw_response=llm_raw_response,
        llm_cited_transaction_ids=cited,
        llm_correctly_identified_the_group=correctly_identified,
    )
