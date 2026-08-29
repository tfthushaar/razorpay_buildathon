"""Isolate the reading step: does a model read settlement advice better than a regex does?

Produces docs/evidence/advice-reading-<date>.json.

This is the sharpest question in the whole residual architecture, so it is measured on its own rather
than inferred from end-to-end accuracy. The keyword baseline is two separable stages -- read the
advice into assertions, then score every valid decomposition against them. Stage two is bookkeeping.
Stage one is reading comprehension over messy, negated, tense-shifted bank text. Only stage one is
compared here, against ground truth recorded by the generator itself (`advice_mentions`), so a result
cannot be contaminated by anything downstream.

Both conditions matter, and the second is the honest one:

  SEEN       phrasing from the bank the keyword rule's negation cues were written against. The rule
             author (me) had full sight of this text. A rule scoring well here is measuring
             authorship as much as reading.

  HELD-OUT   phrasing the cue list has never seen -- abeyance, rescinded, held over, zero-rated,
             struck off, stood down, lapsed, contra. Cause-identifying vocabulary is deliberately
             unchanged (TDS, RSV, GST, MDR still appear), so the rule cannot fail merely by not
             knowing a synonym. Only the expression of applied-vs-not-applied is new.

The gap between a system's SEEN and HELD-OUT score is the thing worth reporting: it is a direct
measurement of how much of an apparent advantage was generalisation and how much was authorship.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains  # noqa: E402
from app.data_gen.generate import SyntheticDataGenerator  # noqa: E402
from app.data_gen.schemas import SyntheticBatch  # noqa: E402
from app.narrator.attribution import _READER_CAUSES, READER_SYSTEM_PROMPT, _strip_fences  # noqa: E402
from app.resolver.keyword_baseline import read_advice  # noqa: E402

CAUSES = list(_READER_CAUSES)


def build(seed: int, n: int, held_out: bool):
    g = SyntheticDataGenerator(seed=seed)
    parts = [g._gen_compound_delta(held_out_phrasing=held_out) for _ in range(n)]
    o, p, r, s, l, gt = [], [], [], [], [], []
    for a, b, c, d, e, f in parts:
        o += a
        p += b
        r += c
        s += d
        l += e
        gt += f
    batch = SyntheticBatch(orders=o, payments=p, refunds=r, settlements=s, ledger_entries=l, ground_truth=gt)
    return build_all_chains(batch), {e.transaction_id: e for e in gt}


def rule_read(narration: str | None) -> dict[str, str]:
    r = read_advice(narration)
    return {c: ("applied" if r[c] else "not_applied") if c in r else "not_mentioned" for c in CAUSES}


def model_read(client, model: str, narration: str | None) -> dict[str, str]:
    prompt = f"Remittance advice:\n  {narration or '(none provided)'}\n\nCharge types: {', '.join(CAUSES)}"
    raw = client.chat(
        model=model,
        messages=[{"role": "system", "content": READER_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        options={"temperature": 0.0},
    ).message.content
    try:
        parsed = json.loads(_strip_fences(raw or ""))
    except (json.JSONDecodeError, TypeError):
        return {c: "PARSE_FAIL" for c in CAUSES}
    return {c: str(parsed.get(c, "not_mentioned")).lower().replace(" ", "_") for c in CAUSES}


def score(chains, truth, readers: dict) -> dict:
    hits = {name: Counter() for name in readers}
    confusion = {name: Counter() for name in readers}
    for tid, chain in chains.items():
        want = {c: truth[tid].advice_mentions.get(c, "not_mentioned") for c in CAUSES}
        for name, fn in readers.items():
            got = fn(chain.bank_narration)
            for c in CAUSES:
                hits[name][got[c] == want[c]] += 1
                confusion[name][f"{want[c]}->{got[c]}"] += 1
    out = {}
    for name in readers:
        total = hits[name][True] + hits[name][False]
        errors = {k: v for k, v in sorted(confusion[name].items(), key=lambda kv: -kv[1]) if k.split("->")[0] != k.split("->")[1]}
        # Not all errors cost the same, and in a system that files recovery claims against an
        # acquirer the asymmetry is the whole point. Asserting a charge the advice actually DENIED is
        # a false claim about money. Missing a mention is an omission, which leaves the case
        # unexplained and escalates it -- wrong, but safe. Reporting only aggregate accuracy would
        # hide that these two failure profiles are not interchangeable.
        dangerous = sum(v for k, v in errors.items() if k.endswith("->applied"))
        conservative = sum(v for k, v in errors.items() if k.endswith("->not_mentioned"))
        out[name] = {
            "correct": hits[name][True],
            "total": total,
            "accuracy": round(hits[name][True] / total, 4) if total else 0.0,
            "dangerous_errors": dangerous,
            "dangerous_error_rate": round(dangerous / total, 4) if total else 0.0,
            "conservative_errors": conservative,
            "errors": errors,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--models", default="qwen2.5:7b-instruct,qwen2.5:14b-instruct")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from ollama import Client

    client = Client(timeout=180.0)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    results = {}
    for condition, held_out in (("seen", False), ("held_out", True)):
        chains, truth = build(args.seed, args.n, held_out)
        readers = {"keyword_rule": rule_read}
        for m in models:
            readers[m] = (lambda mm: lambda nar: model_read(client, mm, nar))(m)
        print(f"\n=== {condition} phrasing (n={args.n} cases x {len(CAUSES)} causes = {args.n * len(CAUSES)} judgements) ===", flush=True)
        scored = score(chains, truth, readers)
        results[condition] = scored
        for name, r in scored.items():
            print(f"  {name:<24} {r['correct']:>4}/{r['total']:<4} = {r['accuracy'] * 100:5.1f}%", flush=True)

    print("\n=== generalisation gap (seen -> held-out) ===")
    gaps = {}
    for name in results["seen"]:
        a, b = results["seen"][name]["accuracy"], results["held_out"][name]["accuracy"]
        gaps[name] = round(b - a, 4)
        print(f"  {name:<24} {a * 100:5.1f}% -> {b * 100:5.1f}%   ({(b - a) * 100:+.1f} pts)")

    payload = {"generated_on": date.today().isoformat(), "seed": args.seed, "n": args.n, "causes": CAUSES, "conditions": results, "generalisation_gap": gaps}
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"advice-reading-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
