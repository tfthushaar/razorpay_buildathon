"""Does the settlement Q&A agent answer correctly, and does it invent transaction ids?

Produces docs/evidence/qa-<date>.json.

This was the one loop in the project with no accuracy number. Its tests covered routing, grounding and
fail-safes -- never whether an answer was right. Ground truth here is computed from the batch's own
answer key, not hand-written, so it cannot drift from the generator.

Scored per answer:

    numeric      the correct count appears in the prose
    citations    Jaccard overlap between cited ids and the ids that should have been cited
    fabrication  ids cited (in the structured field or in the prose) that are not in the batch

Fabrication is the number that matters. An agent that invents a transaction id has not made a small
error; it has produced a reference an operations person will go and look for.

SEEN versus HELD-OUT phrasing carries over from the reading experiment. The mock provider routes on a
date regex and a nine-word keyword list, both of which I wrote, so questions phrased that way measure
authorship rather than comprehension. The held-out column asks the same questions in words the router
was never built for.

Usage:
    cd backend
    python scripts/generate_qa_evidence.py [--seeds 5] [--n 120] [--providers mock,ollama]
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains  # noqa: E402
from app.data_gen.generate import generate  # noqa: E402
from app.narrator.preflight import check_ollama_available  # noqa: E402
from app.narrator.tools import build_tool_context  # noqa: E402
from app.qa.agent import answer_question  # noqa: E402
from app.qa.benchmark import build_questions, extract_ids_from_text, score_answer  # noqa: E402
from app.qa.tools import build_settled_at_index  # noqa: E402


def run_condition(provider: str, seeds: list[int], n: int, phrasing: str) -> dict:
    per_question: dict[str, Counter] = {}
    numeric_hits = numeric_total = 0
    fabricated_total = fabricated_answers = 0
    citation_scores: list[float] = []
    examples: list[dict] = []

    for seed in seeds:
        batch, _ = generate(seed=seed, main_n=n, stress_n=0)
        chains = build_all_chains(batch)
        context = build_tool_context(batch, chains)
        settled_at = build_settled_at_index(batch)
        all_ids = set(chains)

        for spec in build_questions(batch, settled_at):
            question = spec.seen if phrasing == "seen" else spec.held_out
            truth = spec.truth(batch, chains, settled_at)
            result = answer_question(question, context, settled_at, provider=provider)

            cited = list(set(result.cited_transaction_ids) | extract_ids_from_text(result.answer))
            scored = score_answer(result.answer, cited, truth, all_ids)

            bucket = per_question.setdefault(spec.kind, Counter())
            bucket["n"] += 1
            if truth.expected_number is not None:
                numeric_total += 1
                bucket["numeric_n"] += 1
                if scored["numeric_correct"]:
                    numeric_hits += 1
                    bucket["numeric_correct"] += 1
            if scored["citation_jaccard"] is not None:
                citation_scores.append(scored["citation_jaccard"])
            if scored["n_fabricated"]:
                fabricated_total += scored["n_fabricated"]
                fabricated_answers += 1
                bucket["fabricated"] += 1
                if len(examples) < 5:
                    examples.append(
                        {
                            "seed": seed,
                            "kind": spec.kind,
                            "question": question,
                            "answer": (result.answer or "")[:220],
                            "fabricated_ids": scored["fabricated_ids"][:5],
                        }
                    )

    answers = sum(b["n"] for b in per_question.values())
    return {
        "provider": provider,
        "phrasing": phrasing,
        "answers": answers,
        "numeric_correct": numeric_hits,
        "numeric_scored": numeric_total,
        "numeric_accuracy": round(numeric_hits / numeric_total, 4) if numeric_total else None,
        "mean_citation_jaccard": round(sum(citation_scores) / len(citation_scores), 4) if citation_scores else None,
        "answers_with_fabricated_ids": fabricated_answers,
        "fabrication_rate": round(fabricated_answers / answers, 4) if answers else 0.0,
        "fabricated_ids_total": fabricated_total,
        "per_question": {k: dict(v) for k, v in sorted(per_question.items())},
        "fabrication_examples": examples,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--providers", default="mock,ollama")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    if "ollama" in providers:
        import os

        check_ollama_available([os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")])

    seeds = list(range(1, args.seeds + 1))
    results: dict[str, dict] = {}

    for phrasing in ("seen", "held_out"):
        print(f"\n=== {phrasing} phrasing ({len(seeds)} seeds x 6 questions) ===", flush=True)
        print(f"  {'provider':<10} {'numeric':>12} {'citations':>11} {'fabricated':>12}")
        for provider in providers:
            r = run_condition(provider, seeds, args.n, phrasing)
            results[f"{provider}:{phrasing}"] = r
            num = f"{r['numeric_correct']}/{r['numeric_scored']}" if r["numeric_scored"] else "n/a"
            acc = f"({r['numeric_accuracy'] * 100:.0f}%)" if r["numeric_accuracy"] is not None else ""
            cit = f"{r['mean_citation_jaccard']:.2f}" if r["mean_citation_jaccard"] is not None else "n/a"
            fab = f"{r['answers_with_fabricated_ids']}/{r['answers']} ({r['fabrication_rate'] * 100:.0f}%)"
            print(f"  {provider:<10} {num + ' ' + acc:>12} {cit:>11} {fab:>12}", flush=True)

    print("\n=== generalisation gap (seen -> held-out), numeric accuracy ===")
    gaps = {}
    for provider in providers:
        a = results[f"{provider}:seen"]["numeric_accuracy"]
        b = results[f"{provider}:held_out"]["numeric_accuracy"]
        if a is not None and b is not None:
            gaps[provider] = round(b - a, 4)
            print(f"  {provider:<10} {a * 100:5.1f}% -> {b * 100:5.1f}%   ({(b - a) * 100:+.1f} pts)")

    payload = {
        "generated_on": date.today().isoformat(),
        "seeds": seeds,
        "n_per_batch": args.n,
        "questions_per_batch": 6,
        "conditions": results,
        "generalisation_gap_numeric": gaps,
    }
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"qa-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
