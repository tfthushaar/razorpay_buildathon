"""Tests for the EWMA drift detector (app/calibration/drift.py).

Three things need proving: (1) the EWMA arithmetic itself matches a hand-computed example, not just
"looks reasonable" -- a wrong smoothing formula could silently produce a number that happens to
still sit inside the control limits; (2) a genuinely stable category never gets flagged, even with
the occasional expected miss; (3) a genuine recent regression -- good historically, bad on its most
recent decisions -- gets caught, which is the entire point of adding this on top of the aggregate
Wilson CI in calibrator.py.
"""

import math

from app.calibration.drift import DEFAULT_LAMBDA, MIN_POINTS_FOR_DRIFT_CHECK, detect_drift


def test_too_few_points_never_flags_regardless_of_outcomes():
    outcomes = [False] * (MIN_POINTS_FOR_DRIFT_CHECK - 1)  # all wrong, but below the floor
    result = detect_drift(outcomes, target=0.95)
    assert not result.breached


def test_ewma_arithmetic_matches_a_hand_computed_example():
    """lambda=0.5 for round, easy-to-check-by-hand numbers. z_0 = target = 0.8.
    outcomes: [1,1,1,1,1,1,1,0] (7 correct then 1 wrong -- the 8th, at the floor exactly).
    z_1..z_7 = 0.5*1 + 0.5*z_{i-1}, converging toward 1.0 from 0.8:
    z1=0.9, z2=0.95, z3=0.975, z4=0.9875, z5=0.99375, z6=0.996875, z7=0.9984375
    z8 (outcome=0) = 0.5*0 + 0.5*0.9984375 = 0.49921875"""
    outcomes = [True] * 7 + [False]
    result = detect_drift(outcomes, target=0.8, lambda_=0.5)
    assert math.isclose(result.ewma, 0.49921875, rel_tol=1e-9)


def test_stable_high_accuracy_with_one_expected_miss_does_not_flag():
    # 20 real decisions, 19 correct, 1 wrong somewhere in the middle -- a perfectly normal,
    # non-drifting category shouldn't get flagged just because it isn't literally 100%.
    outcomes = [True] * 10 + [False] + [True] * 9
    result = detect_drift(outcomes, target=0.95)
    assert not result.breached


def test_a_genuine_recent_regression_is_caught():
    # 20 historically-correct decisions, then 6 consecutive wrong ones -- the exact shape the
    # aggregate Wilson CI in calibrator.py would be slow to react to (26 decisions, 20 correct is
    # still 76.9% aggregate accuracy, nowhere near enough on its own to explain why this SPECIFIC
    # recent run of failures should raise concern right now).
    outcomes = [True] * 20 + [False] * 6
    result = detect_drift(outcomes, target=0.95)
    assert result.breached
    assert result.ewma < result.lower_control_limit


def test_a_single_recent_miss_after_a_long_good_streak_does_not_falsely_flag():
    # one wrong decision after 20 correct ones should NOT look like a systemic regression --
    # EWMA's smoothing is exactly what should absorb ordinary single-case noise.
    outcomes = [True] * 20 + [False]
    result = detect_drift(outcomes, target=0.95)
    assert not result.breached


def test_perfect_target_handles_zero_variance_without_crashing():
    # target=1.0 makes sigma=0 (target*(1-target)=0) -- any wrong outcome should breach immediately
    # (there's no "normal variance" around a 100% target), and this must not raise (ZeroDivisionError,
    # NaN from a negative sqrt argument, etc).
    outcomes = [True] * (MIN_POINTS_FOR_DRIFT_CHECK - 1) + [False]
    result = detect_drift(outcomes, target=1.0)
    assert result.lower_control_limit == 1.0
    assert result.breached


def test_default_lambda_is_exported_and_used_when_unspecified():
    outcomes = [True] * MIN_POINTS_FOR_DRIFT_CHECK
    explicit = detect_drift(outcomes, target=0.9, lambda_=DEFAULT_LAMBDA)
    default = detect_drift(outcomes, target=0.9)
    assert explicit.ewma == default.ewma
