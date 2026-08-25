"""Tests for the narrator's circuit breaker (app/narrator/circuit_breaker.py).

Uses an injectable clock (time_fn) rather than real time.sleep()/time.monotonic() so state
transitions across the cooldown window are deterministic and instant to test, not flaky or slow.
"""

import threading

import pytest

from app.narrator.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_closed_by_default_and_allows_calls():
    breaker = CircuitBreaker()
    breaker.before_call()  # should not raise
    assert not breaker.is_open


def test_opens_after_reaching_the_failure_threshold_not_before():
    breaker = CircuitBreaker(failure_threshold=3)
    breaker.record_failure()
    assert not breaker.is_open
    breaker.record_failure()
    assert not breaker.is_open
    breaker.record_failure()
    assert breaker.is_open


def test_open_breaker_blocks_calls_until_cooldown_elapses():
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0, time_fn=clock)
    breaker.record_failure()
    assert breaker.is_open

    with pytest.raises(CircuitBreakerOpenError):
        breaker.before_call()

    clock.advance(29.9)
    with pytest.raises(CircuitBreakerOpenError):
        breaker.before_call()

    clock.advance(0.2)  # now past the 30s cooldown
    breaker.before_call()  # half-open trial allowed through, does not raise


def test_success_resets_failure_count_and_closes_the_breaker():
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10.0, time_fn=clock)
    breaker.record_failure()
    breaker.record_success()
    assert not breaker.is_open
    assert breaker.consecutive_failures == 0
    # one more failure alone should not reopen it -- the count was genuinely reset, not just masked
    breaker.record_failure()
    assert not breaker.is_open


def test_a_failed_half_open_trial_reopens_with_a_fresh_cooldown():
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0, time_fn=clock)
    breaker.record_failure()
    assert breaker.is_open

    clock.advance(10.1)
    breaker.before_call()  # half-open trial let through
    breaker.record_failure()  # the trial itself failed
    assert breaker.is_open

    # cooldown should have restarted from the trial's failure, not the original one
    clock.advance(9.9)
    with pytest.raises(CircuitBreakerOpenError):
        breaker.before_call()
    clock.advance(0.2)
    breaker.before_call()  # doesn't raise -- fresh cooldown has now elapsed


def test_thread_safety_of_concurrent_record_failure_calls():
    """Not a correctness proof of the disclosed half-open race (see the module docstring), but
    proves the simpler, load-bearing property: concurrent record_failure() calls must never lose
    an increment or leave the counter in a torn state -- exactly the class of bug this project's
    own BUILD_LOG has found repeatedly elsewhere (unlocked compound state under concurrency)."""
    breaker = CircuitBreaker(failure_threshold=10_000)  # high enough it never opens mid-test
    n_threads = 20
    increments_per_thread = 200

    def hammer():
        for _ in range(increments_per_thread):
            breaker.record_failure()

    threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert breaker.consecutive_failures == n_threads * increments_per_thread
