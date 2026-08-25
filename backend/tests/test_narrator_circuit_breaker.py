"""Integration tests proving the circuit breaker (app/narrator/circuit_breaker.py) is actually
wired into narrate_groq/narrate_ollama correctly, not just correct in isolation (see
test_circuit_breaker.py for the class's own unit tests).

Two properties matter here, and neither is obvious from the unit tests alone: (1) real provider
failures (surviving _call_with_retry's own backoff) must trip the breaker, and once open, the very
next call must skip the provider entirely -- no client call attempted -- rather than merely
returning the same fail-safe answer it would have reached anyway; (2) a model-reasoning failure
(malformed JSON, an out-of-schema category) must NOT count as a breaker failure, since the provider
answered fine, just unusably -- conflating the two would stop calling a provider that isn't actually
unhealthy.

Every test here constructs its own fresh CircuitBreaker and passes it explicitly via the `breaker=`
parameter, rather than touching the module-level `_groq_breaker`/`_ollama_breaker` singletons --
those are process-wide and shared across the whole test session, so mutating them here would leak
state into unrelated tests running later in the same pytest process.
"""

from unittest.mock import patch

from app.narrator.agent import narrate_groq, narrate_ollama
from app.narrator.circuit_breaker import CircuitBreaker
from tests.test_retry import _fake_rate_limit_error


def _narration_queue(seed=42, main_n=150, stress_n=0):
    from app.chain.builder import build_all_chains
    from app.data_gen.generate import generate
    from app.matching.engine import run_matching_engine
    from app.narrator.tools import build_tool_context

    main, _ = generate(seed=seed, main_n=main_n, stress_n=stress_n)
    chains = build_all_chains(main)
    results = run_matching_engine(chains)
    context = build_tool_context(main, chains)
    queue = [txn_id for txn_id, r in results.items() if r.resolution == "needs_narration"]
    return chains, context, queue


def test_repeated_groq_provider_failures_trip_the_breaker_and_the_next_call_skips_the_provider():
    _, context, queue = _narration_queue()
    chain = context.chains[queue[0]]
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=9999.0)

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}), patch("time.sleep"):
        MockGroq.return_value.chat.completions.create.side_effect = _fake_rate_limit_error()

        output1 = narrate_groq(chain, context, breaker=breaker)
        assert output1.category == "genuine_error"
        assert not breaker.is_open, "one real failure shouldn't trip a threshold of 2"

        output2 = narrate_groq(chain, context, breaker=breaker)
        assert breaker.is_open, "the second consecutive real failure should trip the breaker"

        calls_before = MockGroq.return_value.chat.completions.create.call_count
        output3 = narrate_groq(chain, context, breaker=breaker)
        calls_after = MockGroq.return_value.chat.completions.create.call_count

        assert calls_after == calls_before, "an open breaker must skip the provider call entirely, not just reach the same fail-safe answer"
        assert output3.category == "genuine_error"
        assert output3.confidence == 0.0
        assert "circuit open" in output3.reasoning


def test_malformed_groq_answers_never_trip_the_breaker():
    """The provider is responding fine here -- just with an unusable answer. That's a model-
    reasoning problem, not evidence Groq itself is down, and must not count toward the breaker."""
    _, context, queue = _narration_queue()
    chain = context.chains[queue[0]]
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=9999.0)

    from types import SimpleNamespace

    fake_message = SimpleNamespace(tool_calls=None, content='{"category": "not_a_real_category", "confidence": 0.5, "reasoning": "x"}')
    fake_response = SimpleNamespace(choices=[SimpleNamespace(message=fake_message)])

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}):
        MockGroq.return_value.chat.completions.create.return_value = fake_response
        for _ in range(5):  # well past the failure_threshold=2, if this counted at all
            output = narrate_groq(chain, context, breaker=breaker)
            assert output.category == "genuine_error"  # fails safe, correctly

    assert not breaker.is_open, "a malformed/out-of-schema answer is not a provider-availability failure"
    assert breaker.consecutive_failures == 0


def test_a_real_success_resets_the_breaker_after_a_prior_failure():
    _, context, queue = _narration_queue()
    chain = context.chains[queue[0]]
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=9999.0)

    from types import SimpleNamespace

    with patch("groq.Groq") as MockGroq, patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}), patch("time.sleep"):
        MockGroq.return_value.chat.completions.create.side_effect = _fake_rate_limit_error()
        narrate_groq(chain, context, breaker=breaker)
        assert breaker.consecutive_failures == 1

        good_message = SimpleNamespace(tool_calls=None, content='{"category": "genuine_error", "confidence": 0.4, "reasoning": "ok"}')
        good_response = SimpleNamespace(choices=[SimpleNamespace(message=good_message)])
        MockGroq.return_value.chat.completions.create.side_effect = None
        MockGroq.return_value.chat.completions.create.return_value = good_response
        output = narrate_groq(chain, context, breaker=breaker)

    assert output.provider == "groq"
    assert output.confidence == 0.4  # the real, non-fail-safe answer, not a 0.0 escalation
    assert breaker.consecutive_failures == 0
    assert not breaker.is_open


def test_repeated_ollama_provider_failures_trip_the_breaker_and_the_next_call_skips_the_provider():
    import httpx
    from ollama import RequestError

    _, context, queue = _narration_queue()
    chain = context.chains[queue[0]]
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=9999.0)

    with patch("ollama.Client") as MockClient, patch("time.sleep"):
        MockClient.return_value.chat.side_effect = RequestError("connection refused")

        narrate_ollama(chain, context, breaker=breaker)
        assert not breaker.is_open

        narrate_ollama(chain, context, breaker=breaker)
        assert breaker.is_open

        calls_before = MockClient.return_value.chat.call_count
        output3 = narrate_ollama(chain, context, breaker=breaker)
        calls_after = MockClient.return_value.chat.call_count

        assert calls_after == calls_before, "an open breaker must skip the provider call entirely"
        assert output3.category == "genuine_error"
        assert "circuit open" in output3.reasoning


def test_malformed_ollama_answers_never_trip_the_breaker():
    from types import SimpleNamespace

    _, context, queue = _narration_queue()
    chain = context.chains[queue[0]]
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=9999.0)

    fake_message = SimpleNamespace(tool_calls=None, content="not even json")
    fake_response = SimpleNamespace(message=fake_message)

    with patch("ollama.Client") as MockClient:
        MockClient.return_value.chat.return_value = fake_response
        for _ in range(5):
            output = narrate_ollama(chain, context, breaker=breaker)
            assert output.category == "genuine_error"

    assert not breaker.is_open
    assert breaker.consecutive_failures == 0
