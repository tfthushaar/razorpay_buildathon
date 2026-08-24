"""Regression tests for the narrator's tools and mock-backend agent loop (spec §6.4).

Uses the real generator + chain builder + matching engine to produce the actual
"needs_narration" slice, then checks the mock narrator (which calls the real tool functions,
only stubbing the final synthesis step — see agent.py's module docstring) classifies each
adversarial/ambiguous case correctly using genuine cross-referenced signal, not a hardcoded
answer.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.engine import run_matching_engine
from app.narrator.agent import narrate_groq, narrate_mock, narrate_ollama
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
