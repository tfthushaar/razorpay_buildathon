"""Tests for category discovery (app/narrator/discovery.py) -- proposes a candidate new category
for a genuine_error case, grounded in the narrator's own already-gathered tool-call evidence. Never
auto-adopted into NARRATOR_CATEGORIES; these tests check the proposal contract and fail-safe
discipline, mocked the same way test_narrator.py and test_qa_agent.py mock their LLM clients."""

import os
from types import SimpleNamespace
from unittest.mock import patch

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.narrator.agent import narrate_mock
from app.narrator.discovery import (
    CategoryProposal,
    _describe_evidence,
    _describe_prior_proposals,
    propose_category,
    propose_category_groq,
    propose_category_mock,
    propose_category_ollama,
)
from app.narrator.tools import build_tool_context


def _genuine_error_case(main_n=150):
    main, _ = generate(seed=42, main_n=main_n, stress_n=0)
    chains = build_all_chains(main)
    context = build_tool_context(main, chains)
    gt_by_id = {g.transaction_id: g.true_label for g in main.ground_truth}
    genuine_error_ids = [txn_id for txn_id, label in gt_by_id.items() if label == "genuine_error"]
    assert genuine_error_ids, "fixture assumption: seed 42 at main_n=150 should roll a genuine_error case"
    txn_id = genuine_error_ids[0]
    output = narrate_mock(chains[txn_id], context)
    tool_calls = [tc.model_dump() for tc in output.tool_calls]
    return chains[txn_id], tool_calls, context


def test_propose_category_mock_grounds_its_hypothesis_in_real_tool_evidence():
    chain, tool_calls, context = _genuine_error_case()
    proposal = propose_category_mock(chain, tool_calls, context)
    assert proposal.provider == "mock"
    assert proposal.transaction_id == chain.transaction_id
    assert 0.0 <= proposal.confidence <= 1.0
    # every mock branch either names a hypothesis with a matching evidence citation, or explicitly
    # proposes nothing rather than inventing a label -- both are valid, a crash or an ungrounded
    # invented name are not.
    if proposal.proposed_name is not None:
        assert proposal.supporting_evidence


def test_propose_category_groq_parses_a_well_formed_proposal():
    chain, tool_calls, context = _genuine_error_case()
    fake_message = SimpleNamespace(
        content='{"proposed_name": "stale_fx_rate", "hypothesis": "SLA check passed but the delta persists.", '
        '"supporting_evidence": ["check_sla_window reported within_tolerance=True"], "confidence": 0.35}'
    )
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        proposal = propose_category_groq(chain, tool_calls, context)

    assert proposal.provider == "groq"
    assert proposal.proposed_name == "stale_fx_rate"
    assert proposal.confidence == 0.35
    assert proposal.supporting_evidence == ["check_sla_window reported within_tolerance=True"]


def test_propose_category_groq_accepts_an_explicit_null_proposal():
    """The system prompt explicitly allows the model to say "no real hypothesis fits" -- proposed_name
    must accept a real, deliberate null just as readily as a named guess, not just as a fail-safe."""
    chain, tool_calls, context = _genuine_error_case()
    fake_message = SimpleNamespace(
        content='{"proposed_name": null, "hypothesis": "Evidence gathered does not support a specific pattern.", '
        '"supporting_evidence": [], "confidence": 0.0}'
    )
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        proposal = propose_category_groq(chain, tool_calls, context)

    assert proposal.proposed_name is None
    assert proposal.provider == "groq"


def test_propose_category_groq_fails_safe_on_malformed_json():
    chain, tool_calls, context = _genuine_error_case()
    fake_message = SimpleNamespace(content="not valid json at all")
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        proposal = propose_category_groq(chain, tool_calls, context)

    assert proposal.provider == "groq"
    assert proposal.proposed_name is None
    assert proposal.confidence == 0.0
    assert "could not be parsed" in proposal.hypothesis.lower()


def test_propose_category_groq_fails_safe_with_no_api_key():
    chain, tool_calls, context = _genuine_error_case()
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("GROQ_API_KEY", None)
        proposal = propose_category_groq(chain, tool_calls, context)

    assert proposal.provider == "groq"
    assert "GROQ_API_KEY" in proposal.hypothesis
    assert "KeyError" not in proposal.hypothesis


def test_propose_category_ollama_parses_a_well_formed_proposal():
    chain, tool_calls, context = _genuine_error_case()
    with patch("ollama.Client") as MockClient:
        MockClient.return_value.chat.return_value = {
            "message": {
                "content": '{"proposed_name": "delayed_reversal", "hypothesis": "Looks like a delayed reversal.", '
                '"supporting_evidence": ["ledger_gap nonzero"], "confidence": 0.5}'
            }
        }
        proposal = propose_category_ollama(chain, tool_calls, context)

    assert proposal.provider == "ollama"
    assert proposal.proposed_name == "delayed_reversal"
    assert proposal.confidence == 0.5


def test_propose_category_dispatches_to_mock_by_default():
    chain, tool_calls, context = _genuine_error_case()
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop("LLM_PROVIDER", None)
        proposal = propose_category(chain, tool_calls, context)
    assert proposal.provider == "mock"


def test_propose_category_fails_safe_on_an_unknown_provider():
    chain, tool_calls, context = _genuine_error_case()
    proposal = propose_category(chain, tool_calls, context, provider="not-a-real-provider")
    assert proposal.proposed_name is None
    assert "crashed unexpectedly" in proposal.hypothesis or "unknown LLM_PROVIDER" in proposal.hypothesis


# --- Clustering: existing_proposals threaded through so a recurring pattern gets one name, not a
# fresh label each time it shows up (added after measuring 6 distinct names from 8 real proposals
# on the first, non-clustering version of this module). ---


def test_describe_prior_proposals_is_none_with_no_prior_proposals():
    """None, not an empty placeholder string -- verified live that even a "(none yet)" placeholder
    measurably pushes qwen2.5:7b-instruct toward proposing null across the board. The block must be
    genuinely absent from the prompt, not merely empty."""
    assert _describe_prior_proposals(None) is None
    assert _describe_prior_proposals([]) is None


def test_describe_prior_proposals_lists_named_proposals_deduplicated_by_name():
    proposals = [
        CategoryProposal(transaction_id="t1", proposed_name="stale_fx_rate", hypothesis="first case", supporting_evidence=[], confidence=0.4, provider="mock"),
        CategoryProposal(transaction_id="t2", proposed_name=None, hypothesis="no pattern", supporting_evidence=[], confidence=0.0, provider="mock"),
        CategoryProposal(transaction_id="t3", proposed_name="stale_fx_rate", hypothesis="second case, same pattern", supporting_evidence=[], confidence=0.4, provider="mock"),
    ]
    described = _describe_prior_proposals(proposals)
    assert described.count("stale_fx_rate") == 1
    assert "first case" in described
    assert "no pattern" not in described  # a null-named proposal has nothing to reuse -- excluded


def test_describe_evidence_omits_the_prior_proposals_block_entirely_when_nothing_named_exists():
    """Regression guard for the live-verified null-collapse: the section header itself must not
    appear in the prompt when there's nothing to reuse, not just render with an empty/placeholder
    body."""
    chain, tool_calls, context = _genuine_error_case()
    evidence = _describe_evidence(chain, tool_calls, None)
    assert "Proposals already made" not in evidence
    with_prior = _describe_evidence(
        chain,
        tool_calls,
        [CategoryProposal(transaction_id="other", proposed_name="stale_fx_rate", hypothesis="x", supporting_evidence=[], confidence=0.4, provider="mock")],
    )
    assert "Proposals already made" in with_prior
    assert "stale_fx_rate" in with_prior


def test_propose_category_mock_reuses_the_exact_prior_hypothesis_when_it_already_exists():
    """The deterministic mock rule only ever proposes one name (unexplained_settlement_delay) -- this
    checks it reuses the PRIOR case's hypothesis text verbatim rather than regenerating its own,
    proving the existing_proposals plumbing actually reaches the synthesis step, not just the prompt."""
    chain, tool_calls, context = _genuine_error_case()
    sla_result = next((tc["result"] for tc in tool_calls if tc["tool"] == "check_sla_window"), None)
    if sla_result is None or sla_result.get("within_tolerance", True):
        return  # this fixture case doesn't hit the named branch -- nothing to cluster against
    first = propose_category_mock(chain, tool_calls, context, existing_proposals=None)
    assert first.proposed_name == "unexplained_settlement_delay"
    second = propose_category_mock(chain, tool_calls, context, existing_proposals=[first])
    assert second.hypothesis == first.hypothesis


def test_propose_category_groq_receives_prior_proposals_in_its_prompt():
    chain, tool_calls, context = _genuine_error_case()
    prior = [CategoryProposal(transaction_id="other", proposed_name="stale_fx_rate", hypothesis="a prior case's pattern", supporting_evidence=[], confidence=0.4, provider="groq")]
    fake_message = SimpleNamespace(
        content='{"proposed_name": "stale_fx_rate", "hypothesis": "reused the prior name.", "supporting_evidence": [], "confidence": 0.4}'
    )
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        proposal = propose_category_groq(chain, tool_calls, context, existing_proposals=prior)
        sent_messages = MockGroq.return_value.chat.completions.create.call_args.kwargs["messages"]

    assert any("stale_fx_rate" in m["content"] and "a prior case's pattern" in m["content"] for m in sent_messages)
    assert proposal.proposed_name == "stale_fx_rate"


def test_propose_category_dispatcher_threads_existing_proposals_through_to_mock():
    chain, tool_calls, context = _genuine_error_case()
    sla_result = next((tc["result"] for tc in tool_calls if tc["tool"] == "check_sla_window"), None)
    if sla_result is None or sla_result.get("within_tolerance", True):
        return
    first = propose_category(chain, tool_calls, context, provider="mock")
    second = propose_category(chain, tool_calls, context, provider="mock", existing_proposals=[first])
    assert second.hypothesis == first.hypothesis
