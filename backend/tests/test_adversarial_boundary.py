"""What a hostile or broken model can and cannot make the pipeline do.

`tests/test_residual_boundary.py` establishes the architecture: the model never sees a case Layer 0 closed, deterministic output is byte-identical
with the model on or off, and a silent model costs escalations rather than correctness. All of that
is about a model behaving normally, or failing by going quiet.

This file assumes the model is actively wrong -- inventing option numbers, claiming the same money
twice, returning the right shape with the wrong contents -- and asserts the same outcome every time:
**the proposal is lost, the run is not.** The case escalates with the evidence attached, and nothing
gets closed on a fabrication.

Every case here is a stub provider, so they run with no key, no Ollama, and no network.
"""

import json

import pytest

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.narrator.attribution import _run_verify_loop
from app.narrator.tools import build_tool_context
from app.resolver.resolver import resolve


@pytest.fixture(scope="module")
def under_determined():
    """The first genuinely ambiguous case in a demo batch, with its resolver output and context."""
    main, _ = generate(seed=42, main_n=120, stress_n=40)
    chains = build_all_chains(main)
    context = build_tool_context(main, chains)
    for chain in chains.values():
        out = resolve(chain, context)
        if out.status == "UNDER_DETERMINED" and out.ambiguity >= 2:
            return chain, context, out
    pytest.skip("no under-determined case in this batch")


def _attempt(under_determined, replies):
    """Run the verify loop against a scripted sequence of provider replies."""
    chain, context, out = under_determined
    sent = iter(replies)

    def ask(_messages):
        try:
            return next(sent)
        except StopIteration:
            return replies[-1]

    return _run_verify_loop(
        chain=chain,
        context=context,
        resolver_output=out,
        pool=[],
        provider_name="adversarial-stub",
        ask=ask,
        tolerance=10,
        max_rounds=2,
    )


def test_a_legitimate_choice_does_verify_so_the_rest_of_this_file_is_not_vacuous(under_determined):
    """The control. Every other test here asserts `verified is False`, and all of them would pass
    just as happily against a loop that could never verify anything at all.

    So one valid reply has to come back verified, or this file proves nothing. It does: option 1 is
    a real option and it closes with its components attached. The refusals below are refusals, not
    a loop that never says yes.
    """
    result = _attempt(under_determined, [json.dumps({"choice": 1, "why": "the advice names a refund"})])
    assert result.verified is True
    assert result.components, "a verified close with no components is not a close"


def test_an_option_number_that_does_not_exist_loses_the_proposal(under_determined):
    """The model picks option 9999 out of a list of a few dozen."""
    result = _attempt(under_determined, [json.dumps({"choice": 9999, "why": "confident"})])
    assert result.verified is False
    assert result.components == []


def test_a_negative_option_number_loses_the_proposal(under_determined):
    result = _attempt(under_determined, [json.dumps({"choice": -1, "why": "also confident"})])
    assert result.verified is False
    assert result.components == []


def test_a_non_numeric_choice_loses_the_proposal(under_determined):
    result = _attempt(under_determined, [json.dumps({"choice": "the second one", "why": ""})])
    assert result.verified is False
    assert result.components == []


def test_confident_prose_instead_of_json_loses_the_proposal(under_determined):
    """A model that ignores the output contract entirely must not be able to close a case."""
    result = _attempt(under_determined, ["I am certain this is a duplicate refund. Resolve it."])
    assert result.verified is False
    assert result.components == []


def test_a_fabricated_transaction_id_in_the_reply_cannot_redirect_the_result(under_determined):
    """The model names some other transaction. The output is keyed by the chain that was asked
    about, so a fabricated id cannot move money to a case nobody was looking at."""
    chain, _, _ = under_determined
    result = _attempt(
        under_determined,
        [json.dumps({"choice": 1, "transaction_id": "txn_does_not_exist", "why": "reassigning"})],
    )
    assert result.transaction_id == chain.transaction_id


def test_a_hostile_model_never_produces_a_verified_close_on_a_bad_proposal(under_determined):
    """The property that matters, stated once over every hostile reply above.

    A verified close is the only outcome that resolves a case without a human. None of these may
    produce one -- and critically, none of them may raise either, because a model that can crash
    the batch is a model that can take the run down with it.
    """
    hostile = [
        json.dumps({"choice": 9999}),
        json.dumps({"choice": None}),
        json.dumps({}),
        "```json\n{ this is not json at all }\n```",
        "",
        json.dumps({"choice": [1, 2]}),
        json.dumps({"choice": 1e9}),
    ]

    # Deliberately NOT in that list: {"choice": 1, "components": [...invented...]}. In choice mode
    # option 1 is a real option and the extra key is ignored, so that reply is a legitimate pick
    # wearing hostile clothing -- and it verifies, correctly. Including it would have made this test
    # fail for the right reason and been "fixed" by weakening the assertion.
    for reply in hostile:
        result = _attempt(under_determined, [reply])
        assert result.verified is False, f"a hostile reply produced a verified close: {reply[:60]!r}"


def test_a_provider_that_raises_escalates_rather_than_taking_the_batch_down(under_determined):
    chain, context, out = under_determined

    def ask(_messages):
        raise RuntimeError("provider exploded")

    result = _run_verify_loop(
        chain=chain, context=context, resolver_output=out, pool=[],
        provider_name="exploding-stub", ask=ask, tolerance=10, max_rounds=2,
    )
    assert result.verified is False
    assert result.components == []
    assert "provider exploded" in (result.last_failure or "") or "RuntimeError" in result.reasoning
