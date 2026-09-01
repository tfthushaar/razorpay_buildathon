"""The rate limiter that replaced a retry loop.

Every Groq caller in this project fired as fast as it could, took a 429, and backed off
4-8-16-32-64 seconds. That is a collision detector, not a rate limiter, and it cost two days of
quota: a run saturates the ceiling, spends its budget on retries, and stops without finishing a
condition. Backoff decides what to do after the limit is hit; pacing decides not to hit it.

The numbers it paces against are measured rather than assumed -- 8,000 tokens/minute from Groq's own
`x-ratelimit-limit-tokens` header, ~500 tokens per call from its `usage` block -- so
`test_the_constants_match_what_groq_reports` is the one that would catch them drifting.
"""

import time

import pytest

from app.narrator.pacing import DEFAULT_TOKENS_PER_CALL, DEFAULT_TOKENS_PER_MINUTE, SAFETY_FACTOR, Pacer


def test_it_paces_below_the_token_ceiling():
    """The whole point. Calls per minute times tokens per call must stay under the limit."""
    pacer = Pacer()
    calls_per_minute = 60.0 / pacer.min_interval
    assert calls_per_minute * DEFAULT_TOKENS_PER_CALL < DEFAULT_TOKENS_PER_MINUTE


def test_it_leaves_a_margin_rather_than_pacing_at_the_ceiling():
    """The ceiling is shared with anything else using the same key, and the per-call token count is
    an estimate. Pacing at 100% of a limit you cannot measure is not pacing."""
    pacer = Pacer()
    used = (60.0 / pacer.min_interval) * DEFAULT_TOKENS_PER_CALL
    assert used <= DEFAULT_TOKENS_PER_MINUTE * SAFETY_FACTOR + 1


def test_the_first_call_is_not_delayed():
    """A limiter that sleeps before the first call wastes an interval on every short run."""
    pacer = Pacer()
    assert pacer.wait() == 0.0


def test_consecutive_calls_are_spaced():
    pacer = Pacer(tokens_per_minute=6000, tokens_per_call=1000)  # 5.1 calls/min -> ~11.8s
    pacer.wait()
    start = time.monotonic()
    pacer.wait()
    assert time.monotonic() - start > 1.0


def test_a_call_after_a_long_gap_is_not_delayed():
    """If the caller was slow anyway, the limiter must not add to it."""
    pacer = Pacer()
    pacer.wait()
    pacer._last_call = time.monotonic() - 3600
    assert pacer.wait() == 0.0


def test_it_reports_what_it_did():
    pacer = Pacer()
    pacer.wait()
    pacer.wait()
    summary = pacer.summary()
    assert summary["calls"] == 2
    assert summary["slept_seconds"] >= 0
    assert summary["effective_calls_per_minute"] > 0


def test_the_estimate_is_usable_for_deciding_whether_a_run_fits():
    """A run that cannot finish inside the daily budget should be knowable before it starts, which
    is the failure this project hit twice."""
    pacer = Pacer()
    assert pacer.estimate_seconds(300) == pytest.approx(300 * pacer.min_interval)


def test_the_constants_match_what_groq_reports():
    """Measured from response headers and the usage block, not guessed. If Groq changes either, the
    pacer is wrong and this is where it shows."""
    assert DEFAULT_TOKENS_PER_MINUTE == 8_000
    assert 450 <= DEFAULT_TOKENS_PER_CALL <= 600
    assert 0.5 < SAFETY_FACTOR < 1.0
