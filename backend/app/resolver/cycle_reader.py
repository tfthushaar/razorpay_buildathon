"""Reading the settlement cycle out of a bank description, with a model instead of a regex.

Drop-in replacement for `entity_resolution._cycle_agrees`, so the two can be compared with everything
else in the matcher held identical -- same filters, same scoring weights, same tie-breaking. The only
difference between the two columns is whether a regex or a model decided "does this description state
this settlement's cycle?", which makes any gap attributable to reading and to nothing else.

That control matters here specifically. The regex version scores 98.7% on the phrasing it was written
against and buys *exactly nothing* on phrasing it wasn't (88.0% either way). If a model column moves
that number, the movement is the value of reading; if it doesn't, that is equally worth publishing.
"""

import json
import os

from dotenv import load_dotenv

_SYSTEM_PROMPT = """You read bank statement descriptions for settlement reconciliation.

A payment gateway pays a merchant in numbered settlement cycles: a date plus a slot letter (A, B, C \
or D). Two payouts to the same merchant, for the same amount, on the same day are distinguished ONLY \
by which cycle they belong to.

Banks record the cycle however they like, or not at all: "CYCLE C2026-03-13-D", "batch \
c2026-03-13-d", "ref.cyc D of 2026-03-13", "SETTLEMENT RUN D DTD 13.03.2026", "window D on \
2026-03-13", "processed in slot d of 2026-03-13", or nothing whatsoever.

You are given one settlement's cycle and one bank description. Answer whether the description states \
that same cycle.

Respond with ONLY a JSON object:
{"verdict": "same" | "different" | "not_stated", "why": "<very short>"}

Use "not_stated" when the description carries no cycle information at all. Do not guess a cycle from \
the value date alone -- several cycles share a date, and the slot letter is what distinguishes them."""


def _prompt(description: str, cycle_ref: str) -> str:
    return f"Settlement cycle: {cycle_ref}\nBank description: {description}"


def _ask_ollama(model: str, description: str, cycle_ref: str) -> str:
    from ollama import Client

    client = Client(timeout=120.0)
    return (
        client.chat(
            model=model,
            messages=[{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": _prompt(description, cycle_ref)}],
            options={"temperature": 0.0},
        ).message.content
        or ""
    )


# One pacer per process, shared by every call, because the rate limit is per key rather than per
# call site. Created lazily so importing this module costs nothing.
_PACER = None


def _pacer():
    global _PACER
    if _PACER is None:
        from app.narrator.pacing import Pacer

        _PACER = Pacer()
    return _PACER


def _ask_groq(model: str, description: str, cycle_ref: str, max_retries: int = 5) -> str:
    """Groq's free tier rate-limits by tokens-per-minute, and a sweep of a few hundred calls hits it
    reliably.

    Paced rather than only backed off. Backoff decides what to do after the ceiling is hit; the
    pacer decides not to hit it, at roughly 14 calls a minute against a 7,500 token ceiling. Two
    days of quota were spent discovering that a retry loop is a collision detector and not a rate
    limiter. The backoff stays as the fallback for a limit the pacer did not anticipate, and a
    rate-limit failure still waits rather than being recorded as an unreadable description --
    this project has already once mistaken a 429 for a capability finding.
    """
    import time

    from groq import Groq

    load_dotenv()
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is not set — refusing to run a Groq column that would silently measure nothing")

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    delay = 4.0
    last: Exception | None = None
    for _ in range(max_retries):
        _pacer().wait()
        try:
            return (
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": _prompt(description, cycle_ref)}],
                    temperature=0.0,
                )
                .choices[0]
                .message.content
                or ""
            )
        except Exception as e:  # noqa: BLE001 -- rate limits surface as several exception types
            last = e
            if "rate" not in str(e).lower() and "429" not in str(e):
                raise
            time.sleep(delay)
            delay *= 2
    raise last if last else RuntimeError("groq call failed")


def _strip(raw: str) -> str:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def model_cycle_agrees(cycle_ref: str, description: str, model: str | None = None, provider: str = "ollama") -> bool | None:
    """True / False / None, matching `_cycle_agrees`'s contract exactly.

    A failed call or an unparseable answer returns None -- "not stated" -- rather than a guess. That
    is the safe direction here: None leaves the candidate scored on its other evidence, whereas a
    fabricated True or False would move a match on a reading that never happened.
    """
    if not cycle_ref:
        return None
    if provider == "groq":
        model = model or "openai/gpt-oss-20b"
        ask = _ask_groq
    else:
        model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        ask = _ask_ollama
    try:
        parsed = json.loads(_strip(ask(model, description, cycle_ref)))
    except Exception:  # noqa: BLE001 -- provider failure or bad JSON both mean "no reading available"
        return None
    verdict = str(parsed.get("verdict", "")).lower().strip()
    if verdict == "same":
        return True
    if verdict == "different":
        return False
    return None
