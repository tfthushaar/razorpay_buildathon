"""An escalation signal for the cascade, after the three it already had turned out not to work.

`experiments/cascade.py` routes cases up a ladder of readers and is measured at 20.0% end to end,
worse than free parsimony. LIMITATIONS records why: no signal tried correlates with correctness.
Self-reported confidence is high on wrong answers as readily as right ones. The verifier is trivially
satisfied in choice mode, so the model tiers never escalate at all. The tie count measures whether
the ADVICE discriminated, which is a property of the text and not of the reading.

Semantic entropy is the literature's answer to exactly this question. Rather than asking a model how
sure it is, sample it several times and measure how much its answers disagree. A model that returns
the same answer under resampling has a stable belief; one that scatters does not, whatever confidence
it reports.

The usual difficulty is deciding when two free-text answers mean the same thing, which normally needs
an entailment model. Here it costs nothing. Layer 0 has already enumerated the valid decompositions,
so an answer IS a choice among them, and two samples agree exactly when they select the same
component set. The clustering is an equality check.

    entropy 0.0    every sample chose the same decomposition
    entropy high   the samples scattered across many

MEASURED AT TWO LEVELS, because the first attempt measured the wrong one. Entropy over the final
DECOMPOSITION came out at exactly zero on every case: the deterministic scorer maps several different
readings onto the same choice, so it absorbs the model's variation before it reaches the answer. That
is a good property of the architecture and a useless signal for a gate. Entropy over the raw VERDICTS
is where the disagreement actually lives, and it is what the evidence script reports AUROC for.

WHETHER THIS ACTUALLY WORKS IS AN OPEN QUESTION, and the point of measuring it. The claim to test is
that entropy separates correct readings from incorrect ones, reported as AUROC. An AUROC near 0.5
means it fails like the other three, which is a more useful statement than "no signal I tried", and
the evidence script publishes it either way.

Method from Farquhar et al., Nature 2024. See docs/CREDITS.md. No third-party code.
"""

from __future__ import annotations

import math
from collections import Counter

from pydantic import BaseModel

DEFAULT_SAMPLES = 5
DEFAULT_TEMPERATURE = 0.7  # resampling at 0.0 returns the same answer every time and measures nothing


class EntropyResult(BaseModel):
    transaction_id: str
    n_samples: int
    n_distinct_answers: int
    entropy: float  # natural log units, 0.0 when every sample agreed
    normalised_entropy: float  # 0..1, divided by log(n_samples), so runs of different k compare
    modal_share: float  # share of samples that chose the most common answer
    failed_samples: int  # calls that errored or returned nothing parseable


def choice_entropy(signatures: list[str | None]) -> EntropyResult | None:
    """Shannon entropy over which decomposition the samples chose.

    A failed call is counted and dropped rather than treated as its own answer. Counting failures as a
    distinct cluster would let provider flakiness masquerade as model uncertainty, which is the same
    confusion that once let a missing API key read as a model that could not reason.
    """
    failed = sum(1 for s in signatures if s is None)
    usable = [s for s in signatures if s is not None]
    if not usable:
        return None

    counts = Counter(usable)
    total = len(usable)
    entropy = -sum((c / total) * math.log(c / total) for c in counts.values())
    entropy = abs(entropy)  # a single cluster gives -0.0, which prints as -0.000
    ceiling = math.log(total) if total > 1 else 1.0
    return EntropyResult(
        transaction_id="",
        n_samples=total,
        n_distinct_answers=len(counts),
        entropy=round(entropy, 4),
        normalised_entropy=round(entropy / ceiling, 4) if ceiling > 0 else 0.0,
        modal_share=round(counts.most_common(1)[0][1] / total, 4),
        failed_samples=failed,
    )


def components_signature(components: list) -> str:
    """A stable identity for one decomposition, so two samples can be compared for agreement.

    Sorted, so component order cannot make two identical answers look different. Cause and amount
    both matter: the same causes in different amounts is a different explanation of where the money
    went, and treating it as agreement would understate disagreement.
    """
    return "|".join(sorted(f"{c.cause}:{c.amount}" for c in components))


def auroc(scores: list[float], labels: list[bool]) -> float | None:
    """Probability that a randomly chosen correct case scores below a randomly chosen wrong one.

    Entropy is a signal of being WRONG, so it should rank wrong cases higher. 0.5 is no better than
    chance; below 0.5 means the signal points the wrong way, which is worth reporting rather than
    flipping silently.

    Computed by rank-sum with ties averaged, which matters here because entropy over 5 samples takes
    very few distinct values and ties are the common case, not an edge case.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must be the same length")
    n_pos = sum(1 for lab in labels if not lab)  # "positive" = the wrong readings entropy should flag
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None

    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1

    rank_sum = sum(ranks[i] for i, lab in enumerate(labels) if not lab)
    return round((rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg), 4)
