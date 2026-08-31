"""Was each transaction handled correctly, which is not the same as whether it was resolved.

The batch match rate counts every escalation as a transaction the system failed to close. That is
wrong for a category the policy forbids closing. `genuine_error` is in NEVER_AUTO_RESOLVE by
design: no accuracy figure makes auto-resolving an admittedly-unexplained shortfall correct, so
escalating one is the right answer and scoring it as a miss understates the system against its own
policy. On seed 42 that is 6 of 18 escalations.

Four outcomes, one per transaction, mutually exclusive:

    correctly_resolved    auto-resolved, and the category was right
    wrongly_resolved      auto-resolved and wrong, OR auto-resolved a forbidden category
    missed                escalated, when resolving it would have been correct and permitted
    correctly_escalated   escalated, and escalating was the right answer

    correct_disposition = correctly_resolved + correctly_escalated

THREE GUARDS, because a metric that only ever moves up is a metric nobody should trust.

  1. The strict match rate is reported beside it, always. This is an addition to the headline, not
     a replacement for it.
  2. Auto-resolving a NEVER_AUTO_RESOLVE category counts as `wrongly_resolved`, never as a correct
     disposition. Without that, disposition could be maximised by resolving exactly the things the
     policy exists to stop, and `test_disposition.py` asserts it directly.
  3. `wrongly_resolved` is a share of the TOTAL, not of the resolved subset, so a system that
     resolves less but is wrong about more of what it does resolve cannot hide behind a smaller
     denominator.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.calibration.calibrator import NEVER_AUTO_RESOLVE


class DispositionReport(BaseModel):
    total: int
    correctly_resolved: int
    wrongly_resolved: int
    missed: int
    correctly_escalated: int

    @property
    def correct_disposition(self) -> int:
        return self.correctly_resolved + self.correctly_escalated

    @property
    def correct_disposition_rate(self) -> float:
        return self.correct_disposition / self.total if self.total else 0.0

    @property
    def strict_resolution_rate(self) -> float:
        """The number this project has always published: resolved over total, escalations counted
        as failures whether or not escalating them was correct."""
        return self.correctly_resolved / self.total if self.total else 0.0

    @property
    def wrongly_resolved_rate(self) -> float:
        """Against the total, deliberately. See guard 3."""
        return self.wrongly_resolved / self.total if self.total else 0.0


def score_dispositions(
    outcomes: list[tuple[str, str, bool]],
) -> DispositionReport:
    """Score one batch.

    Each tuple is (true_label, predicted_category, was_auto_resolved). Nothing here reads the
    matching engine or the calibration gate; it takes the decisions already made and asks whether
    each one was the right disposition for that transaction.
    """
    correctly_resolved = wrongly_resolved = missed = correctly_escalated = 0

    for true_label, predicted, auto_resolved in outcomes:
        forbidden = true_label in NEVER_AUTO_RESOLVE or predicted in NEVER_AUTO_RESOLVE
        if auto_resolved:
            # Guard 2: closing a forbidden category is a wrong disposition even when the category
            # label itself is correct. The policy is the thing being violated, not the taxonomy.
            if forbidden or predicted != true_label:
                wrongly_resolved += 1
            else:
                correctly_resolved += 1
        elif forbidden:
            correctly_escalated += 1
        else:
            missed += 1

    return DispositionReport(
        total=len(outcomes),
        correctly_resolved=correctly_resolved,
        wrongly_resolved=wrongly_resolved,
        missed=missed,
        correctly_escalated=correctly_escalated,
    )
