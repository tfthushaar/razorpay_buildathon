"""EWMA-based drift detection for a category's real-provider accuracy over time -- an exponentially
weighted moving average control chart, the same family of statistical-process-control technique
Six Sigma manufacturing lines use to catch a process quietly drifting out of spec before it fails a
hard threshold outright (Montgomery, "Introduction to Statistical Quality Control" -- the EWMA chart
for monitoring a proportion; not novel here, borrowed for the reason Merkle trees were borrowed from
distributed systems: the problem it solves is real and the technique already exists elsewhere).

Why this exists, distinct from calibrator.py's Wilson confidence interval: the Wilson bound is an
AGGREGATE statistic over every real-provider decision a category has ever received. Once a category
earns trust, it stays trusted as long as the aggregate holds up -- but the aggregate is exactly what
makes it slow to react to a genuine RECENT regression. A category with 40 historical correct
decisions that starts getting its next 5 wrong barely moves the aggregate accuracy at all, and the
Wilson lower bound can stay comfortably above threshold for a while even though the last 5 decisions
are a real, current problem. EWMA weights recent observations exponentially more than old ones by
design, so it reacts to a genuine shift much faster than an all-time aggregate can, without needing
a rolling-window size to be chosen up front -- the exponential decay does that implicitly.

This is an ADDITIONAL gate on top of (not instead of) the existing Wilson-CI and distinct-transaction
requirements, the same pattern calibrator.py already established for MIN_DISTINCT_TRANSACTIONS_FOR_
AUTO_RESOLVE: a category can fail any one of the three checks and that alone is enough to escalate.
"""

from dataclasses import dataclass

# Fewer real-provider decisions than this and the EWMA control limits are too wide (dominated by
# the transient-variance term, see below) to mean anything -- flagging "drift" off 2-3 points would
# just be noise dressed up as a statistical finding. Chosen to match the shape of this project's
# own MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE floor (calibrator.py): a deliberately conservative
# minimum-evidence bar, not a value tuned to make any specific demo scenario trip or not trip.
MIN_POINTS_FOR_DRIFT_CHECK = 8

DEFAULT_LAMBDA = 0.3  # smoothing factor -- higher weights recent observations more heavily
DEFAULT_L_SIGMA = 3.0  # control-limit width in standard deviations, the standard "3-sigma" convention


@dataclass
class DriftResult:
    ewma: float  # the current EWMA statistic -- a smoothed, recency-weighted accuracy estimate
    target: float  # the aggregate accuracy this EWMA is being checked against
    lower_control_limit: float  # ewma below this is a real, not noise, decline
    breached: bool  # True only if there were enough real decisions to check at all


def detect_drift(
    outcomes: list[bool],
    target: float,
    lambda_: float = DEFAULT_LAMBDA,
    l_sigma: float = DEFAULT_L_SIGMA,
) -> DriftResult:
    """`outcomes`: True/False per real-provider decision, in chronological order (oldest first) --
    the caller (calibrator.calibrate) is responsible for that ordering; this function has no way to
    verify it and will silently produce a meaningless result on shuffled input. `target`: the
    reference accuracy to check against (this project uses the category's own current aggregate
    accuracy, i.e. "has the recent trend diverged from what the aggregate says it should be").

    Uses Montgomery's full EWMA control-chart formula for a Bernoulli/attribute process, including
    the transient-variance term (1 - (1-lambda)^(2n)) rather than only the steady-state
    approximation -- this matters specifically because MIN_POINTS_FOR_DRIFT_CHECK is deliberately
    small (8), where the transient term still meaningfully widens the control limit relative to its
    steady-state value; dropping it would make the check falsely trigger on data volumes this
    module explicitly still considers borderline."""
    if len(outcomes) < MIN_POINTS_FOR_DRIFT_CHECK:
        return DriftResult(ewma=target, target=target, lower_control_limit=0.0, breached=False)

    z = target
    for outcome in outcomes:
        x = 1.0 if outcome else 0.0
        z = lambda_ * x + (1 - lambda_) * z

    n = len(outcomes)
    variance_factor = (lambda_ / (2 - lambda_)) * (1 - (1 - lambda_) ** (2 * n))
    sigma = (target * (1 - target) * variance_factor) ** 0.5
    lcl = max(0.0, target - l_sigma * sigma)

    return DriftResult(ewma=z, target=target, lower_control_limit=lcl, breached=z < lcl)
