"""Does resampling disagreement predict a wrong reading?

Produces docs/evidence/semantic-entropy-<date>.json.

The cascade escalates on three signals and none of them works. Self-reported confidence is
uninformative, the verifier cannot fail in choice mode, and the tie count describes the advice rather
than the reading. This measures a fourth: sample the reader several times and see whether it agrees
with itself.

The test is AUROC of entropy against correctness. Entropy should rank WRONG readings above right
ones, so:

    AUROC ~ 0.5   no signal, and the cascade's problem is not solved by this either
    AUROC > 0.7   a usable escalation gate
    AUROC < 0.5   the signal points backwards, which would itself be worth knowing

Both outcomes are published. A negative result here is stronger than the current LIMITATIONS wording,
which says only that no signal I tried correlated with correctness.

Usage:
    cd backend
    python scripts/generate_semantic_entropy_evidence.py [--n 60] [--samples 5] [--model qwen2.5:7b-instruct]
"""

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.narrator.attribution import attribute_reader  # noqa: E402
from app.narrator.preflight import check_ollama_available  # noqa: E402
from app.resolver import resolve  # noqa: E402

# Same batch builder and same truth representation the residual table uses, so the AUROC below is
# measured on the cases that table reports and not on a differently-constructed set.
from scripts.generate_residual_evidence import build_compound_batch, truth_multiset  # noqa: E402
def _verdict_signature(raw: str) -> str | None:
    """The model's reading, normalised so formatting differences are not counted as disagreement.

    Only the verdicts matter; whitespace, key order and code fences do not. Two responses that say
    the same thing about every charge type are the same reading.
    """
    import json as _json
    import re as _re

    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = _json.loads(text[start : end + 1])
    except Exception:  # noqa: BLE001 -- an unparseable reading is a failed sample, not a new opinion
        return None
    if not isinstance(parsed, dict):
        return None
    return "|".join(f"{k}={_re.sub(r'[^a-z_]', '', str(v).lower())}" for k, v in sorted(parsed.items()))


from experiments.semantic_entropy import (  # noqa: E402
    DEFAULT_SAMPLES,
    DEFAULT_TEMPERATURE,
    auroc,
    choice_entropy,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="under-determined cases to score")
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    check_ollama_available([args.model])
    from ollama import Client

    client = Client(timeout=120.0)

    captured: list[str] = []

    def ask(messages: list[dict]) -> str:
        """Records every raw response, so entropy can be measured over the READING as well as over
        the decomposition the scorer picked from it."""
        raw = client.chat(model=args.model, messages=messages, options={"temperature": args.temperature}).message.content or ""
        captured.append(raw)
        return raw

    _, chains, context, truth_by_id = build_compound_batch(args.seed, args.n)
    cases = []
    for transaction_id, chain in chains.items():
        resolver_output = resolve(chain, context)
        if resolver_output.status != "UNDER_DETERMINED":
            continue
        cases.append((transaction_id, chain, resolver_output, truth_multiset(truth_by_id[transaction_id])))

    print(f"{len(cases)} under-determined cases, {args.samples} samples each at temperature {args.temperature}")
    print(f"{'':>4} {'entropy':>9} {'readings':>9} {'choices':>9} {'correct':>9}")

    rows = []
    started = time.perf_counter()
    for index, (transaction_id, chain, resolver_output, truth) in enumerate(cases, start=1):
        signatures: list[str | None] = []
        verdicts: list[str | None] = []
        for _ in range(args.samples):
            captured.clear()
            output = attribute_reader(chain, context, resolver_output, ask, "ollama_reader")
            signatures.append(
                tuple(sorted((c.cause, c.amount) for c in output.components)) if output.components else None
            )
            verdicts.append(_verdict_signature(captured[-1]) if captured else None)

        result = choice_entropy(signatures)
        verdict_result = choice_entropy(verdicts)
        if result is None or verdict_result is None:
            continue
        usable = [s for s in signatures if s is not None]
        modal = max(set(usable), key=usable.count)
        correct = modal == truth

        rows.append(
            {
                "transaction_id": transaction_id,
                "choice_entropy": result.entropy,
                "n_distinct_choices": result.n_distinct_answers,
                "entropy": verdict_result.entropy,
                "normalised_entropy": verdict_result.normalised_entropy,
                "n_distinct_answers": verdict_result.n_distinct_answers,
                "modal_share": verdict_result.modal_share,
                "failed_samples": result.failed_samples,
                "modal_answer_correct": correct,
            }
        )
        print(
            f"{index:>4} {verdict_result.entropy:>9.3f} {verdict_result.n_distinct_answers:>9} "
            f"{result.n_distinct_answers:>9} {str(correct):>9}",
            flush=True,
        )

    if not rows:
        raise SystemExit("no scorable cases; nothing to report")

    labels = [r["modal_answer_correct"] for r in rows]
    entropy_auroc = auroc([r["entropy"] for r in rows], labels)
    modal_auroc = auroc([-r["modal_share"] for r in rows], labels)
    n_correct = sum(labels)

    print(f"\n=== does disagreement predict a wrong reading? (n={len(rows)}) ===")
    print(f"  accuracy of the modal answer      {n_correct}/{len(rows)} = {n_correct / len(rows):.1%}")
    print(f"  AUROC, entropy over readings      {entropy_auroc}")
    print(f"  AUROC, 1 - modal share            {modal_auroc}")
    collapsed = sum(1 for r in rows if r["n_distinct_choices"] == 1)
    print(f"  cases where the scorer collapsed every reading to one choice: {collapsed}/{len(rows)}")
    for label, want in (("correct", True), ("wrong", False)):
        subset = [r["entropy"] for r in rows if r["modal_answer_correct"] is want]
        if subset:
            print(f"  mean entropy on {label:<8} readings  {statistics.mean(subset):.4f}  (n={len(subset)})")

    # An AUROC above 0.5 on 59 cases is easy to get by luck. Shuffling the correctness labels and
    # re-scoring says how easy, which is the difference between a signal and a hopeful number.
    permutation_p = None
    if entropy_auroc is not None:
        import random as _random

        rng = _random.Random(7)
        shuffles = 20_000
        at_least_as_extreme = 0
        for _ in range(shuffles):
            shuffled = labels[:]
            rng.shuffle(shuffled)
            value = auroc([r["entropy"] for r in rows], shuffled)
            if value is not None and value >= entropy_auroc:
                at_least_as_extreme += 1
        permutation_p = round((at_least_as_extreme + 1) / (shuffles + 1), 4)
        print(f"  permutation test on the AUROC     p = {permutation_p} ({shuffles:,} label shuffles)")

    if entropy_auroc is None:
        verdict = "every case fell on one side; AUROC undefined"
    elif permutation_p is not None and permutation_p >= 0.05:
        verdict = (
            f"AUROC {entropy_auroc} is not distinguishable from chance at this n "
            f"(permutation p = {permutation_p}); suggestive, not established"
        )
    elif entropy_auroc >= 0.70:
        verdict = "entropy separates correct from incorrect well enough to gate on"
    elif entropy_auroc >= 0.60:
        verdict = "entropy carries some signal, weaker than a usable gate needs"
    elif entropy_auroc > 0.45:
        verdict = "entropy does not separate correct from incorrect; the cascade's problem is not solved by this either"
    else:
        verdict = "entropy points the WRONG way; disagreement is associated with being right here"
    print(f"  verdict: {verdict}")

    payload = {
        "generated_on": date.today().isoformat(),
        "model": args.model,
        "seed": args.seed,
        "samples_per_case": args.samples,
        "temperature": args.temperature,
        "n_cases": len(rows),
        "modal_answer_accuracy": round(n_correct / len(rows), 4),
        "auroc_entropy": entropy_auroc,
        "auroc_one_minus_modal_share": modal_auroc,
        "permutation_p": permutation_p,
        "scorer_collapsed_to_one_choice": sum(1 for r in rows if r["n_distinct_choices"] == 1),
        "verdict": verdict,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "cases": rows,
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"semantic-entropy-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
