"""Match weights estimated from data, instead of the constants I picked by hand.

`entity_resolution.py` scores a candidate bank row with weights I chose: 2.0 for an exact UTR, 1.0
for an exact amount, 0.5 for an exact date, the name similarity as-is, and plus or minus 3.0 for the
settlement cycle. They are reasonable and they are arbitrary, which is the standing criticism
probabilistic record linkage makes of fuzzy matching: the numbers encode the author's belief about
which fields matter rather than what the data says.

The Fellegi-Sunter model answers the same question by estimating, per field, two probabilities:

    m = P(the field agrees | the pair is a true match)
    u = P(the field agrees | the pair is not a match)

and weighting agreement by log2(m/u), disagreement by log2((1-m)/(1-u)). A field that agrees on
almost every true match and almost no false one carries a large weight; a field that agrees
everywhere carries almost none, however important it feels. Both are read off the data.

FITTING AND SCORING NEVER SHARE A BATCH. The weights are estimated on one seed and applied to
another, for the same reason the calibrated forecast interval is fitted and verified separately:
estimating m and u from the pairs you then score is measuring memorisation. `fit_weights` takes the
truth mapping and is only ever called on a calibration batch.

WHAT THIS IS EXPECTED TO SHOW, AND WHY IT IS WORTH RUNNING ANYWAY. In the hard case every structured
field ties by construction: same merchant, same amount, same day, and the truncated UTRs share a
tail. No weighting scheme breaks a genuine tie, so this should not move the headline. What it buys is
that the headline stops depending on my constants. "Even with weights estimated from the data, the
structured fields tie" is a claim about the problem; "with my weights, they tie" is a claim about me.

Method from Fellegi and Sunter (1969), and from Splink's argument for it. See docs/CREDITS.md.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from app.resolver.entity_resolution import (
    MAX_AMOUNT_SLIP_PAISE,
    MAX_DATE_SLIP_DAYS,
    BankRow,
    SettlementRow,
    _name_similarity,
    _utr_match,
)

# Comparators, each reduced to a yes/no. Finer buckets would estimate more parameters from the same
# few hundred pairs, and the amount of data here does not support that.
FIELDS = ("utr_exact", "amount_exact", "date_exact", "name_close")

NAME_CLOSE_THRESHOLD = 0.5

# m and u are clamped away from 0 and 1 so a field that happens to agree on every observed match in a
# calibration batch produces a large weight rather than an infinite one.
_EPSILON = 1e-3


class FieldWeights(BaseModel):
    m: float
    u: float
    agree_weight: float
    disagree_weight: float


class MatchWeights(BaseModel):
    fields: dict[str, FieldWeights]
    n_matches: int
    n_non_matches: int

    def score(self, comparisons: dict[str, bool]) -> float:
        total = 0.0
        for field, weights in self.fields.items():
            total += weights.agree_weight if comparisons.get(field) else weights.disagree_weight
        return total


def compare(settlement: SettlementRow, row: BankRow) -> dict[str, bool] | None:
    """Yes/no per field, or None when the pair is outside the blocking window entirely.

    The same filters `match_settlement` applies, so the two scorers see the identical candidate set
    and any difference between them is the weighting and nothing else.
    """
    amount_delta = row.credit_amount - settlement.amount
    if abs(amount_delta) > MAX_AMOUNT_SLIP_PAISE:
        return None
    date_slip = (row.value_date.date() - settlement.value_date.date()).days
    if not -MAX_DATE_SLIP_DAYS <= date_slip <= MAX_DATE_SLIP_DAYS:
        return None
    hit, evidence = _utr_match(settlement.utr, row.description)
    if not hit:
        return None
    return {
        "utr_exact": evidence.startswith("exact"),
        "amount_exact": amount_delta == 0,
        "date_exact": date_slip == 0,
        "name_close": _name_similarity(settlement.merchant_id, row.description) >= NAME_CLOSE_THRESHOLD,
    }


def fit_weights(
    settlements: list[SettlementRow],
    bank_rows: list[BankRow],
    truth: dict[str, str],
) -> MatchWeights:
    """Estimate m and u from a calibration batch whose true pairings are known.

    Every candidate pair inside the blocking window is used, labelled by the truth mapping. Pairs
    outside the window are excluded from both counts: they are not candidates for either scorer, and
    counting them as non-matches would inflate every weight by rewarding the blocking rather than the
    comparison.
    """
    agree_match = {f: 0 for f in FIELDS}
    agree_non_match = {f: 0 for f in FIELDS}
    n_matches = n_non_matches = 0

    for settlement in settlements:
        true_row_id = truth.get(settlement.settlement_id)
        for row in bank_rows:
            comparisons = compare(settlement, row)
            if comparisons is None:
                continue
            is_match = row.bank_row_id == true_row_id
            if is_match:
                n_matches += 1
            else:
                n_non_matches += 1
            for field, agreed in comparisons.items():
                if agreed:
                    if is_match:
                        agree_match[field] += 1
                    else:
                        agree_non_match[field] += 1

    fields: dict[str, FieldWeights] = {}
    for field in FIELDS:
        m = _clamp(agree_match[field] / n_matches if n_matches else 0.0)
        u = _clamp(agree_non_match[field] / n_non_matches if n_non_matches else 0.0)
        fields[field] = FieldWeights(
            m=round(m, 6),
            u=round(u, 6),
            agree_weight=round(math.log2(m / u), 4),
            disagree_weight=round(math.log2((1.0 - m) / (1.0 - u)), 4),
        )
    return MatchWeights(fields=fields, n_matches=n_matches, n_non_matches=n_non_matches)


def _clamp(p: float) -> float:
    return min(max(p, _EPSILON), 1.0 - _EPSILON)


def match_all_weighted(
    settlements: list[SettlementRow],
    bank_rows: list[BankRow],
    weights: MatchWeights,
) -> dict[str, "MatchResult"]:
    """Score every settlement with estimated weights, returning what `match_all` returns.

    Same shape so the evidence script can score this column with the identical function it uses for
    every other one. No cycle-reference term: this column exists to test whether the STRUCTURED
    fields can be weighted better, and adding a reader would confound that with the reading result.
    """
    from app.resolver.entity_resolution import MatchCandidate, MatchResult, _name_similarity, _utr_match

    out: dict[str, MatchResult] = {}
    for settlement in settlements:
        candidates: list[MatchCandidate] = []
        for row in bank_rows:
            comparisons = compare(settlement, row)
            if comparisons is None:
                continue
            _, evidence = _utr_match(settlement.utr, row.description)
            candidates.append(
                MatchCandidate(
                    bank_row_id=row.bank_row_id,
                    score=round(weights.score(comparisons), 4),
                    utr_evidence=evidence,
                    amount_delta=row.credit_amount - settlement.amount,
                    date_slip_days=(row.value_date.date() - settlement.value_date.date()).days,
                    name_similarity=round(_name_similarity(settlement.merchant_id, row.description), 4),
                    cycle_agrees=None,
                )
            )
        if not candidates:
            out[settlement.settlement_id] = MatchResult(
                settlement_id=settlement.settlement_id, status="UNMATCHED", candidates=[], tied_at_top=0
            )
            continue
        candidates.sort(key=lambda c: (-c.score, c.bank_row_id))
        top = candidates[0].score
        tied = sum(1 for c in candidates if c.score == top)
        out[settlement.settlement_id] = MatchResult(
            settlement_id=settlement.settlement_id,
            status="RESOLVED" if tied == 1 else "UNDER_DETERMINED",
            candidates=candidates,
            tied_at_top=tied,
        )
    return out
