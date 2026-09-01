"""A rate limiter that respects the ceiling instead of discovering it.

Groq's free tier allows roughly 7,500 tokens per minute. Every Groq caller in this project fires as
fast as it can, takes a 429, and backs off 4-8-16-32-64 seconds. That is not a rate limiter, it is a
collision detector, and it has cost this project two days of quota: a run saturates the ceiling,
spends its budget on retries, and stops without finishing a single condition.

Pacing is the obvious fix and it went unwritten because backoff LOOKS like it handles the problem.
It does not. Backoff decides what to do after the limit is hit; this decides not to hit it.

    ~500 tokens per call against an 8,000/minute ceiling is 16 calls a minute at the ceiling, so
    with a safety margin a call may start roughly every 4.4 seconds.

The limiter is deliberately dumb: one process, one lock, a wall-clock gap between calls. No token
accounting, because the token count per call is an estimate and a limiter that trusts its own
estimate of a number it cannot see is the thing that got us here.

WHY THIS IS NOT SILENTLY APPLIED TO EVERY EXISTING RESULT. Pacing changes which calls come back
unparseable, and the committed reading column reports 7 unparseable judgements out of 420. Applying
this to that script without re-running it would leave a published number describing a client that no
longer exists. Callers opt in.
"""

from __future__ import annotations

import threading
import time

# Both measured from Groq's own response headers and usage block rather than assumed:
# x-ratelimit-limit-tokens reports 8,000, and a real cycle-reading call costs 366 prompt + ~103-129
# completion tokens. 500 is that rounded up, because pacing against an underestimate is how you
# discover the ceiling rather than respect it.
DEFAULT_TOKENS_PER_MINUTE = 8_000
DEFAULT_TOKENS_PER_CALL = 500

# A margin, because the ceiling is shared with anything else using the same key and the token
# estimate per call is exactly that. Pacing at 100% of a limit you cannot measure is not pacing.
SAFETY_FACTOR = 0.85


class Pacer:
    """Blocks until enough time has passed since the previous call."""

    def __init__(
        self,
        tokens_per_minute: int = DEFAULT_TOKENS_PER_MINUTE,
        tokens_per_call: int = DEFAULT_TOKENS_PER_CALL,
        safety_factor: float = SAFETY_FACTOR,
    ):
        calls_per_minute = (tokens_per_minute * safety_factor) / tokens_per_call
        self.min_interval = 60.0 / calls_per_minute if calls_per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.calls = 0
        self.slept_seconds = 0.0

    def wait(self) -> float:
        """Block until the next call is allowed. Returns how long it waited, for reporting."""
        with self._lock:
            now = time.monotonic()
            gap = now - self._last_call
            delay = max(0.0, self.min_interval - gap) if self._last_call else 0.0
            if delay:
                time.sleep(delay)
            self._last_call = time.monotonic()
            self.calls += 1
            self.slept_seconds += delay
            return delay

    def estimate_seconds(self, n_calls: int) -> float:
        return n_calls * self.min_interval

    def summary(self) -> dict:
        return {
            "calls": self.calls,
            "min_interval_seconds": round(self.min_interval, 2),
            "slept_seconds": round(self.slept_seconds, 1),
            "effective_calls_per_minute": round(60.0 / self.min_interval, 1) if self.min_interval else None,
        }
