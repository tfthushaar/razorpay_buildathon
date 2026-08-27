"""Tests for the settlement Q&A agent (app/qa/), a genuinely separate agentic loop from the
narrator -- same provider dispatch, circuit breaker, and fail-safe discipline, mocked LLM clients
here the same way test_narrator.py does, since the mapping was verified live against a real
Ollama call before being trusted (see BUILD_LOG.md)."""

import os
from types import SimpleNamespace
from unittest.mock import patch

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.narrator.tools import build_tool_context
from app.qa.agent import answer_groq, answer_mock, answer_ollama, answer_question
from app.qa.tools import build_settled_at_index, find_transactions_by_date, get_transaction_detail, list_flagged_transactions


def _real_context():
    main, _ = generate(seed=42, main_n=60, stress_n=0)
    chains = build_all_chains(main)
    context = build_tool_context(main, chains)
    settled_at_index = build_settled_at_index(main)
    return main, chains, context, settled_at_index


def test_find_transactions_by_date_matches_a_real_settlement_date():
    main, chains, context, settled_at_index = _real_context()
    some_txn_id, some_date = next(iter(settled_at_index.items()))
    date_str = some_date.date().isoformat()

    result = find_transactions_by_date(date_str, chains, settled_at_index)

    assert result["date"] == date_str
    assert result["count"] >= 1
    assert any(m["transaction_id"] == some_txn_id for m in result["matches"])


def test_find_transactions_by_date_rejects_an_unparseable_date():
    _, chains, _, settled_at_index = _real_context()
    result = find_transactions_by_date("not-a-date", chains, settled_at_index)
    assert "error" in result


def test_get_transaction_detail_returns_real_chain_fields():
    _, chains, _, _ = _real_context()
    txn_id = next(iter(chains))
    detail = get_transaction_detail(txn_id, chains)
    assert detail["transaction_id"] == txn_id
    assert "settlement_delta" in detail
    assert "hops" in detail
    assert len(detail["hops"]) > 0


def test_get_transaction_detail_reports_a_clean_error_for_an_unknown_id():
    _, chains, _, _ = _real_context()
    detail = get_transaction_detail("order_does_not_exist", chains)
    assert "error" in detail


def test_list_flagged_transactions_finds_every_real_duplicate_refund_and_netting_trap():
    # main_n=60 (the size _real_context uses elsewhere in this file) isn't guaranteed to roll any
    # adversarial cases -- this needs a batch large enough to reliably contain some, same reason
    # test_narrator.py's own duplicate/netting check uses main_n=150 rather than 60.
    main, _ = generate(seed=42, main_n=150, stress_n=0)
    chains = build_all_chains(main)
    context = build_tool_context(main, chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    expected_flagged = {txn_id for txn_id, label in gt_by_id.items() if label in ("duplicate_refund", "netting_trap")}
    assert expected_flagged, "fixture assumption: seed 42 at main_n=150 should roll at least one adversarial case"

    result = list_flagged_transactions(context)

    flagged_ids = {f["transaction_id"] for f in result["flagged"]}
    assert result["count"] == len(result["flagged"])
    assert expected_flagged.issubset(flagged_ids)


def test_answer_mock_grounds_its_answer_in_real_tool_calls():
    _, _, context, settled_at_index = _real_context()
    answer = answer_mock("why was Tuesday's payout short?", context, settled_at_index)
    assert answer.provider == "mock"
    assert len(answer.tool_calls) > 0
    assert all(tc.tool == "get_transaction_detail" for tc in answer.tool_calls)
    assert set(answer.cited_transaction_ids).issubset(context.chains.keys())


def test_answer_groq_cites_only_transaction_ids_it_actually_looked_up():
    _, _, context, settled_at_index = _real_context()
    real_txn_id = next(iter(context.chains))

    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_transaction_detail", arguments=f'{{"transaction_id": "{real_txn_id}"}}'),
    )
    tc.model_dump = lambda tc=tc: {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
    tool_call_message = SimpleNamespace(content="", tool_calls=[tc])
    final_message = SimpleNamespace(
        tool_calls=None,
        content=f'{{"answer": "That transaction settled short because of a fee mismatch.", "cited_transaction_ids": ["{real_txn_id}"]}}',
    )
    tool_response = SimpleNamespace(choices=[SimpleNamespace(message=tool_call_message)])
    final_response = SimpleNamespace(choices=[SimpleNamespace(message=final_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.side_effect = [tool_response, final_response]
        answer = answer_groq("why did this settle short?", context, settled_at_index)

    assert answer.provider == "groq"
    assert answer.cited_transaction_ids == [real_txn_id]
    assert len(answer.tool_calls) == 1
    assert answer.tool_calls[0].tool == "get_transaction_detail"


def test_answer_groq_fails_safe_on_malformed_final_json():
    _, _, context, settled_at_index = _real_context()
    fake_message = SimpleNamespace(tool_calls=None, content="not valid json at all")
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        answer = answer_groq("some question", context, settled_at_index)

    assert answer.provider == "groq"
    assert answer.cited_transaction_ids == []
    assert "could not be parsed" in answer.answer.lower()


def test_answer_groq_fails_safe_with_a_clean_message_when_no_api_key_is_configured():
    _, _, context, settled_at_index = _real_context()
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("GROQ_API_KEY", None)
        answer = answer_groq("some question", context, settled_at_index)

    assert answer.provider == "groq"
    assert "GROQ_API_KEY" in answer.answer
    assert "KeyError" not in answer.answer


def test_answer_ollama_happy_path():
    _, _, context, settled_at_index = _real_context()
    real_txn_id = next(iter(context.chains))

    with patch("ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.side_effect = [
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "get_transaction_detail", "arguments": {"transaction_id": real_txn_id}}}],
                }
            },
            {"message": {"content": f'{{"answer": "Found it.", "cited_transaction_ids": ["{real_txn_id}"]}}', "tool_calls": None}},
        ]
        answer = answer_ollama("why did this settle short?", context, settled_at_index)

    assert answer.provider == "ollama"
    assert answer.cited_transaction_ids == [real_txn_id]


def test_answer_question_dispatches_to_mock_by_default():
    _, _, context, settled_at_index = _real_context()
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("LLM_PROVIDER", None)
        answer = answer_question("a question", context, settled_at_index)
    assert answer.provider == "mock"


def test_answer_question_fails_safe_on_an_unknown_provider():
    _, _, context, settled_at_index = _real_context()
    answer = answer_question("a question", context, settled_at_index, provider="not-a-real-provider")
    assert "crashed unexpectedly" in answer.answer or "unknown LLM_PROVIDER" in answer.answer
    assert answer.cited_transaction_ids == []
