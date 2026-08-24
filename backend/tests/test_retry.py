"""Tests for the retry-with-backoff wrapper, originally Groq-only, generalized 2026-08-24 so
narrate_ollama can reuse it for its own transient errors instead of duplicating the backoff loop.

The Groq-specific tests exist because a real batch run against the live Groq API hit a real rate
limit mid-batch (8000 TPM on openai/gpt-oss-20b, request 4 of 18 — see BUILD_LOG.md 2026-08-24).
These tests use a fake RateLimitError rather than hitting the real API, so they're fast, free, and
deterministic — the point is to verify the retry/backoff mechanics, not to re-trigger the real
rate limit. The generalization tests below use a plain custom exception, deliberately NOT one of
Groq's types, to prove the parameterization is real and not just Groq with extra steps.
"""

from unittest.mock import patch

import httpx
import pytest
from groq import APIConnectionError, RateLimitError

from app.narrator.agent import _call_with_retry


class _FakeLocalModelError(Exception):
    """Stands in for ollama.ResponseError / httpx.ConnectError -- deliberately not a Groq
    exception, so these tests fail loudly if _call_with_retry's `retry_on` parameterization is
    broken (e.g. reverted to hardcoding Groq's exception tuple, as it originally did)."""


def _fake_rate_limit_error(retry_after: str | None = None) -> RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, request=request, headers=headers, json={"error": {"message": "rate limited"}})
    return RateLimitError("rate limited", response=response, body=None)


def test_retries_and_eventually_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _fake_rate_limit_error()
        return "success"

    with patch("time.sleep") as mock_sleep:
        result = _call_with_retry(flaky, max_retries=4, base_delay=1.0)

    assert result == "success"
    assert calls["n"] == 3
    assert mock_sleep.call_count == 2  # slept before retry 2 and retry 3, not after final success


def test_honors_retry_after_header():
    def always_fails():
        raise _fake_rate_limit_error(retry_after="7")

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(RateLimitError):
            _call_with_retry(always_fails, max_retries=1, base_delay=1.0)

    mock_sleep.assert_called_once_with(7.0)


def test_falls_back_to_exponential_backoff_without_header():
    def always_fails():
        raise _fake_rate_limit_error()  # no retry-after header

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(RateLimitError):
            _call_with_retry(always_fails, max_retries=2, base_delay=2.0)

    assert [c.args[0] for c in mock_sleep.call_args_list] == [2.0, 4.0]


def test_reraises_after_max_retries_exhausted():
    def always_fails():
        raise _fake_rate_limit_error()

    with patch("time.sleep"):
        with pytest.raises(RateLimitError):
            _call_with_retry(always_fails, max_retries=2, base_delay=0.01)


def test_retries_connection_errors_too():
    calls = {"n": 0}
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise APIConnectionError(request=request)
        return "ok"

    with patch("time.sleep"):
        result = _call_with_retry(flaky, max_retries=2, base_delay=0.01)
    assert result == "ok"


def test_default_retry_on_does_not_catch_non_groq_exceptions():
    """Without an explicit retry_on, a non-Groq exception must propagate immediately, not be
    silently swallowed by an overly broad except clause. This is the exact property that would be
    violated if _call_with_retry's Groq-specific exceptions were ever loosened to `Exception`."""

    def raises_something_else():
        raise _FakeLocalModelError("connection refused")

    with pytest.raises(_FakeLocalModelError):
        _call_with_retry(raises_something_else, max_retries=2, base_delay=0.01)


def test_custom_retry_on_catches_and_retries_arbitrary_exceptions():
    """Proves the generalization is real: an explicit retry_on tuple with a non-Groq exception
    type gets retried exactly like a Groq RateLimitError does, via the same code path
    narrate_ollama uses (ollama_retry_exceptions passed as retry_on)."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeLocalModelError("model still loading")
        return "local model ready"

    with patch("time.sleep") as mock_sleep:
        result = _call_with_retry(flaky, max_retries=4, base_delay=1.0, retry_on=(_FakeLocalModelError,))

    assert result == "local model ready"
    assert calls["n"] == 3
    assert mock_sleep.call_count == 2


def test_custom_retry_on_ignores_retry_after_when_exception_has_no_response():
    """A plain custom exception has no `.response` attribute (unlike Groq's RateLimitError) --
    must fall back to exponential backoff cleanly rather than crashing on a missing attribute."""

    def always_fails():
        raise _FakeLocalModelError("still down")

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(_FakeLocalModelError):
            _call_with_retry(always_fails, max_retries=2, base_delay=1.5, retry_on=(_FakeLocalModelError,))

    assert [c.args[0] for c in mock_sleep.call_args_list] == [1.5, 3.0]
