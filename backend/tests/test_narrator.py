"""Regression tests for the narrator's tools and mock-backend agent loop (spec §6.4).

Uses the real generator + chain builder + matching engine to produce the actual
"needs_narration" slice, then checks the mock narrator (which calls the real tool functions,
only stubbing the final synthesis step — see agent.py's module docstring) classifies each
adversarial/ambiguous case correctly using genuine cross-referenced signal, not a hardcoded
answer.
"""

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.engine import run_matching_engine
from app.narrator.agent import narrate_mock
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
