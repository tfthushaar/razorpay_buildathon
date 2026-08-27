"""Regret in rupees (upgrade build Phase 4): the REALIZED cost of calibrated autonomy, not a
forward-looking risk estimate. `CategoryCalibration.amount_at_risk` (calibrator.py) already reports
`(1 - accuracy) * distinct_amount_total` for a category currently auto-resolving -- a statistical
expectation applied uniformly across all of that category's money. This module answers a narrower,
harder question instead: of the real decisions that were ACTUALLY auto-resolved without a human
looking at them, which ones were ACTUALLY wrong, and how much real money did that touch?

Answering that needs a chronological replay: a category's auto-resolve status changes over time (it
can newly qualify once distinct evidence clears the Wilson bound, and drift detection can revoke it
later -- see calibrator.py/drift.py), and only a decision made WHILE the category was already
qualified was ever actually skipped past a human. A decision from before the category qualified was
escalated for real, so it can't have caused a real loss.
"""

from pydantic import BaseModel

from app.calibration.calibrator import ScoredDecision, calibrate
from app.calibration.history import CalibrationHistory

DEFAULT_MINUTES_PER_MANUAL_REVIEW = 4.0  # disclosed assumption, not measured -- see RegretReport.minutes_per_manual_review_assumption


class RegretReport(BaseModel):
    threshold: float
    # Real, distinct rupee amount that was actually auto-resolved (no human review) while its
    # category was already qualified for auto-resolve, AND was actually wrong (predicted_category
    # != true_label) -- the realized cost of autonomy, not amount_at_risk's forward-looking estimate.
    realized_regret_amount: int
    realized_regret_transaction_count: int  # distinct transactions behind that amount
    # Distinct real-provider transactions that were EVER actually auto-resolved (correct + wrong) --
    # the denominator "hours saved" is estimated from. Deduped by transaction_id (first occurrence),
    # same discipline as calibrator.py's distinct_amount_total, so a case re-scored across multiple
    # batch runs isn't counted as a separate review avoided each time.
    auto_resolved_transaction_count: int
    minutes_per_manual_review_assumption: float  # the disclosed assumption itself -- never hidden
    estimated_analyst_hours_saved: float  # auto_resolved_transaction_count * assumption / 60 -- an ESTIMATE, not measured


def compute_regret(history: CalibrationHistory, threshold: float = 0.90) -> RegretReport:
    decisions = history.all_decisions()  # chronological (history.py's SELECT is ORDER BY id ASC)

    by_category: dict[str, list[ScoredDecision]] = {}
    for d in decisions:
        by_category.setdefault(d.predicted_category, []).append(d)

    realized_regret_amount = 0
    seen_regret_transaction_ids: set[str] = set()
    seen_auto_resolved_transaction_ids: set[str] = set()

    for category, items in by_category.items():
        for i, current in enumerate(items):
            if current.provider == "mock":
                # mock never gates or gets gated past a human -- see pipeline.py's _final_decision,
                # which requires narrator_provider != "mock" even when the category qualifies.
                continue
            prior = items[:i]
            prior_report = calibrate(prior, threshold=threshold)
            was_already_qualified = any(c.category == category and c.decision == "auto_resolve" for c in prior_report.categories)
            if not was_already_qualified:
                continue
            if current.transaction_id not in seen_auto_resolved_transaction_ids:
                seen_auto_resolved_transaction_ids.add(current.transaction_id)
            if current.predicted_category != current.true_label and current.transaction_id not in seen_regret_transaction_ids:
                seen_regret_transaction_ids.add(current.transaction_id)
                realized_regret_amount += current.amount

    return RegretReport(
        threshold=threshold,
        realized_regret_amount=realized_regret_amount,
        realized_regret_transaction_count=len(seen_regret_transaction_ids),
        auto_resolved_transaction_count=len(seen_auto_resolved_transaction_ids),
        minutes_per_manual_review_assumption=DEFAULT_MINUTES_PER_MANUAL_REVIEW,
        estimated_analyst_hours_saved=round(len(seen_auto_resolved_transaction_ids) * DEFAULT_MINUTES_PER_MANUAL_REVIEW / 60, 2),
    )
