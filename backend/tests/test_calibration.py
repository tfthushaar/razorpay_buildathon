"""Regression tests for the calibration / auto-resolve layer (spec §6.5).

`provider="groq"` in these fixtures stands in for "a real LLM produced this decision" — the exact
label doesn't matter, only that it isn't "mock". See test_mock_decisions_never_count_toward_the_gate
for the property this whole module exists to protect after an external audit caught it missing.
"""

from app.calibration.calibrator import ScoredDecision, calibrate
from app.calibration.wilson import wilson_score_interval


def test_wilson_interval_with_no_data_is_maximally_uncertain():
    lower, upper = wilson_score_interval(0, 0)
    assert (lower, upper) == (0.0, 1.0)


def test_wilson_interval_penalizes_small_samples():
    """Same 100% point accuracy, but the small-N case must have a much wider (lower) bound —
    this is the whole reason we gate on the interval, not the raw percentage."""
    small_lower, _ = wilson_score_interval(6, 6)
    large_lower, _ = wilson_score_interval(200, 200)
    assert small_lower < large_lower
    assert small_lower < 0.70  # 6/6 alone should not be trusted as "90%+ accurate"
    assert large_lower > 0.95  # 200/200 should be trusted


def test_genuine_error_always_escalates_even_at_100pct_accuracy():
    decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="genuine_error", true_label="genuine_error", amount=100_00, provider="groq")
        for i in range(50)
    ]
    report = calibrate(decisions, threshold=0.90)
    genuine = next(c for c in report.categories if c.category == "genuine_error")
    assert genuine.accuracy == 1.0
    assert genuine.decision == "escalate"
    assert genuine.amount_at_risk == 0


def test_high_accuracy_large_n_category_auto_resolves():
    # 100/102 correct (~98%): large enough N that the Wilson *lower bound* itself clears 90%,
    # not just the point estimate — a smaller N at the same accuracy (see the test above) would
    # correctly fail to clear it, which is exactly the behavior being protected here.
    #
    # The 2 wrong decisions are placed in the MIDDLE of the sequence, not appended at the end --
    # calibrate() now also runs EWMA drift detection (app/calibration/drift.py), which is
    # deliberately recency-sensitive: 2 genuinely historical misses shouldn't read as "this
    # category's most recent decisions are bad," but 2 wrong decisions placed as literally the
    # last two processed would (correctly) look exactly like that to a recency-weighted check.
    # Scattering them mid-sequence is what makes this test represent "98% accurate with two old,
    # already-priced-in mistakes," which is what it was always meant to prove.
    decisions = (
        [
            ScoredDecision(transaction_id=f"t{i}", predicted_category="duplicate_refund", true_label="duplicate_refund", amount=500_00, provider="groq")
            for i in range(50)
        ]
        + [
            ScoredDecision(transaction_id=f"wrong{i}", predicted_category="duplicate_refund", true_label="netting_trap", amount=500_00, provider="groq")
            for i in range(2)
        ]
        + [
            ScoredDecision(transaction_id=f"t{i}", predicted_category="duplicate_refund", true_label="duplicate_refund", amount=500_00, provider="groq")
            for i in range(50, 100)
        ]
    )
    report = calibrate(decisions, threshold=0.90)
    dup = next(c for c in report.categories if c.category == "duplicate_refund")
    assert dup.n == 102
    assert dup.decision == "auto_resolve"
    assert dup.amount_at_risk > 0  # the two wrong cases contribute real rupees-at-risk, not zero


def test_low_accuracy_category_escalates_and_has_no_risk_exposure():
    decisions = [
        ScoredDecision(
            transaction_id=f"t{i}",
            predicted_category="netting_trap",
            true_label=("netting_trap" if i < 5 else "genuine_error"),
            amount=200_00,
            provider="groq",
        )
        for i in range(10)
    ]
    report = calibrate(decisions, threshold=0.90)
    netting = next(c for c in report.categories if c.category == "netting_trap")
    assert netting.decision == "escalate"
    assert netting.amount_at_risk == 0, "escalated categories should show zero risk exposure — nothing was auto-resolved"


def test_live_threshold_dial_changes_decision_without_new_data():
    """Simulates dragging the dashboard's threshold slider: same scored decisions, different
    threshold, cheap re-aggregation — this is what makes the dial 'live' rather than a re-run."""
    # The 3 wrong decisions (i in [7,8,9]) sit in the middle, not at the tail -- see the identical
    # note on test_high_accuracy_large_n_category_auto_resolves: EWMA drift detection is
    # deliberately recency-sensitive, so 3 wrong decisions ending the sequence would (correctly)
    # look like an active regression rather than "17/20 historically, three of them old misses."
    decisions = [
        ScoredDecision(
            transaction_id=f"t{i}",
            predicted_category="duplicate_refund",
            true_label=("netting_trap" if i in (7, 8, 9) else "duplicate_refund"),
            amount=300_00,
            provider="groq",
        )
        for i in range(20)
    ]
    loose = calibrate(decisions, threshold=0.60)
    strict = calibrate(decisions, threshold=0.95)
    loose_decision = next(c for c in loose.categories if c.category == "duplicate_refund").decision
    strict_decision = next(c for c in strict.categories if c.category == "duplicate_refund").decision
    assert loose_decision == "auto_resolve"
    assert strict_decision == "escalate"


def test_human_feedback_loop_can_flip_a_category_across_threshold():
    """A category that starts below threshold should be able to cross it as more human-confirmed
    resolutions accumulate — the calibration layer re-earning trust live (spec §6.5)."""
    decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00, provider="groq")
        for i in range(4)
    ]
    before = calibrate(decisions, threshold=0.85)
    assert next(c for c in before.categories if c.category == "netting_trap").decision == "escalate"

    # human resolves more escalated netting_trap cases and confirms the model was right each time
    for i in range(4, 30):
        decisions.append(
            ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00, provider="groq")
        )
    after = calibrate(decisions, threshold=0.85)
    assert next(c for c in after.categories if c.category == "netting_trap").decision == "auto_resolve"


def test_repeated_scoring_of_the_same_small_case_set_cannot_auto_resolve():
    """generate() is fully deterministic per seed (verified directly: repeated generate(seed=42,
    ...) calls produce the identical narration queue every time), and nothing before this fix
    deduplicated by transaction_id -- an external audit 2026-08-24 (round 13) found this is a real
    gaming vector requiring no threading race: repeatedly running the same (default) seed
    re-observes the SAME small set of cases and inflates the Wilson n with correlated, not
    independent, samples. Proven with this project's own committed evidence:
    docs/evidence/real-ollama-run-2026-08-24.json shows duplicate_refund n=15, but seed=42 only
    ever produces 4 distinct duplicate_refund transactions -- the accumulated 15 could only have
    come from re-scoring those same 4 cases across multiple runs.

    This test reproduces the mechanism directly: 4 distinct transactions, each re-scored 10 times
    (n=40, matching real API behavior where re-running a batch narrates the same cases again),
    always correctly. wilson_score_interval(40, 40) alone clears the 90% threshold (91.2% lower
    bound, verified separately) -- so before this fix, this exact shape of data would have wrongly
    auto-resolved on 4 real-world cases, not 40."""
    distinct_ids = [f"case{i}" for i in range(4)]
    decisions = [
        ScoredDecision(transaction_id=tid, predicted_category="duplicate_refund", true_label="duplicate_refund", amount=500_00, provider="groq")
        for tid in distinct_ids
        for _ in range(10)  # each of the 4 real cases re-scored 10 times -> n=40, distinct=4
    ]
    report = calibrate(decisions, threshold=0.90)
    dup = next(c for c in report.categories if c.category == "duplicate_refund")
    assert dup.n == 40
    assert dup.distinct_transaction_count == 4
    assert dup.ci_lower >= 0.90, "sanity check: the Wilson bound alone should already clear the threshold here"
    assert dup.decision == "escalate", "must not auto-resolve on 4 distinct real-world cases just because they were re-scored many times"
    assert "distinct" in dup.reason.lower()


def test_amount_total_and_amount_at_risk_are_not_inflated_by_repeated_rescoring():
    """A real external review (2026-08-25) caught this project's own README quoting amount_total
    as "money auto-resolved," when amount_total sums a transaction's rupee amount once per
    observation, not once per distinct transaction -- for netting_trap, 36 real decisions across
    only 15 distinct transactions produced an amount_total ~47x the real distinct money. This test
    reproduces the mechanism directly: 20 distinct transactions, each re-scored 3 times (n=60,
    distinct=20, clears the 15-distinct floor), 59/60 correct (91.1% Wilson lower bound, verified
    separately) -- the one wrong observation placed mid-sequence, not at the tail, so it reads as
    an old already-priced-in miss rather than a live drift regression (same convention as the
    high-accuracy auto-resolve test above). amount_total inflates 3x as expected;
    distinct_amount_total and amount_at_risk must NOT."""
    distinct_ids = [f"case{i}" for i in range(20)]
    decisions = [
        ScoredDecision(transaction_id=tid, predicted_category="netting_trap", true_label="netting_trap", amount=1_000_00, provider="groq")
        for tid in distinct_ids
        for _ in range(3)  # each of the 20 real cases re-scored 3 times -> n=60
    ]
    decisions[30] = ScoredDecision(  # one wrong observation, placed mid-sequence
        transaction_id=decisions[30].transaction_id, predicted_category="netting_trap", true_label="duplicate_refund", amount=1_000_00, provider="groq"
    )
    report = calibrate(decisions, threshold=0.90)
    nt = next(c for c in report.categories if c.category == "netting_trap")

    assert nt.n == 60
    assert nt.correct == 59
    assert nt.distinct_transaction_count == 20
    assert nt.decision == "auto_resolve", "sanity check: 59/60 correct should clear the 90% CI lower bound (91.1%, verified separately)"
    assert nt.amount_total == 60 * 1_000_00, "amount_total legitimately counts every observation, inflation included"
    assert nt.distinct_amount_total == 20 * 1_000_00, "distinct money must count each transaction exactly once, not once per re-score"
    assert nt.amount_at_risk == round((1 - nt.accuracy) * nt.distinct_amount_total)
    assert nt.amount_at_risk != round((1 - nt.accuracy) * nt.amount_total), "sanity check: the two formulas must actually differ here, or this test isn't exercising the bug"


def test_genuinely_distinct_cases_can_still_auto_resolve_past_the_floor():
    """The contrasting case: the floor added above must not block legitimate accumulation. Same
    n=40, same 100% accuracy, but 40 GENUINELY DISTINCT transactions (e.g. accumulated across many
    different random seeds, the way the dashboard's "Randomize" button is meant to be used) should
    still auto-resolve exactly as before this fix."""
    decisions = [
        ScoredDecision(transaction_id=f"distinct{i}", predicted_category="duplicate_refund", true_label="duplicate_refund", amount=500_00, provider="groq")
        for i in range(40)
    ]
    report = calibrate(decisions, threshold=0.90)
    dup = next(c for c in report.categories if c.category == "duplicate_refund")
    assert dup.n == 40
    assert dup.distinct_transaction_count == 40
    assert dup.decision == "auto_resolve"


def test_mock_decisions_never_count_toward_the_gate():
    """The property this whole module exists to protect (added after an external audit found it
    missing 2026-08-24): mock-mode is a deterministic rule-based stand-in for zero-cost testing,
    not AI judgment. No volume of mock decisions, however consistently 'correct', should ever be
    able to satisfy 'the AI has proven itself accurate on this category' — that claim must be
    backed by a real provider's decisions specifically."""
    mock_decisions = [
        ScoredDecision(transaction_id=f"t{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00, provider="mock")
        for i in range(200)  # even a huge volume, even 100% "accurate"
    ]
    report = calibrate(mock_decisions, threshold=0.90)
    netting = next(c for c in report.categories if c.category == "netting_trap")
    assert netting.decision == "escalate"
    assert netting.n == 0, "n must reflect only real-provider decisions, not the 200 mock ones"
    assert netting.mock_n == 200

    # mixing in a handful of real decisions on top should behave exactly like those few decisions
    # were the only data -- the 200 mock ones must contribute nothing to accuracy or n
    mixed = mock_decisions + [
        ScoredDecision(transaction_id=f"real{i}", predicted_category="netting_trap", true_label="netting_trap", amount=400_00, provider="groq")
        for i in range(3)
    ]
    mixed_report = calibrate(mixed, threshold=0.90)
    mixed_netting = next(c for c in mixed_report.categories if c.category == "netting_trap")
    assert mixed_netting.n == 3
    assert mixed_netting.mock_n == 200
    assert mixed_netting.decision == "escalate"  # n=3 real decisions still isn't enough to clear 90% CI lower bound
