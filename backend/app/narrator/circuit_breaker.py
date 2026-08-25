"""Circuit breaker for narrator provider calls -- a reliability-engineering pattern (Netflix's
Hystrix, AWS SDK's own client-side throttling), not novel here, borrowed because the failure mode
it protects against is real and already observed in this project: a Groq rate-limit storm (8000 TPM
hit on request 4 of an 18-transaction run, see BUILD_LOG.md 2026-08-24) or a genuinely down local
Ollama service currently gets re-discovered from scratch on every single transaction, paying the
full retry-with-backoff cost (`_call_with_retry`: up to 4 retries with exponential delay for Groq)
before failing safe. For an 18-transaction narration queue against a provider already known to be
failing, that's minutes of wall-clock time spent re-learning the same fact eighteen times.

Tracks PROVIDER AVAILABILITY failures only -- a real API error/timeout surviving `_call_with_retry`
-- deliberately NOT model-reasoning failures (malformed JSON, an out-of-schema category, a bad tool
call). Those mean the provider answered, just unusably; that's not evidence the provider itself is
unhealthy, and tripping the breaker on it would stop calling a provider that's actually fine. See
agent.py's narrate_groq/narrate_ollama for where record_failure() is (and isn't) called.

Concurrency note, disclosed rather than engineered around: once the cooldown elapses, `before_call`
lets any caller through as a "half-open" trial without itself flipping a single-trial-in-flight
flag, so under genuine concurrent access (this project's own `/api/run` load tests exercise exactly
that: several batches, each with their own narration queue, can be in flight at once against the
same module-level breaker) more than one trial call can land in the same window. That's an accepted
simplification, not a data race -- no shared state can be corrupted by it, it just means the
"exactly one trial call" ideal a textbook implementation aims for isn't strictly enforced here.
"""

import threading
import time
from typing import Callable


class CircuitBreakerOpenError(Exception):
    """Raised by before_call() when the breaker is open and the cooldown hasn't elapsed yet --
    callers should fail safe immediately rather than attempt the call."""


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open = False
        self._opened_at: float | None = None

    def before_call(self) -> None:
        """Raises CircuitBreakerOpenError if the call should be skipped entirely. Once the cooldown
        has elapsed, lets the call through as a half-open trial -- state only actually closes again
        on a real record_success()."""
        with self._lock:
            if not self._open:
                return
            elapsed = self._time_fn() - self._opened_at
            if elapsed < self._cooldown_seconds:
                raise CircuitBreakerOpenError(
                    f"circuit open after {self._failure_threshold} consecutive provider failures; "
                    f"cooling down for {self._cooldown_seconds - elapsed:.0f}s more before trying again"
                )
            # cooldown elapsed -- fall through and let this call proceed as a half-open trial.

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open = False
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._open = True
                self._opened_at = self._time_fn()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures
