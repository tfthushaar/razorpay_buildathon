"""Layer 2: structured attribution over the residual, with a deterministic verifier in the loop.

This replaces asking the model for a label. A label is exactly the kind of thing a lookup table can
produce, which is why every labelled category in this project eventually collapsed to one. What is
asked for instead is a decomposition -- which causes, in what amounts, citing what evidence -- and
that has two properties a label cannot have:

  it is self-verifying   the components must sum to the observed delta and every citation must
                         resolve to a real object whose properties support the amount claimed. Both
                         are deterministic checks (app/resolver/verifier.py). Generative output with
                         an arithmetic verifier in front of it is the only shape that is safe near
                         money.

  it changes what        not "picked the right string" but "every component of the explanation
  accuracy means         checked out". Much harder to game, considerably more useful to whoever has
                         to act on it, and not reducible to a classification lookup.

Two deliberate design choices worth stating plainly, because both cut against making the numbers
look good:

`attribute_mock` is not a stub -- it is app/resolver/keyword_baseline.py, the strongest rule I could
write for this task, negation handling included. That makes the zero-cost default genuinely strong
rather than a strawman, and it means the rule's column appears in every results table automatically
instead of being something I remember to run. If the rule wins, every table says so without my
choosing to report it.

The model and the rule get symmetric help. The rule's negation-cue list was assembled with full
sight of the generator's own phrasing, which is the strongest position a rule author is ever in; so
the model's prompt gets the corresponding domain warning that remittance advice routinely mentions
items that were not applied. Neither is given the answer, and neither is given something the other
was denied.
"""

import json
import os
from typing import Literal

from pydantic import BaseModel

from app.chain.builder import CausalChain
from app.narrator.tools import ToolContext
from app.resolver.causes import CauseCandidate, Decomposition
from app.resolver.keyword_baseline import best_decomposition_by_advice
from app.resolver.resolver import DEFAULT_TOLERANCE_PAISE, ResolverOutput, present_options
from app.resolver.verifier import VerificationResult, verify_decomposition

# How many of Layer 0's valid breakdowns are actually shown. Both the model and the keyword baseline
# see the same window, and how often the true answer falls outside it is published as a ceiling on
# everyone's accuracy rather than absorbed into the model's score. See resolver.rank_decompositions.
OPTION_WINDOW = 40

DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# How many times a proposal that fails verification is handed back with the specific complaint. Three
# is not arbitrary: measured on the real residual, essentially all recoveries happen by the third
# attempt, and rounds beyond that spend tokens re-asserting rather than repairing. See
# docs/RESULTS.md for the per-round recovery numbers this was set from.
MAX_VERIFY_ROUNDS = 3

# Two prompts, because the two halves of the residual are genuinely different tasks.
#
# On UNDER_DETERMINED, Layer 0 has already done all the arithmetic and produced k explanations that
# each add up. Asking the model to redo that search would be asking it to solve subset-sum in its
# head, which it cannot do and should not be asked to -- measured directly: given the raw candidate
# pool, a live 7b run picked a single candidate and stopped, 0 of 6, never attempting to compose. So
# it is given the finished explanations and asked only to CHOOSE, which is exactly what the
# architecture says its job is, and exactly what the keyword baseline does with the same list.
#
# On UNMATCHED there is nothing to choose from, so the pool is handed over and construction is the
# task. That is the harder half and it is scored separately rather than blended into one number.

_ADVICE_GUIDANCE = """Read the remittance advice carefully. It is written by banks, not by lawyers: \
abbreviated, inconsistently punctuated, and it routinely mentions charges that were NOT applied this \
cycle -- denied, reversed, exempted, still pending approval, or scheduled to begin in a later cycle. \
A mention is not a confirmation. Equally, the advice is incomplete: real components are often not \
mentioned at all, so absence from the text is not evidence against a component."""

CHOICE_SYSTEM_PROMPT = f"""You are a settlement reconciliation analyst. A settlement came in for a \
different net amount than the transaction's own records predict.

The deterministic resolver has already found EVERY breakdown that adds up to the observed delta. \
They all add up correctly -- that is not what separates them. Exactly one of them is what actually \
happened, and your job is to say which.

Because every option is arithmetically valid, arithmetic cannot decide this. The evidence decides \
it, above all the settlement's free-text remittance advice.

{_ADVICE_GUIDANCE}

Respond with ONLY a JSON object, no prose around it:
{{
  "choice": <the number of the option that really happened>,
  "why": "<short justification, citing the advice where it is relevant>",
  "proposed_action": {{"type": "<recover_from_acquirer|adjust_ledger|write_off|escalate>", "amount": <integer>}},
  "confidence": <0.0-1.0>
}}"""

CONSTRUCT_SYSTEM_PROMPT = f"""You are a settlement reconciliation analyst. A settlement came in for a \
different net amount than the transaction's own records predict, and the deterministic resolver \
could NOT find any combination of known causes that accounts for it.

You are given the observed delta and every individual cause the resolver considered possible. Build \
the breakdown that explains the delta, selecting candidates by their number.

{_ADVICE_GUIDANCE}

Respond with ONLY a JSON object, no prose around it:
{{
  "decomposition": [
    {{"candidate": <number from the list>, "why": "<short reason this one really happened>"}}
  ],
  "proposed_action": {{"type": "<recover_from_acquirer|adjust_ledger|write_off|escalate>", "amount": <integer>}},
  "confidence": <0.0-1.0>
}}

Do not retype the amounts -- each candidate's amount is fixed and already correct, including its \
sign. The amounts of the candidates you pick must add up to the observed delta."""


READER_SYSTEM_PROMPT = """You read bank settlement remittance advice. It is abbreviated, \
inconsistently punctuated, and written in a hurry.

For each charge type listed, decide what the advice says about THIS settlement cycle:

  "applied"       the advice says this charge WAS applied/deducted/withheld this cycle
  "not_applied"   the advice mentions it but says it was NOT applied -- denied, reversed, waived, \
released, exempted, cancelled, still pending or proposed, or scheduled to begin in a later cycle
  "not_mentioned" the advice says nothing about it either way

Be careful: mentioning a charge is not the same as confirming it. A great deal of this text exists \
precisely to record that something did NOT happen.

Respond with ONLY a JSON object mapping each charge type to one of those three strings:
{"fee_rate_mismatch": "...", "gst_on_fee_mismatch": "...", "duplicate_refund": "...", \
"tds_deduction": "...", "rolling_reserve": "...", "fx_rounding": "...", "promotional_waiver": "..."}"""

_READER_CAUSES = (
    "fee_rate_mismatch",
    "gst_on_fee_mismatch",
    "duplicate_refund",
    "tds_deduction",
    "rolling_reserve",
    "fx_rounding",
    "promotional_waiver",
)


class AttributionOutput(BaseModel):
    transaction_id: str
    components: list[CauseCandidate]
    proposed_action: dict | None = None
    confidence: float
    provider: str
    reasoning: str = ""
    verified: bool = False
    verify_rounds_used: int = 0
    resolver_status: str = ""
    ambiguity: int = 0
    chance_baseline: float = 0.0
    last_failure: str = ""

    @property
    def cause_multiset(self) -> tuple[tuple[str, int], ...]:
        return tuple(sorted((c.cause, c.amount) for c in self.components))


def _confidence_from_verification(verified: bool, rounds_used: int) -> float:
    """Confidence as a FACT about what happened, not a number the model asserted about itself.

    The model's own self-reported confidence is deliberately discarded. It was consistently
    uninformative in this project's earlier measurements -- high on wrong answers as readily as right
    ones, which is the usual finding and the reason the calibration layer exists at all. What
    replaces it is how many rounds of deterministic verification the answer survived: a proposal that
    verified first try is worth more than one that needed two corrections, and one that never
    verified is worth nothing and escalates.

    This is a reliability signal, NOT a calibrated probability of correctness. Turning it into a
    trust decision is the calibration layer's job (app/calibration/), which scores it against real
    outcomes rather than taking it at face value."""
    if not verified:
        return 0.0
    return max(0.3, 1.0 - 0.2 * (rounds_used - 1))


def _render_candidates(pool: list[CauseCandidate]) -> str:
    lines = []
    for i, c in enumerate(pool, 1):
        # explicit sign on every amount: the convention (a fee BELOW the contracted rate contributes
        # POSITIVELY to the delta) is genuinely counterintuitive, and an unsigned-looking positive
        # number next to the word "fee" invites exactly the misreading this format is here to prevent
        lines.append(f"  [{i}] {c.cause:22s} {c.amount:+12d} paise   {c.evidence_ref:32s} {c.detail}")
    return "\n".join(lines)


def _facts(chain: CausalChain) -> str:
    return f"""Transaction {chain.transaction_id} ({chain.rail}, merchant {chain.merchant_id})

  captured amount:            {chain.hops[0].actual}
  fee charged on record:      {chain.fee_amount}
  tax charged on record:      {chain.tax_amount}
  refunds on record:          {chain.refund_total} across {len(chain.refund_ids)} refund(s)
  predicted net settlement:   {chain.computed_expected_settlement}
  ACTUAL settled amount:      {chain.actual_settled_amount}
  OBSERVED DELTA to explain:  {chain.settlement_delta}

Settlement remittance advice (free text, verbatim):
  {chain.bank_narration or "(none provided)"}"""


def _render_options(options: list[Decomposition]) -> str:
    lines = []
    for i, d in enumerate(options, 1):
        parts = "\n".join(f"       {c.cause:22s} {c.amount:+12d} paise   {c.evidence_ref}" for c in d.components)
        lines.append(f"  OPTION {i}:\n{parts}")
    return "\n".join(lines)


def _describe_choice_case(chain: CausalChain, options: list[Decomposition], total_k: int) -> str:
    windowed = (
        f"\n(These are {len(options)} of the {total_k} valid breakdowns, the most parsimonious first.)"
        if total_k > len(options)
        else ""
    )
    return f"""{_facts(chain)}

The resolver found {total_k} breakdowns that each add up to {chain.settlement_delta}. Exactly one is \
what really happened.{windowed}

{_render_options(options)}

Which option really happened?"""


def _describe_construct_case(chain: CausalChain, pool: list[CauseCandidate]) -> str:
    return f"""{_facts(chain)}

No combination of known causes accounts for this delta. Individual causes the resolver considered:

{_render_candidates(pool)}

Explain the delta of {chain.settlement_delta}."""


def _components_from_choice(payload: dict, options: list[Decomposition]) -> tuple[list[CauseCandidate], str]:
    """Resolve a chosen option number back into its components.

    An out-of-range or missing choice returns nothing, which fails the sum check and produces a
    specific arithmetic complaint the model can act on -- the same treatment as any other bad
    proposal, rather than a silent parse failure that would look like a provider outage."""
    raw = payload.get("choice", payload.get("option"))
    why = str(payload.get("why", ""))[:600]
    try:
        i = int(raw)
    except (TypeError, ValueError):
        return [], why
    if not 1 <= i <= len(options):
        return [], why
    return list(options[i - 1].components), why


def _components_from_payload(payload: dict, pool: list[CauseCandidate]) -> list[CauseCandidate]:
    """Resolve a proposal into real candidates, preferring selection by candidate number.

    Selection-by-index rather than retyping {cause, amount, evidence_ref} is a deliberate correction,
    made after watching a live 7b run fail 6 of 6 cases: reading the actual verifier complaints
    showed the model was repeatedly choosing the RIGHT candidate and then transcribing its amount
    with the sign flipped ("a fee applied at 0.0045 moves the delta by 1550, not -1550"). The signed
    convention here is genuinely confusing -- a fee charged BELOW the contracted rate is a positive
    contribution to the delta -- and every one of those failures was a transcription error rather
    than a reasoning one. The candidates are already in front of the model with correct amounts, so
    picking them by number removes that whole error class without making the actual task any easier:
    the combinatorics of choosing the right 2-4 of ~30 are exactly unchanged, and the keyword
    baseline selects from the identical pool, so the comparison stays symmetric.

    Free-form components are still accepted, because an UNMATCHED case has no pool to select from and
    the model must be able to construct there. They go through the same verifier either way.
    """
    out: list[CauseCandidate] = []
    for item in payload.get("decomposition", []) or []:
        if not isinstance(item, dict):
            continue
        index = item.get("candidate", item.get("index"))
        if isinstance(index, (int, str)):
            try:
                i = int(index)
            except (TypeError, ValueError):
                i = 0
            if 1 <= i <= len(pool):
                picked = pool[i - 1]
                out.append(
                    CauseCandidate(
                        cause=picked.cause,
                        amount=picked.amount,
                        evidence_ref=picked.evidence_ref,
                        detail=str(item.get("why", ""))[:200] or picked.detail,
                    )
                )
                continue
            # An out-of-range number is left to fail the sum check with a specific complaint rather
            # than silently dropped, which would make the arithmetic feedback misleading.
            out.append(
                CauseCandidate(cause="fx_rounding", amount=0, evidence_ref=f"invalid_candidate:{index}", detail=f"candidate {index} is not in the list")
            )
            continue
        try:
            out.append(
                CauseCandidate(
                    cause=item["cause"],
                    amount=int(item["amount"]),
                    evidence_ref=str(item["evidence_ref"]),
                    detail=str(item.get("why", ""))[:200],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def attribute_mock(
    chain: CausalChain, context: ToolContext, resolver_output: ResolverOutput, tolerance: int = DEFAULT_TOLERANCE_PAISE
) -> AttributionOutput:
    """The strongest rule I could write, standing in as the zero-cost provider. See module docstring."""
    # the SAME window, in the SAME shuffled order the model is shown (resolver.present_options), so
    # neither side is choosing from a list the other was denied and neither can exploit position --
    # the comparison is only meaningful if it is symmetric in both
    options = present_options(resolver_output.decompositions, chain.transaction_id, limit=OPTION_WINDOW)
    best, tied = best_decomposition_by_advice(options, chain.bank_narration)
    components = list(best.components) if best else []
    verification = verify_decomposition(chain, context, components, tolerance=tolerance)
    return AttributionOutput(
        transaction_id=chain.transaction_id,
        components=components,
        confidence=_confidence_from_verification(verification.passed, 1),
        provider="mock",
        reasoning=(
            f"Keyword/negation read of the remittance advice selected 1 of {resolver_output.ambiguity} "
            f"arithmetically valid decompositions ({tied} tied on advice agreement, broken by fewest components)."
        ),
        verified=verification.passed,
        verify_rounds_used=1,
        resolver_status=resolver_output.status,
        ambiguity=resolver_output.ambiguity,
        chance_baseline=resolver_output.chance_baseline,
    )


def _run_verify_loop(
    chain: CausalChain,
    context: ToolContext,
    resolver_output: ResolverOutput,
    pool: list[CauseCandidate],
    provider_name: str,
    ask: "callable",
    tolerance: int,
    max_rounds: int,
) -> AttributionOutput:
    """The provider-agnostic half of the loop: propose, verify, hand back the specific failure, repeat.

    `ask(messages) -> str` is the only provider-specific piece."""
    choosing = resolver_output.status == "UNDER_DETERMINED"
    options = present_options(resolver_output.decompositions, chain.transaction_id, limit=OPTION_WINDOW) if choosing else []
    messages = [
        {"role": "system", "content": CHOICE_SYSTEM_PROMPT if choosing else CONSTRUCT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                _describe_choice_case(chain, options, resolver_output.ambiguity)
                if choosing
                else _describe_construct_case(chain, pool)
            ),
        },
    ]
    components: list[CauseCandidate] = []
    verification: VerificationResult | None = None
    reasoning = ""
    action = None

    for round_no in range(1, max_rounds + 1):
        try:
            raw = ask(messages)
        except Exception as e:  # noqa: BLE001 -- any provider failure escalates, it never guesses
            return AttributionOutput(
                transaction_id=chain.transaction_id,
                components=[],
                confidence=0.0,
                provider=provider_name,
                reasoning=f"Provider call failed ({type(e).__name__}: {e}); escalating rather than guessing.",
                verified=False,
                verify_rounds_used=round_no - 1,
                resolver_status=resolver_output.status,
                ambiguity=resolver_output.ambiguity,
                chance_baseline=resolver_output.chance_baseline,
                last_failure=str(e)[:300],
            )

        try:
            payload = json.loads(_strip_fences(raw))
        except (json.JSONDecodeError, TypeError):
            messages.append({"role": "assistant", "content": raw[:1500]})
            messages.append({"role": "user", "content": "That was not valid JSON. Respond with ONLY the JSON object described."})
            continue

        if choosing:
            components, reasoning = _components_from_choice(payload, options)
        else:
            components = _components_from_payload(payload, pool)
            reasoning = "; ".join(c.detail for c in components if c.detail)[:600]
        action = payload.get("proposed_action")
        verification = verify_decomposition(chain, context, components, tolerance=tolerance)

        if verification.passed:
            return AttributionOutput(
                transaction_id=chain.transaction_id,
                components=components,
                proposed_action=action if isinstance(action, dict) else None,
                confidence=_confidence_from_verification(True, round_no),
                provider=provider_name,
                reasoning=reasoning,
                verified=True,
                verify_rounds_used=round_no,
                resolver_status=resolver_output.status,
                ambiguity=resolver_output.ambiguity,
                chance_baseline=resolver_output.chance_baseline,
            )

        messages.append({"role": "assistant", "content": raw[:1500]})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your decomposition did not pass verification:\n"
                    f"{verification.failure_feedback()}\n\n"
                    "Fix these specific problems and respond with ONLY the corrected JSON object."
                ),
            }
        )

    return AttributionOutput(
        transaction_id=chain.transaction_id,
        components=components,
        proposed_action=action if isinstance(action, dict) else None,
        confidence=0.0,
        provider=provider_name,
        reasoning=reasoning or "No proposal survived verification.",
        verified=False,
        verify_rounds_used=max_rounds,
        resolver_status=resolver_output.status,
        ambiguity=resolver_output.ambiguity,
        chance_baseline=resolver_output.chance_baseline,
        last_failure=verification.failure_feedback()[:400] if verification else "",
    )


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def attribute_ollama(
    chain: CausalChain,
    context: ToolContext,
    resolver_output: ResolverOutput,
    pool: list[CauseCandidate],
    model: str = DEFAULT_OLLAMA_MODEL,
    tolerance: int = DEFAULT_TOLERANCE_PAISE,
    max_rounds: int = MAX_VERIFY_ROUNDS,
) -> AttributionOutput:
    from ollama import Client

    client = Client(timeout=120.0)

    def ask(messages: list[dict]) -> str:
        response = client.chat(model=model, messages=messages, options={"temperature": 0.0})
        return response.message.content or ""

    return _run_verify_loop(chain, context, resolver_output, pool, "ollama", ask, tolerance, max_rounds)


def attribute_groq(
    chain: CausalChain,
    context: ToolContext,
    resolver_output: ResolverOutput,
    pool: list[CauseCandidate],
    model: str = DEFAULT_GROQ_MODEL,
    tolerance: int = DEFAULT_TOLERANCE_PAISE,
    max_rounds: int = MAX_VERIFY_ROUNDS,
) -> AttributionOutput:
    from groq import Groq

    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    def ask(messages: list[dict]) -> str:
        response = client.chat.completions.create(model=model, messages=messages, temperature=0.0)
        return response.choices[0].message.content or ""

    return _run_verify_loop(chain, context, resolver_output, pool, "groq", ask, tolerance, max_rounds)


def attribute_reader(
    chain: CausalChain,
    context: ToolContext,
    resolver_output: ResolverOutput,
    ask: "callable",
    provider_name: str,
    tolerance: int = DEFAULT_TOLERANCE_PAISE,
) -> AttributionOutput:
    """The model does ONLY the reading; the deterministic scorer does the matching.

    This is the division of labour the residual architecture actually implies, and it is a sharper
    experiment than handing the model the whole option list. The keyword baseline is already two
    separable stages: read the advice into a set of assertions about what applied, then score every
    valid decomposition against those assertions. Stage two is arithmetic bookkeeping that a rule does
    perfectly and a language model has no advantage at. Stage one is reading comprehension over messy
    negated text, which is the opposite.

    So this swaps ONLY stage one. Same options, same scorer, same tie-break -- the single difference
    between this column and the keyword column is whether a regex or a model read the sentence. Any
    gap between them is attributable to language understanding and to nothing else, which is not true
    of the whole-option-list comparison, where a loss could equally be the model failing to track 40
    options.
    """
    from app.resolver.keyword_baseline import score_decomposition

    options = present_options(resolver_output.decompositions, chain.transaction_id, limit=OPTION_WINDOW)
    prompt = f"Remittance advice:\n  {chain.bank_narration or '(none provided)'}\n\nCharge types: {', '.join(_READER_CAUSES)}"
    try:
        raw = ask([{"role": "system", "content": READER_SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
        verdicts = json.loads(_strip_fences(raw))
    except Exception as e:  # noqa: BLE001
        return AttributionOutput(
            transaction_id=chain.transaction_id,
            components=[],
            confidence=0.0,
            provider=provider_name,
            reasoning=f"Reader call failed ({type(e).__name__}); escalating rather than guessing.",
            resolver_status=resolver_output.status,
            ambiguity=resolver_output.ambiguity,
            chance_baseline=resolver_output.chance_baseline,
            last_failure=str(e)[:300],
        )

    asserted: dict[str, bool] = {}
    for cause in _READER_CAUSES:
        verdict = str(verdicts.get(cause, "not_mentioned")).lower()
        if verdict == "applied":
            asserted[cause] = True
        elif verdict in ("not_applied", "not applied"):
            asserted[cause] = False

    if not options:
        components: list[CauseCandidate] = []
    else:
        scored = [(score_decomposition(d, asserted), -len(d.components), i) for i, d in enumerate(options)]
        best = max(scored)
        components = list(options[best[2]].components)

    verification = verify_decomposition(chain, context, components, tolerance=tolerance)
    return AttributionOutput(
        transaction_id=chain.transaction_id,
        components=components,
        confidence=_confidence_from_verification(verification.passed, 1),
        provider=provider_name,
        reasoning=f"Model read the advice as: {json.dumps({k: ('applied' if v else 'not_applied') for k, v in asserted.items()})}",
        verified=verification.passed,
        verify_rounds_used=1,
        resolver_status=resolver_output.status,
        ambiguity=resolver_output.ambiguity,
        chance_baseline=resolver_output.chance_baseline,
    )


Provider = Literal["mock", "ollama", "groq", "ollama_reader", "groq_reader"]


def attribute(
    chain: CausalChain,
    context: ToolContext,
    resolver_output: ResolverOutput,
    pool: list[CauseCandidate],
    provider: str | None = None,
    tolerance: int = DEFAULT_TOLERANCE_PAISE,
    max_rounds: int = MAX_VERIFY_ROUNDS,
    model: str | None = None,
) -> AttributionOutput:
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    if provider == "ollama":
        return attribute_ollama(chain, context, resolver_output, pool, model=model or DEFAULT_OLLAMA_MODEL, tolerance=tolerance, max_rounds=max_rounds)
    if provider == "groq":
        return attribute_groq(chain, context, resolver_output, pool, model=model or DEFAULT_GROQ_MODEL, tolerance=tolerance, max_rounds=max_rounds)
    if provider == "ollama_reader":
        from ollama import Client

        client = Client(timeout=120.0)

        def ask_ollama(messages: list[dict]) -> str:
            return client.chat(model=model or DEFAULT_OLLAMA_MODEL, messages=messages, options={"temperature": 0.0}).message.content or ""

        return attribute_reader(chain, context, resolver_output, ask_ollama, "ollama_reader", tolerance=tolerance)
    if provider == "groq_reader":
        from groq import Groq

        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

        def ask_groq(messages: list[dict]) -> str:
            return client.chat.completions.create(model=model or DEFAULT_GROQ_MODEL, messages=messages, temperature=0.0).choices[0].message.content or ""

        return attribute_reader(chain, context, resolver_output, ask_groq, "groq_reader", tolerance=tolerance)
    return attribute_mock(chain, context, resolver_output, tolerance=tolerance)
