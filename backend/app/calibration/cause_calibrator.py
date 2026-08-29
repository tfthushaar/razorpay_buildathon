"""Calibration per CAUSE, not per category -- the trust unit the residual architecture actually needs.

Category-level calibration answers "can this system be trusted on netting traps". That was the right
question while the model's output was a label. Once the output is a decomposition, it is the wrong
question: one transaction produces several independent attributions, each of which is separately
right or wrong, and lumping them into one verdict throws away most of the signal and all of the
actionability.

Per-cause scoring says something a category verdict cannot:

    fee-rate discrepancies are attributed accurately enough to act on without review
    (Wilson lower bound 94%, n=180); refund-duplication attributions are not (LB 71%) and
    those escalate.

That is a decision an operations lead can actually take, and it is the shape of trust that makes the
drift-revocation machinery load-bearing rather than decorative -- a cause can lose autonomy on its
own without dragging four unrelated ones down with it.

It also fixes a real statistical weakness in the old framing, which is worth naming plainly: a
category verdict on a category that appears ten times a batch has n=10, and a 10/10 result has a
Wilson lower bound of 72.2% -- nowhere near a 90% gate. Scoring per cause over decompositions
generates several judgements per transaction rather than one, so n grows fast enough for a lower
bound to actually mean something within a realistic number of runs.

PRECISION is what gates autonomy here, deliberately. "Of the fee-rate discrepancies this system
asserted, how many were real" is the question that matters when the consequence is an automated
recovery claim against an acquirer. Recall is tracked and reported, because a system that silently
omits half the real causes is failing in a way precision alone would never show -- but it does not
gate, because omitting a cause escalates a case, and escalating is always safe.
"""

from pydantic import BaseModel, computed_field

from app.calibration.wilson import wilson_score_interval

DEFAULT_THRESHOLD = 0.90

# Attributions this system will never act on unattended, whatever the measured accuracy.
#
# `netting_adjustment` asserts that another merchant transaction absorbed this one's shortfall, so
# acting on it unattended moves money between two merchants' books on the strength of one inference.
# `promotional_waiver` rests entirely on a free-text reading, with no structured record anywhere
# confirming it -- the one cause whose only evidence is a sentence. Both escalate by policy, on the
# same principle as the category layer's `genuine_error`: no accuracy figure makes acting alone on
# these the right call.
NEVER_AUTO_ATTRIBUTE = {"netting_adjustment", "promotional_waiver"}


class ScoredAttribution(BaseModel):
    """One (cause, was-it-real) judgement, from one component of one proposed decomposition."""

    transaction_id: str
    cause: str
    amount: int
    correct: bool
    provider: str
    was_in_truth_but_omitted: bool = False  # recall miss: truth had this cause, the proposal did not


class CauseCalibration(BaseModel):
    cause: str
    n: int  # real-provider assertions of this cause
    correct: int
    accuracy: float
    ci_lower: float
    ci_upper: float
    omitted: int  # times this cause was genuinely present and NOT proposed
    recall: float | None
    decision: str
    reason: str
    mock_n: int = 0


class CauseCalibrationReport(BaseModel):
    threshold: float
    causes: list[CauseCalibration]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auto_attribute_causes(self) -> list[str]:
        return [c.cause for c in self.causes if c.decision == "auto_attribute"]


def calibrate_causes(scored: list[ScoredAttribution], threshold: float = DEFAULT_THRESHOLD) -> CauseCalibrationReport:
    by_cause: dict[str, list[ScoredAttribution]] = {}
    for s in scored:
        by_cause.setdefault(s.cause, []).append(s)

    out: list[CauseCalibration] = []
    for cause, items in sorted(by_cause.items()):
        # mock decisions are tracked but never gate autonomy -- identical policy to the category
        # calibrator, and for the identical reason: mock here is a deterministic keyword rule
        # (app/resolver/keyword_baseline.py), not model judgement, so letting it accumulate toward
        # "the AI has proven itself" would make the gate satisfiable with no model ever called
        real = [i for i in items if i.provider != "mock" and not i.was_in_truth_but_omitted]
        mock_n = sum(1 for i in items if i.provider == "mock" and not i.was_in_truth_but_omitted)
        omitted = sum(1 for i in items if i.was_in_truth_but_omitted and i.provider != "mock")

        n = len(real)
        correct = sum(1 for i in real if i.correct)
        accuracy = correct / n if n else 0.0
        lower, upper = wilson_score_interval(correct, n) if n else (0.0, 0.0)
        recall = correct / (correct + omitted) if (correct + omitted) else None

        if cause in NEVER_AUTO_ATTRIBUTE:
            decision, reason = "escalate", "policy: this cause never auto-attributes, whatever the measured accuracy"
        elif n == 0:
            decision, reason = "escalate", "no real-provider assertions of this cause yet"
        elif lower >= threshold:
            decision, reason = "auto_attribute", f"Wilson lower bound {lower:.1%} >= threshold {threshold:.0%} over n={n}"
        else:
            decision, reason = "escalate", f"Wilson lower bound {lower:.1%} < threshold {threshold:.0%} over n={n}"

        out.append(
            CauseCalibration(
                cause=cause,
                n=n,
                correct=correct,
                accuracy=round(accuracy, 4),
                ci_lower=round(lower, 4),
                ci_upper=round(upper, 4),
                omitted=omitted,
                recall=round(recall, 4) if recall is not None else None,
                decision=decision,
                reason=reason,
                mock_n=mock_n,
            )
        )

    return CauseCalibrationReport(threshold=threshold, causes=out)


def score_attribution(
    transaction_id: str, proposed_components, true_causes, provider: str
) -> list[ScoredAttribution]:
    """Turn one proposed decomposition into per-cause judgements against ground truth.

    A component counts as correct when its (cause, amount) pair is genuinely in the truth -- naming
    the right mechanism with the wrong number is not a partial success when the number is what gets
    claimed back from an acquirer. Truth entries the proposal never mentioned are emitted as recall
    misses so omission is visible rather than free.
    """
    truth_pairs = {(c.cause, c.amount) for c in true_causes}
    proposed_pairs = {(c.cause, c.amount) for c in proposed_components}

    scored = [
        ScoredAttribution(
            transaction_id=transaction_id,
            cause=c.cause,
            amount=c.amount,
            correct=(c.cause, c.amount) in truth_pairs,
            provider=provider,
        )
        for c in proposed_components
    ]
    scored += [
        ScoredAttribution(
            transaction_id=transaction_id,
            cause=cause,
            amount=amount,
            correct=False,
            provider=provider,
            was_in_truth_but_omitted=True,
        )
        for cause, amount in sorted(truth_pairs - proposed_pairs)
    ]
    return scored
