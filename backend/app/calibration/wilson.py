"""Wilson score confidence interval for a binomial proportion.

Used instead of a raw accuracy percentage because per-category sample sizes in a 50-200 record
batch are small — a flat "92%" with no interval invites exactly the kind of scrutiny
a judge should apply. The auto-resolve threshold is checked against the *lower bound* of this
interval, not the point estimate.
"""

import math


def wilson_score_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% CI by default (z=1.96). Returns (lower, upper), both in [0, 1]."""
    if n == 0:
        return (0.0, 1.0)  # no data at all -> maximal uncertainty, not "0% accurate"

    p_hat = successes / n
    denom = 1 + (z**2) / n
    center = (p_hat + (z**2) / (2 * n)) / denom
    margin = (z * math.sqrt((p_hat * (1 - p_hat) / n) + (z**2) / (4 * n**2))) / denom

    return (max(0.0, center - margin), min(1.0, center + margin))
