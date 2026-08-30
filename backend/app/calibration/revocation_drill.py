"""Time-to-revocation drill (upgrade build Phase 5): a demo harness around the real, already-tested
calibration machinery (`calibrate()`, `detect_drift()`, `CalibrationHistory.add_and_report()`) --
no new statistics here, just an experiment that answers a question the rest of this project can only
show anecdotally: once a category has earned auto-resolve, how many genuinely wrong decisions (and
how much real money) does it take before the system revokes that trust on its own?

Runs entirely against a fresh, isolated CalibrationHistory (a temp SQLite file, discarded when the
drill finishes) -- never touches the real app's accumulated history."""

import tempfile
from pathlib import Path

from pydantic import BaseModel

from app.calibration.calibrator import DEFAULT_THRESHOLD, NEVER_AUTO_RESOLVE, ScoredDecision
from app.calibration.history import CalibrationHistory

DEFAULT_QUALIFYING_DECISIONS = 60  # was 40, when the gate used a Wilson bound. 40 perfect decisions
# give Wilson 91.2% and clear a 90% gate, but the gate is re-checked after every batch, so it needs a
# bound valid at every stopping time (app/calibration/confidence_sequence.py). Under that bound 40
# perfect decisions are worth 86.6%, and 55 is the first n that clears 90%. 60 keeps a margin, the
# same reason 40 was chosen over the old 35.
DEFAULT_REGRESSION_BUDGET = 50  # how many deliberately-wrong decisions the drill is willing to replay before giving up
DEFAULT_AMOUNT_PER_DECISION = 50_000  # smallest-currency-unit amount per synthetic decision, an arbitrary but disclosed constant


class RevocationDrillReport(BaseModel):
    category: str
    threshold: float
    qualifying_decision_count: int  # how many clean decisions seeded the category into auto_resolve
    revoked: bool
    decisions_survived: int | None  # None if never revoked within the regression budget
    amount_survived: int | None  # cumulative real amount of the (all-wrong) regression decisions replayed before revocation
    revocation_reason: str | None  # calibrator.py's own stated reason for the category's escalate decision


def run_revocation_drill(
    category: str = "netting_trap",
    threshold: float = DEFAULT_THRESHOLD,
    n_qualifying: int = DEFAULT_QUALIFYING_DECISIONS,
    regression_budget: int = DEFAULT_REGRESSION_BUDGET,
    amount_per_decision: int = DEFAULT_AMOUNT_PER_DECISION,
) -> RevocationDrillReport:
    if category in NEVER_AUTO_RESOLVE:
        return RevocationDrillReport(
            category=category,
            threshold=threshold,
            qualifying_decision_count=0,
            revoked=False,
            decisions_survived=None,
            amount_survived=None,
            revocation_reason=f"{category!r} is in NEVER_AUTO_RESOLVE — it can never qualify for auto-resolve in the first place, so there is nothing to revoke",
        )

    other_category = "genuine_error" if category != "genuine_error" else "duplicate_refund"

    with tempfile.TemporaryDirectory() as tmp:
        history = CalibrationHistory(db_path=Path(tmp) / "drill.db")

        qualifying = [
            ScoredDecision(transaction_id=f"drill_qualify_{i}", predicted_category=category, true_label=category, amount=amount_per_decision, provider="groq")
            for i in range(n_qualifying)
        ]
        report = history.add_and_report(qualifying, threshold=threshold, source="drill")
        cat_state = next((c for c in report.categories if c.category == category), None)

        if cat_state is None or cat_state.decision != "auto_resolve":
            history.close()
            return RevocationDrillReport(
                category=category,
                threshold=threshold,
                qualifying_decision_count=n_qualifying,
                revoked=False,
                decisions_survived=None,
                amount_survived=None,
                revocation_reason=(
                    f"seeding {n_qualifying} clean decisions did not qualify {category!r} for auto-resolve at all "
                    f"(got: {cat_state.reason if cat_state else 'no report for this category'}) — increase n_qualifying"
                ),
            )

        cumulative_amount = 0
        for i in range(regression_budget):
            # every regression-phase decision is deliberately wrong (predicted_category never
            # matches true_label) -- a controlled, worst-case experiment, not a probabilistic one,
            # so the drill's result is reproducible rather than depending on a random seed.
            wrong_decision = ScoredDecision(
                transaction_id=f"drill_regress_{i}", predicted_category=category, true_label=other_category, amount=amount_per_decision, provider="groq"
            )
            report = history.add_and_report([wrong_decision], threshold=threshold, source="drill")
            cumulative_amount += amount_per_decision
            cat_state = next((c for c in report.categories if c.category == category), None)

            if cat_state is not None and cat_state.decision != "auto_resolve":
                history.close()
                return RevocationDrillReport(
                    category=category,
                    threshold=threshold,
                    qualifying_decision_count=n_qualifying,
                    revoked=True,
                    decisions_survived=i + 1,
                    amount_survived=cumulative_amount,
                    revocation_reason=cat_state.reason,
                )

        history.close()

    return RevocationDrillReport(
        category=category,
        threshold=threshold,
        qualifying_decision_count=n_qualifying,
        revoked=False,
        decisions_survived=None,
        amount_survived=None,
        revocation_reason=f"autonomy was not revoked within {regression_budget} deliberately-wrong regression decisions — increase regression_budget",
    )
