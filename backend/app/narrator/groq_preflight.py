"""Refuse to start a Groq column that the daily budget cannot finish.

This exists because of a misdiagnosis worth not repeating. Groq's free tier caps tokens per day
(200,000) separately from tokens per minute (8,000), and only the per-minute limits appear in response
headers. There is no TPD field to read. So the obvious readiness check is to make a call and see if it
succeeds -- and that check is wrong.

A `max_tokens: 5` probe costs about 30 tokens. With 90 tokens left under the daily cap it returns 200
while a real 450-token request returns 429. I ran exactly that probe, read the 200, concluded the
quota had reset, and started a run that had roughly one call of headroom. Every subsequent call
would have retried five times, given up, and been recorded as an unreadable advice line -- which is
how this project previously produced a column of 300 silent failures that scored byte-identical to
its baseline.

So the preflight sends a request the size of a real one and reads the error text, which is the only
place the daily numbers appear. It is deliberately noisy on failure: a run that cannot finish should
stop at the start, loudly, rather than fill an evidence file with fabricated verdicts.
"""

import os
import re

from dotenv import load_dotenv

# A realistic call for this project's Groq columns: system prompt plus one advice line plus a short
# JSON verdict. Probing with anything smaller measures nothing useful about whether a run can run.
TYPICAL_TOKENS_PER_CALL = 450


class GroqBudgetError(RuntimeError):
    """Raised when the daily token budget cannot cover the requested run."""


def _daily_usage_from_error(text: str) -> tuple[int, int] | None:
    """Groq reports daily usage only inside the 429 body, e.g. 'Limit 200000, Used 199966'."""
    limit = re.search(r"Limit (\d+)", text)
    used = re.search(r"Used (\d+)", text)
    if limit and used:
        return int(used.group(1)), int(limit.group(1))
    return None


def check_groq_budget(estimated_calls: int, model: str = "openai/gpt-oss-20b") -> dict:
    """Send one realistically-sized request. Raise if the daily budget cannot cover the run.

    Returns a dict describing the headroom when the run can proceed.
    """
    import httpx

    load_dotenv()
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise GroqBudgetError("GROQ_API_KEY is not set")

    needed = estimated_calls * TYPICAL_TOKENS_PER_CALL
    # padding to a realistic prompt size, so the probe costs what a real call costs
    filler = "settlement remittance advice line for preflight sizing. " * 40

    response = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": f"Reply with the single word OK. Context: {filler}"}],
            "max_tokens": 16,
            "temperature": 0.0,
        },
        timeout=60,
    )

    if response.status_code == 429:
        usage = _daily_usage_from_error(response.text)
        if usage:
            used, limit = usage
            raise GroqBudgetError(
                f"Groq daily token budget exhausted: {used:,} of {limit:,} used. "
                f"This run needs roughly {needed:,} tokens across {estimated_calls:,} calls. "
                f"Wait for the daily counter to roll over. Do not run a partial column."
            )
        raise GroqBudgetError(f"Groq rate-limited the preflight and reported no daily usage: {response.text[:300]}")

    if response.status_code != 200:
        raise GroqBudgetError(f"Groq preflight failed with {response.status_code}: {response.text[:300]}")

    return {
        "ok": True,
        "estimated_calls": estimated_calls,
        "estimated_tokens": needed,
        "tokens_per_minute_remaining": int(response.headers.get("x-ratelimit-remaining-tokens", 0)),
        "requests_remaining": int(response.headers.get("x-ratelimit-remaining-requests", 0)),
        "note": "Per-minute headroom only. Groq does not expose a daily-remaining header, so a run can still exhaust the daily cap partway through; the scripts fail loudly rather than record unreadable results.",
    }
