"""Paired significance testing for the rule-vs-model comparisons.

Every headline in docs/RESULTS.md compares two systems on the SAME cases: the same settlements, the
same bank rows, the same option lists. That pairing is the whole design — it is what makes a
difference attributable to the one thing that was swapped — and it also means comparing the two
confidence intervals is the wrong test. Independent intervals ignore the pairing and are badly
conservative: the three-source model (94.7%, [89.8, 97.3]) and regex (88.0%, [81.8, 92.3]) intervals
OVERLAP, while the paired test on the same data puts the difference at p = 0.002.

So the right test is McNemar's, computed exactly rather than with the chi-square approximation, since
the discordant counts here are small enough (single figures on the residual) that the approximation
is not trustworthy. Only the discordant pairs carry information: cases both systems got right, or
both got wrong, say nothing about which is better.

This module exists because of a real error it would have prevented. I published "parsimony beats
every reader including 14b" off 19/60 against 16/60 — a three-case difference. The exact paired test
on those same cases gives 7 discordant one way, 4 the other, p = 0.55: not distinguishable from
chance. Overstating a result AGAINST my own architecture costs exactly as much credibility as
overstating one for it, and it is the easier mistake to miss because it feels like humility.
"""

from math import comb

from pydantic import BaseModel

from app.calibration.wilson import wilson_score_interval


class PairedComparison(BaseModel):
    """The 2x2 paired table and its exact test.

    `only_a` / `only_b` are the discordant cells — the only ones the test uses.
    """

    label_a: str
    label_b: str
    n: int
    correct_a: int
    correct_b: int
    both: int
    only_a: int
    only_b: int
    neither: int
    p_value: float

    @property
    def accuracy_a(self) -> float:
        return self.correct_a / self.n if self.n else 0.0

    @property
    def accuracy_b(self) -> float:
        return self.correct_b / self.n if self.n else 0.0

    @property
    def discordant(self) -> int:
        return self.only_a + self.only_b

    def significant_at(self, alpha: float = 0.05) -> bool:
        return self.p_value < alpha

    def summary(self) -> str:
        lo_a, hi_a = wilson_score_interval(self.correct_a, self.n)
        lo_b, hi_b = wilson_score_interval(self.correct_b, self.n)
        verdict = "significant" if self.significant_at() else "NOT distinguishable"
        return (
            f"{self.label_a} {self.correct_a}/{self.n} = {self.accuracy_a:.1%} [{lo_a:.1%}, {hi_a:.1%}]  vs  "
            f"{self.label_b} {self.correct_b}/{self.n} = {self.accuracy_b:.1%} [{lo_b:.1%}, {hi_b:.1%}]  |  "
            f"discordant {self.only_a}/{self.only_b}, exact McNemar p={self.p_value:.4f} ({verdict})"
        )


def exact_mcnemar_p(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar (a binomial sign test on the discordant pairs).

    Under the null that neither system is better, each discordant pair is a fair coin, so the
    discordant counts are Binomial(discordant, 0.5). Doubling the one-sided tail is the standard
    two-sided construction; it is clamped at 1.0 because doubling can exceed 1 when the split is
    close to even.
    """
    discordant = only_a + only_b
    if discordant == 0:
        return 1.0
    smaller = min(only_a, only_b)
    one_sided = sum(comb(discordant, k) for k in range(smaller + 1)) / (2**discordant)
    return min(1.0, 2 * one_sided)


def compare_paired(label_a: str, results_a: dict[str, bool], label_b: str, results_b: dict[str, bool]) -> PairedComparison:
    """Compare two systems keyed by case id. Only cases present in BOTH are used.

    Keying by case id rather than by position is deliberate: a positional zip silently mis-pairs if
    either side ever reorders or skips a case, and a mis-paired McNemar looks perfectly healthy while
    testing nothing.
    """
    shared = sorted(set(results_a) & set(results_b))
    both = only_a = only_b = neither = 0
    for case in shared:
        a, b = results_a[case], results_b[case]
        if a and b:
            both += 1
        elif a:
            only_a += 1
        elif b:
            only_b += 1
        else:
            neither += 1

    return PairedComparison(
        label_a=label_a,
        label_b=label_b,
        n=len(shared),
        correct_a=both + only_a,
        correct_b=both + only_b,
        both=both,
        only_a=only_a,
        only_b=only_b,
        neither=neither,
        p_value=exact_mcnemar_p(only_a, only_b),
    )


def robustness_p(only_a: int, only_b: int, concede: int = 2) -> float:
    """The p-value after conceding `concede` discordant cases to whichever side is losing.

    A sensitivity check worth publishing next to the headline: if a couple of the cases credited to
    the winner were scored wrongly, does the conclusion survive? Reporting the number that survives
    that concession is a stronger claim than the raw one, and it costs nothing to compute.

    The concession is clamped to what the winning side actually has. Without the clamp, conceding 2
    from a 0-vs-1 split produces a count of -1, and `exact_mcnemar_p` then sums an empty range and
    returns a confident **p = 0.0** for a comparison with one discordant case in it. Caught in a live
    run, where a column that lost 1-0 was reported as p=0.0000 after concession — a sensitivity check
    that manufactures significance is worse than no sensitivity check.
    """
    shift = min(concede, max(only_a, only_b))
    if only_a >= only_b:
        return exact_mcnemar_p(only_a - shift, only_b + shift)
    return exact_mcnemar_p(only_a + shift, only_b - shift)
