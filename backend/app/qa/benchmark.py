"""A ground-truth question set for the settlement Q&A agent.

The Q&A agent was the one loop in this project with no accuracy measurement. Its fourteen tests cover
routing, grounding and fail-safes -- whether it calls a tool, whether it cites only real ids, whether
it degrades cleanly on a malformed response. None of them asks whether an answer is *right*.

That is measurable here without hand-writing answers, because the batch is synthetic and
deterministic: every question below has an answer computed from the batch itself. Hand-written
expected answers would drift the moment the generator changed; derived ones cannot.

Three things are scored, and the third is the one that matters most in finance.

    numeric      the answer states the correct count or total
    citations    the transaction ids it cites are the right ones
    fabrication  ids cited that do not exist in the batch at all

Fabrication is the Q&A analogue of the keyword rule reading a denial as a confirmation. An agent that
invents a transaction id has not made a small mistake; it has produced a reference an operations
person will go and look for.

SEEN versus HELD-OUT phrasing carries over from the reading experiment, and for the same reason. The
mock provider routes on a date regex and a nine-word keyword list, and I wrote both. Questions phrased
the way that router expects measure authorship. The held-out set asks the identical questions in
words the router was never built for -- "anything that looks off", "which ones did it give up on" --
with no vocabulary from the keyword list. A test asserts the held-out phrasings contain none of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.chain.builder import CausalChain
from app.data_gen.schemas import SyntheticBatch

_ID_RE = re.compile(r"\border_[0-9a-f]{6,}\b")
_NUM_RE = re.compile(r"\b\d[\d,]*\b")


@dataclass(frozen=True)
class GroundTruth:
    """What a correct answer to one question would contain."""

    expected_number: int | None
    expected_ids: set[str]


@dataclass(frozen=True)
class QuestionSpec:
    kind: str
    seen: str
    held_out: str
    truth: Callable[[SyntheticBatch, dict[str, CausalChain], dict[str, datetime]], GroundTruth]


def _flagged_ids_truth(batch, chains, settled_at) -> GroundTruth:
    """Ids only, no expected count.

    "Which transaction ids are flagged" asks for a list. Scoring it numerically as well marked the
    model wrong for answering the question as asked, while crediting an answer that stated a count and
    named nothing. Same underlying set as `_flagged_truth`; different question, so different scoring.
    """
    return GroundTruth(
        expected_number=None,
        expected_ids={g.transaction_id for g in batch.ground_truth if g.true_label in ("duplicate_refund", "netting_trap")},
    )


def _flagged_truth(batch, chains, settled_at) -> GroundTruth:
    """Transactions the batch's own ground truth marks as duplicate_refund or netting_trap.

    Derived from the generator's answer key rather than from the tool the agent calls, so the
    benchmark is not grading the agent against its own instrument.
    """
    ids = {g.transaction_id for g in batch.ground_truth if g.true_label in ("duplicate_refund", "netting_trap")}
    return GroundTruth(expected_number=len(ids), expected_ids=ids)


def _busiest_date_truth(batch, chains, settled_at) -> GroundTruth:
    by_date: dict[str, set[str]] = {}
    for txn_id, when in settled_at.items():
        by_date.setdefault(when.date().isoformat(), set()).add(txn_id)
    if not by_date:
        return GroundTruth(expected_number=0, expected_ids=set())
    best = max(by_date.items(), key=lambda kv: (len(kv[1]), kv[0]))
    return GroundTruth(expected_number=len(best[1]), expected_ids=best[1])


def _needs_review_truth(batch, chains, settled_at) -> GroundTruth:
    """Transactions the deterministic engine could not close on its own.

    This used to score against the generator's `genuine_error` label, which was a defect: that label
    is the answer key, the agent has no access to it, and no system could ever produce the number. The
    observable meaning of "could not account for" is what reaches human review.
    """
    from app.matching.engine import run_matching_engine

    results = run_matching_engine(chains)
    ids = {t for t, r in results.items() if r.resolution == "needs_narration"}
    return GroundTruth(expected_number=len(ids), expected_ids=set())


def _clean_count_truth(batch, chains, settled_at) -> GroundTruth:
    """Transactions whose settlement matches their records exactly.

    Previously scored against the `clean_match` label (72 on seed 1) when the observable answer is
    delta == 0 (95). Same defect: unanswerable by construction, and the agent was marked wrong for it.
    """
    return GroundTruth(expected_number=sum(1 for c in chains.values() if c.settlement_delta == 0), expected_ids=set())


def _batch_size_truth(batch, chains, settled_at) -> GroundTruth:
    return GroundTruth(expected_number=len(chains), expected_ids=set())


def busiest_settlement_date(batch: SyntheticBatch, settled_at: dict[str, datetime]) -> str:
    by_date: dict[str, int] = {}
    for when in settled_at.values():
        by_date[when.date().isoformat()] = by_date.get(when.date().isoformat(), 0) + 1
    return max(by_date.items(), key=lambda kv: (kv[1], kv[0]))[0] if by_date else "2026-01-01"


# The held-out column deliberately avoids every token in agent.py's _ANOMALY_KEYWORDS
# ("duplicate", "netting", "anomal", "flagged", "suspicious", "fraud", "unexplained", "shortfall",
# "mismatch") and, where the question is not about a date, any YYYY-MM-DD literal. Same question,
# words the router was never written for. `test_held_out_phrasings_avoid_every_router_keyword`
# enforces it.
QUESTIONS: tuple[QuestionSpec, ...] = (
    QuestionSpec(
        kind="flagged_count",
        seen="How many transactions were flagged as duplicate refunds or netting traps?",
        held_out="How many payments in this run look like the same money moving twice?",
        truth=_flagged_truth,
    ),
    QuestionSpec(
        kind="flagged_ids",
        seen="Which transaction ids are flagged as suspicious in this batch?",
        held_out="List the payments a reviewer should look at first, by id.",
        truth=_flagged_ids_truth,
    ),
    QuestionSpec(
        kind="needs_review_count",
        seen="How many transactions have an unexplained shortfall?",
        held_out="How many payments could the system not account for at all?",
        truth=_needs_review_truth,
    ),
    QuestionSpec(
        kind="clean_count",
        seen="How many transactions reconciled cleanly with no mismatch?",
        held_out="How many payments came through exactly as expected?",
        truth=_clean_count_truth,
    ),
    QuestionSpec(
        kind="batch_size",
        seen="How many transactions are in this batch in total?",
        held_out="What is the size of this run?",
        truth=_batch_size_truth,
    ),
    QuestionSpec(
        kind="busiest_date",
        seen="",  # filled per batch, since the date itself is data
        held_out="On the single busiest payout day of this run, how many settled?",
        truth=_busiest_date_truth,
    ),
)


def build_questions(batch: SyntheticBatch, settled_at: dict[str, datetime]) -> list[QuestionSpec]:
    """QUESTIONS with the date-dependent prompt filled in for this batch."""
    date = busiest_settlement_date(batch, settled_at)
    out = []
    for q in QUESTIONS:
        if q.kind == "busiest_date":
            out.append(QuestionSpec(kind=q.kind, seen=f"How many transactions settled on {date}?", held_out=q.held_out, truth=q.truth))
        else:
            out.append(q)
    return out


def score_answer(answer_text: str, cited: list[str], truth: GroundTruth, all_ids: set[str]) -> dict:
    """Score one answer.

    `numeric_correct` looks for the expected figure anywhere in the prose, with thousands separators
    stripped, rather than demanding a parse of the whole sentence. A stricter check would measure
    formatting rather than correctness; a looser one (substring) would credit "10" inside "104".
    """
    numbers = {int(m.replace(",", "")) for m in _NUM_RE.findall(answer_text or "")}
    numeric_correct = truth.expected_number is not None and truth.expected_number in numbers

    cited_set = set(cited)
    fabricated = cited_set - all_ids
    if truth.expected_ids:
        overlap = cited_set & truth.expected_ids
        union = cited_set | truth.expected_ids
        citation_score = len(overlap) / len(union) if union else 1.0
    else:
        citation_score = None

    return {
        "numeric_correct": numeric_correct,
        "expected_number": truth.expected_number,
        "numbers_found": sorted(numbers)[:8],
        "citation_jaccard": round(citation_score, 4) if citation_score is not None else None,
        "fabricated_ids": sorted(fabricated),
        "n_fabricated": len(fabricated),
        "n_cited": len(cited_set),
    }


def extract_ids_from_text(text: str) -> set[str]:
    """Ids mentioned in prose but not returned in `cited_transaction_ids`.

    A model that names a transaction in its answer has cited it as far as a reader is concerned, even
    if the structured field disagrees, so fabrication is checked against both.
    """
    return set(_ID_RE.findall(text or ""))
