"""A lower bound on a category's accuracy that survives being checked after every batch.

THE PROBLEM THIS FIXES. `CalibrationHistory.add_and_report()` recomputes the Wilson lower bound over
every decision accumulated so far, after every batch, and grants autonomy the first time it clears
90%. Wilson's coverage guarantee holds for a FIXED n. Checking repeatedly and stopping at the first
crossing is optional stopping, and the guarantee does not survive it.

It fails in the dangerous direction. Simulating the real gate, 90% threshold, checked every 5
decisions up to n=300:

    true accuracy    crossed by peeking    crossed at a fixed check
        88%                 3.12%                    0.12%
        90%                10.12%                    1.55%
        92%                35.70%                   17.18%

A cause whose true accuracy is 88%, genuinely below the bar, was being granted autonomy roughly 25
times more often than a 5% guarantee suggests. This is the gate that lets a machine close a case
about money without a human, so it is the wrong place to be quietly anti-conservative.

THE FIX. A confidence sequence is a bound valid at ALL stopping times simultaneously, which is
exactly the guarantee the calibration loop needs and Wilson does not provide. This implements the
betting construction of Waudby-Smith and Ramdas: to test whether the true accuracy could be as low as
m, bet repeatedly against that hypothesis and see whether the wealth grows.

    K_t(m) = product over i of (1 + lambda_i * (X_i - m))

Each X_i is 1 for a correct decision and 0 for a wrong one. If the true accuracy exceeds m the bets
win on average and the wealth compounds; wealth reaching 1/alpha is evidence against m at level
alpha, uniformly over time. The lower bound is the smallest m that wealth has NOT ruled out.

`lambda_i` must be predictable, meaning chosen from decisions 1..i-1 only. Looking at X_i to size the
bet on X_i is the same cheat as fitting a quantile and scoring it on the same data.

Method implemented from the papers rather than taken as a dependency. See docs/CREDITS.md.
"""

from __future__ import annotations

import math

DEFAULT_ALPHA = 0.05

# Candidate accuracies are searched on a grid. 0.001 resolves a 90% gate far finer than any decision
# count this project reaches, and keeps the whole sweep to a few hundred thousand cheap operations.
_GRID_STEP = 0.001

# Bets are truncated to this fraction of the largest stake that stays solvent. Betting the full
# amount risks a wealth of zero on a single wrong decision, after which the process can never
# recover and the bound is stuck forever.
_BET_TRUNCATION = 0.5


def _wealth_rules_out(outcomes: list[bool], m: float, alpha: float) -> bool:
    """Does betting against "true accuracy is m" reach 1/alpha, i.e. is m ruled out?

    The bet size adapts to the running mean and variance of the decisions seen SO FAR, which is what
    keeps it predictable. A cause sitting near the threshold needs smaller, longer bets than one that
    is obviously good, and a fixed bet size would be badly tuned for one of them.
    """
    threshold = 1.0 / alpha
    log_wealth = 0.0
    running_sum = 0.0
    running_sq = 0.0

    for i, outcome in enumerate(outcomes, start=1):
        # Estimates from the first i-1 decisions only.
        prior_mean = (0.5 + running_sum) / i
        prior_var = (0.25 + running_sq) / i
        denominator = prior_var * i * math.log(1.0 + i)
        raw = math.sqrt(2.0 * math.log(1.0 / alpha) / denominator) if denominator > 0 else 0.0
        # Solvency: the factor must stay positive, which binds only from below, so only m > 0
        # constrains the stake. At m = 0 the factor is 1 + bet*x and cannot go negative, and a batch
        # of entirely wrong decisions leaves the wealth at exactly 1, correctly ruling out nothing.
        bet = min(raw, _BET_TRUNCATION / m) if m > 0.0 else raw

        x = 1.0 if outcome else 0.0
        factor = 1.0 + bet * (x - m)
        if factor <= 0.0:
            return False  # bankrupt against this m; it stays in the interval
        log_wealth += math.log(factor)
        if log_wealth >= math.log(threshold):
            return True

        running_sum += x
        running_sq += (x - prior_mean) ** 2

    return False


def accuracy_lower_bound(correct: int, n: int, alpha: float = DEFAULT_ALPHA) -> float:
    """Anytime-valid lower bound, from counts alone, taking the least favourable ordering.

    The bound genuinely depends on WHEN the failures happened, not only how many there were, because
    the bets are sized from the history so far. At 57 correct of 60 the spread is wide:

        failures first  0.843     <- used here
        spread evenly   0.843
        failures last   0.903

    A first draft of this function replayed the correct decisions first, which is the ordering that
    flatters the bound most, and would have reported 0.903 for a cause whose worst case is 0.843.
    Where the true order is known, `lower_bound_from_outcomes` uses it; this counts-only path is for
    callers that never recorded it, and it assumes the worst.
    """
    if n <= 0:
        return 0.0
    outcomes = [False] * (n - correct) + [True] * correct
    return lower_bound_from_outcomes(outcomes, alpha)


def lower_bound_from_outcomes(outcomes: list[bool], alpha: float = DEFAULT_ALPHA) -> float:
    """The smallest candidate accuracy the wealth process has not ruled out."""
    if not outcomes:
        return 0.0
    steps = int(round(1.0 / _GRID_STEP))
    low, high = 0, steps  # binary search: wealth rules out every m below the bound
    while low < high:
        mid = (low + high) // 2
        if _wealth_rules_out(outcomes, mid * _GRID_STEP, alpha):
            low = mid + 1
        else:
            high = mid
    return round(low * _GRID_STEP, 4)


def wilson_vs_sequence(correct: int, n: int, alpha: float = DEFAULT_ALPHA) -> dict:
    """Both bounds side by side, for reporting what the correction costs."""
    from app.calibration.wilson import wilson_score_interval

    wilson_lower, _ = wilson_score_interval(correct, n) if n else (0.0, 0.0)
    sequence_lower = accuracy_lower_bound(correct, n, alpha)
    return {
        "n": n,
        "correct": correct,
        "wilson_lower": round(wilson_lower, 4),
        "sequence_lower": sequence_lower,
        "cost_points": round((wilson_lower - sequence_lower) * 100, 2),
    }
