"""Regression tests for the narrator's tools and mock-backend agent loop.

Uses the real generator + chain builder + matching engine to produce the actual
"needs_narration" slice, then checks the mock narrator (which calls the real tool functions,
only stubbing the final synthesis step — see agent.py's module docstring) classifies each
adversarial/ambiguous case correctly using genuine cross-referenced signal, not a hardcoded
answer.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.engine import run_matching_engine
from app.narrator.agent import narrate, narrate_groq, narrate_mock, narrate_ollama
from app.narrator.tools import build_tool_context, check_batch_anomalies, recall_similar_resolutions


def _narration_queue(seed=42, main_n=150, stress_n=0):
    main, _ = generate(seed=seed, main_n=main_n, stress_n=stress_n)
    chains = build_all_chains(main)
    results = run_matching_engine(chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    context = build_tool_context(main, chains)
    queue = [txn_id for txn_id, r in results.items() if r.resolution == "needs_narration"]
    return chains, context, queue, gt_by_id


def test_check_batch_anomalies_finds_the_real_duplicate_and_netting_signals():
    chains, context, queue, gt_by_id = _narration_queue()
    for txn_id in queue:
        label = gt_by_id[txn_id]
        result = check_batch_anomalies(txn_id, context)
        if label == "duplicate_refund":
            assert result["duplicate_refund_match"] is not None, f"{txn_id} should trip the duplicate check"
        elif label == "netting_trap":
            assert result["netting_partner"] is not None, f"{txn_id} should find its netting partner"
            linked = chains[txn_id]
            assert result["netting_partner"]["transaction_id"] in {
                c for c in context.transaction_ids_by_settlement_batch[linked.settlement_batch_id]
            }
        elif label == "genuine_error":
            assert result["duplicate_refund_match"] is None
            assert result["netting_partner"] is None


def test_mock_narrator_classifies_the_narration_queue_correctly():
    _, context, queue, gt_by_id = _narration_queue(main_n=150)
    assert queue, "test setup should produce a non-empty narration queue"

    from app.chain.builder import build_all_chains as _bac  # local import just for chains access below

    correct = 0
    for txn_id in queue:
        chain = context.chains[txn_id]
        output = narrate_mock(chain, context)
        assert output.provider == "mock"
        assert 0.0 <= output.confidence <= 1.0
        assert output.category in {"duplicate_refund", "netting_trap", "genuine_error"}
        assert output.tool_calls, "mock narrator should still record real tool calls, not skip straight to an answer"
        if output.category == gt_by_id[txn_id]:
            correct += 1

    accuracy = correct / len(queue)
    assert accuracy >= 0.9, f"mock narrator accuracy on the narration queue was only {accuracy:.1%}"


def test_narrate_groq_fails_safe_on_out_of_schema_category():
    """A real ollama run once returned category="timing_lag" (a category the system prompt
    explicitly forbids) at confidence 0.9 -- valid JSON, invalid schema, and nothing downstream of
    the parse step checked for it. Caught by an external audit 2026-08-24 reading the live DB
    directly (see BUILD_LOG.md). Proves the fix: an out-of-schema category must fail safe exactly
    like malformed JSON does, not sail through as a confident wrong answer."""
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]
    fake_message = SimpleNamespace(
        tool_calls=None,
        content='{"category": "timing_lag", "confidence": 0.9, "reasoning": "looks like a timing issue"}',
    )
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        output = narrate_groq(chain, context)

    assert output.category == "genuine_error"
    assert output.confidence == 0.0
    assert output.provider == "groq"
    assert "timing_lag" in output.reasoning


def test_narrate_groq_fails_safe_with_a_clean_message_when_no_api_key_is_configured():
    """Round 20's audit picked "groq" on the live deployment (Render only sets LLM_PROVIDER=mock,
    no GROQ_API_KEY) and found the resulting escalation reasoning was a raw, unpolished
    'Narrator crashed unexpectedly (KeyError: 'GROQ_API_KEY')' leaking out of narrate()'s generic
    orchestration backstop -- functionally safe, but reads as an internal error rather than a
    designed message. Fixed by catching the missing key specifically inside narrate_groq itself."""
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("GROQ_API_KEY", None)
        output = narrate_groq(chain, context)

    assert output.category == "genuine_error"
    assert output.confidence == 0.0
    assert output.provider == "groq"
    assert "GROQ_API_KEY" in output.reasoning
    assert "KeyError" not in output.reasoning


# Round 7's audit found the round-6 fix above only guarded the JSON-parse step, not the field
# access/construction that follows it -- a syntactically valid but structurally wrong payload
# (missing key, wrapped in an array, wrong value type) raised an uncaught exception that crashed
# the whole batch instead of escalating the one transaction. These five shapes are the exact ones
# the audit reproduced live against both providers.
_MALFORMED_FINAL_ANSWERS = [
    ('{"category": "genuine_error", "reasoning": "no confidence key at all"}', "missing confidence key"),
    ('{"category": "genuine_error", "confidence": 0.5}', "missing reasoning key"),
    ('[{"category": "genuine_error", "confidence": 0.5, "reasoning": "wrapped in an array"}]', "top-level JSON array, not an object"),
    ("null", "top-level JSON null"),
    ('{"category": "genuine_error", "confidence": "high", "reasoning": "confidence is a string"}', "confidence is a string, not a number"),
]


def test_narrate_groq_fails_safe_on_structurally_malformed_final_answer():
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    for content, description in _MALFORMED_FINAL_ANSWERS:
        fake_message = SimpleNamespace(tool_calls=None, content=content)
        fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])
        with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            MockGroq.return_value.chat.completions.create.return_value = fake_response
            output = narrate_groq(chain, context)
        assert output.category == "genuine_error", f"should fail safe on: {description}"
        assert output.confidence == 0.0, f"should fail safe on: {description}"
        assert output.provider == "groq", f"should fail safe on: {description}"


def test_narrate_ollama_fails_safe_on_structurally_malformed_final_answer():
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    for content, description in _MALFORMED_FINAL_ANSWERS:
        fake_message = SimpleNamespace(tool_calls=None, content=content)
        fake_response = SimpleNamespace(message=fake_message)
        with patch("ollama.Client") as MockClient:
            MockClient.return_value.chat.return_value = fake_response
            output = narrate_ollama(chain, context)
        assert output.category == "genuine_error", f"should fail safe on: {description}"
        assert output.confidence == 0.0, f"should fail safe on: {description}"
        assert output.provider == "ollama", f"should fail safe on: {description}"


def test_narrate_groq_clamps_out_of_range_confidence():
    """Round 7 also found `confidence` was never validated to be in [0.0, 1.0] -- a model
    returning confidence=5.0 or a negative value would construct a "valid" NarratorOutput that
    then produces a negative escalation priority score (escalation.py's ambiguity = 1 -
    confidence) and a nonsensical percentage in the UI. Confidence should be clamped, not trusted
    verbatim, same principle as validating category."""
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    fake_message = SimpleNamespace(
        tool_calls=None,
        content='{"category": "genuine_error", "confidence": 5.0, "reasoning": "overconfident"}',
    )
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])
    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        output = narrate_groq(chain, context)

    assert output.category == "genuine_error"  # a real, valid category this time -- not a fail-safe path
    assert output.confidence == 1.0, "confidence must be clamped to the valid [0.0, 1.0] range"


# Found by re-reading the tool-call branch (the `if msg.tool_calls:` block, one layer earlier in
# the same loop) while writing up round 7's fix -- the exact same "trust an LLM-shaped value
# without checking it" gap round 7 just fixed for the final answer was also sitting, unfixed, in
# how a *tool call* gets executed. Reproduced directly before writing these tests (see BUILD_LOG):
# malformed tool-call-arguments JSON and a hallucinated/unknown tool name both crashed the whole
# batch uncaught, the same failure shape round 7 found, one call earlier.
def test_narrate_groq_fails_safe_on_an_unusable_tool_call():
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    bad_tool_calls = [
        (SimpleNamespace(id="call_1", function=SimpleNamespace(name="lookup_fee_schedule", arguments="{not valid json")), "malformed arguments JSON"),
        (SimpleNamespace(id="call_1", function=SimpleNamespace(name="some_hallucinated_tool", arguments="{}")), "hallucinated/unknown tool name"),
    ]
    for tc, description in bad_tool_calls:
        tc.model_dump = lambda tc=tc: {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        fake_message = SimpleNamespace(tool_calls=[tc], content=None)
        fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])
        with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            MockGroq.return_value.chat.completions.create.return_value = fake_response
            output = narrate_groq(chain, context)
        assert output.category == "genuine_error", f"should fail safe on: {description}"
        assert output.confidence == 0.0, f"should fail safe on: {description}"
        assert output.provider == "groq", f"should fail safe on: {description}"


def test_narrate_ollama_fails_safe_on_an_unusable_tool_call():
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    bad_tool_calls = [
        (SimpleNamespace(function=SimpleNamespace(name="lookup_fee_schedule", arguments="not-a-mapping")), "arguments not convertible to a dict"),
        (SimpleNamespace(function=SimpleNamespace(name="some_hallucinated_tool", arguments={})), "hallucinated/unknown tool name"),
    ]
    for tc, description in bad_tool_calls:
        tc.model_dump = lambda tc=tc: {"function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        fake_message = SimpleNamespace(tool_calls=[tc], content=None)
        fake_response = SimpleNamespace(message=fake_message)
        with patch("ollama.Client") as MockClient:
            MockClient.return_value.chat.return_value = fake_response
            output = narrate_ollama(chain, context)
        assert output.category == "genuine_error", f"should fail safe on: {description}"
        assert output.confidence == 0.0, f"should fail safe on: {description}"
        assert output.provider == "ollama", f"should fail safe on: {description}"


# Round 8's audit found the tool-call guard above still didn't catch everything: `tc.function`
# being None, or a real tool (recall_similar_resolutions, the one tool that actually reads its
# arguments) receiving a non-dict value like a JSON array or JSON null, all raised an UNCAUGHT
# AttributeError -- the except tuple only had (json.JSONDecodeError, TypeError, ValueError), not
# AttributeError. Reproduced independently before fixing, same as every other finding in this log.
def test_narrate_groq_fails_safe_on_tool_call_shapes_that_raise_attributeerror():
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    bad_tool_calls = [
        (SimpleNamespace(id="c1", function=None), "tc.function is None"),
        (SimpleNamespace(id="c1", function=SimpleNamespace(name="recall_similar_resolutions", arguments="[1,2,3]")), "arguments is a JSON array, not an object"),
        (SimpleNamespace(id="c1", function=SimpleNamespace(name="recall_similar_resolutions", arguments="null")), "arguments is JSON null"),
    ]
    for tc, description in bad_tool_calls:
        tc.model_dump = lambda tc=tc: {"id": tc.id}
        fake_message = SimpleNamespace(tool_calls=[tc], content=None)
        fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])
        with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
            MockGroq.return_value.chat.completions.create.return_value = fake_response
            output = narrate_groq(chain, context)
        assert output.category == "genuine_error", f"should fail safe on: {description}"
        assert output.confidence == 0.0, f"should fail safe on: {description}"
        assert output.provider == "groq", f"should fail safe on: {description}"


def test_narrate_ollama_fails_safe_on_tool_call_with_missing_function():
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    tc = SimpleNamespace(function=None, model_dump=lambda: {})
    fake_message = SimpleNamespace(tool_calls=[tc], content=None)
    fake_response = SimpleNamespace(message=fake_message)
    with patch("ollama.Client") as MockClient:
        MockClient.return_value.chat.return_value = fake_response
        output = narrate_ollama(chain, context)

    assert output.category == "genuine_error"
    assert output.confidence == 0.0
    assert output.provider == "ollama"


def test_narrate_dispatcher_fails_safe_on_a_completely_unforeseen_exception():
    """Round 8's core structural point: rounds 5-8 each found a *different* unguarded
    model-supplied value inside a specific provider function's own exception handling -- a real,
    recurring pattern that per-function whack-a-mole can't fully close, since the next one is
    always a shape nobody's seen yet. This tests the orchestration-level backstop in narrate()
    itself, independent of any specific provider's internal logic: even a totally unanticipated
    exception type from a provider function must never propagate out of narrate() and crash the
    batch. Mocks narrate_groq directly (not the HTTP client) so this doesn't depend on which
    specific failure modes narrate_groq happens to guard against today."""
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]

    with patch("app.narrator.agent.narrate_groq") as mock_narrate_groq:
        mock_narrate_groq.side_effect = RuntimeError("a totally unforeseen failure mode")
        output = narrate(chain, context, provider="groq")

    assert output.category == "genuine_error"
    assert output.confidence == 0.0
    assert output.provider == "groq"
    assert "unforeseen failure mode" in output.reasoning


def test_narrate_ollama_fails_safe_on_out_of_schema_category():
    """Same fix, same live-observed failure, the other real provider -- see the Groq version of
    this test for the full story."""
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]
    fake_message = SimpleNamespace(
        tool_calls=None,
        content='{"category": "timing_lag", "confidence": 0.9, "reasoning": "looks like a timing issue"}',
    )
    fake_response = SimpleNamespace(message=fake_message)

    with patch("ollama.Client") as MockClient:
        MockClient.return_value.chat.return_value = fake_response
        output = narrate_ollama(chain, context)

    assert output.category == "genuine_error"
    assert output.confidence == 0.0
    assert output.provider == "ollama"
    assert "timing_lag" in output.reasoning


def test_narrate_ollama_constructs_its_client_with_a_finite_timeout():
    """Verified directly (not assumed) that ollama.Client() with no kwargs resolves to
    timeout=None -- the ollama package overrides httpx.Client's own sane 5s default to unbounded.
    Every fail-safe in this file protects against a call that raises; a call that never returns
    bypasses all of them, including the retry logic's own httpx.TimeoutException handling, which
    was already correctly wired but had nothing to ever catch. Doesn't wait out a real timeout
    (that would make the suite unbearably slow) -- just proves the client is constructed with a
    real, finite value, which is the actual fix."""
    _, context, queue, _ = _narration_queue(main_n=150)
    chain = context.chains[queue[0]]
    fake_message = SimpleNamespace(tool_calls=None, content='{"category": "genuine_error", "confidence": 0.5, "reasoning": "ok"}')
    fake_response = SimpleNamespace(message=fake_message)

    with patch("ollama.Client") as MockClient:
        MockClient.return_value.chat.return_value = fake_response
        narrate_ollama(chain, context)

    assert MockClient.call_args is not None, "Client() should have been constructed"
    _, kwargs = MockClient.call_args
    assert kwargs.get("timeout") is not None, "Client() must be constructed with an explicit, finite timeout -- not left at the library's own unbounded default"
    assert kwargs["timeout"] > 0


def test_recall_grows_as_the_run_progresses():
    _, context, queue, _ = _narration_queue(main_n=150)
    assert context.audit_log == []
    chain = context.chains[queue[0]]
    before = recall_similar_resolutions("duplicate_refund", context)
    assert before["prior_count"] == 0

    narrate_mock(chain, context)
    assert len(context.audit_log) == 1

    after = recall_similar_resolutions(context.audit_log[0]["category"], context)
    assert after["prior_count"] == 1
