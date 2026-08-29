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


def _ask_ollama(model: str, description: str, cycle_ref: str) -> str:
    from ollama import Client

    client = Client(timeout=120.0)
    prompt = f"Settlement cycle: {cycle_ref}\nBank description: {description}"
    return (
        client.chat(
            model=model,
            messages=[{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            options={"temperature": 0.0},
        ).message.content
        or ""
    )


def _strip(raw: str) -> str:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def model_cycle_agrees(cycle_ref: str, description: str, model: str | None = None) -> bool | None:
    """True / False / None, matching `_cycle_agrees`'s contract exactly.

    A failed call or an unparseable answer returns None -- "not stated" -- rather than a guess. That
    is the safe direction here: None leaves the candidate scored on its other evidence, whereas a
    fabricated True or False would move a match on a reading that never happened.
    """
    if not cycle_ref:
        return None
    model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    try:
        parsed = json.loads(_strip(_ask_ollama(model, description, cycle_ref)))
    except Exception:  # noqa: BLE001 -- provider failure or bad JSON both mean "no reading available"
        return None
    verdict = str(parsed.get("verdict", "")).lower().strip()
    if verdict == "same":
        return True
    if verdict == "different":
        return False
    return None
