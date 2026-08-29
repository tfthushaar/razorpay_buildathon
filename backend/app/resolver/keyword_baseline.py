"""The best rule I could write for the residual, kept as a standing comparator.

This exists because of a pattern this project kept repeating: a category would be introduced, the
model would score well on it, and then a rule written afterwards would match or beat it -- three
times (`netting_trap`, `multiway_netting_trap`, `narration_explained`). Each time the rule was
written *after* the claim was published, which is the wrong order. So the rule is written first now,
it is written to win, and it appears as its own column in every table where the model's value on the
residual is claimed. If it wins, that is the finding and it gets published as the finding.

What it does, which is genuinely the strongest cheap approach to this data:

  1. split the remittance advice into fragments on its separators
  2. work out which cause each fragment is about, by keyword
  3. decide whether the fragment ASSERTS that cause or denies/defers it, using a real negation-cue
     list -- not just keyword presence, which would be a strawman
  4. score every one of Layer 0's arithmetically-valid decompositions against the resulting set of
     asserted causes, and return the best-matching one

Step 3 is the part that makes this a fair fight. A rule that only checked whether "TDS" appears
anywhere would be trivially beaten by text containing "TDS NOT withheld", and beating that proves
nothing. This one reads the negation. Where it still loses is documented from the measurement rather
than predicted here: see docs/RESULTS.md.
"""

import re

from app.resolver.causes import Decomposition

CAUSE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fee_rate_mismatch": ("mdr", "chgs levied", "chgs ", "rate change", "charge"),
    "gst_on_fee_mismatch": ("gst", "tax on fee", "slab"),
    "duplicate_refund": ("rfnd", "refund", "redebit", "re-deduct", "second debit"),
    "tds_deduction": ("tds", "194o", "deduction at source", "deducted at source"),
    "rolling_reserve": ("rsv", "reserve", "resv", "hold"),
    "fx_rounding": ("fx", "conv rounding", "rounding"),
    "promotional_waiver": ("waiv", "promo", "exempt", "feeexempt"),
}

# Cues that a fragment mentioning a cause is saying it did NOT apply this cycle -- negated outright,
# scoped to a different period, or still only proposed. Assembled by reading the generator's own
# negative register (app/data_gen/generate.py `_CAUSE_PHRASES`) and generalising, which is the
# strongest position a rule author is realistically ever in: full sight of the phrasing.
NEGATION_CUES: tuple[str, ...] = (
    "not applied",
    "not withheld",
    "not re-deducted",
    "no second debit",
    "no hold",
    "no change",
    "no exemption",
    "no fx adj",
    "nil deduction",
    "denied",
    "cancelled",
    "suppressed",
    "released",
    "expired",
    "waived for this batch",
    "next cycle",
    "next settlement",
    "next fy",
    "to commence",
    "effective next",
    "pending",
    "proposed",
    "queried",
    "flagged",
    "already netted",
    "exemption cert",
    "lower deduction certificate",
    " not ",
    " no ",
)

_SPLIT = re.compile(r"\s*(?:\||;|/|--|,)\s*")


def _fragment_negated(fragment: str) -> bool:
    low = f" {fragment.lower()} "
    return any(cue in low for cue in NEGATION_CUES)


def read_advice(narration: str | None) -> dict[str, bool]:
    """cause -> True if the advice asserts it applied, False if it explicitly denies/defers it.

    Causes the text says nothing about are simply absent from the mapping -- distinct from being
    denied, and treated as such by the scorer below."""
    asserted: dict[str, bool] = {}
    if not narration:
        return asserted
    for fragment in _SPLIT.split(narration):
        if not fragment.strip():
            continue
        low = fragment.lower()
        negated = _fragment_negated(fragment)
        for cause, keywords in CAUSE_KEYWORDS.items():
            if any(k in low for k in keywords):
                # a fragment can key more than one cause ("partial waiver 50pct + GST adj"); an
                # assertion never downgrades to a denial, so a later positive mention wins
                asserted[cause] = asserted.get(cause, False) or not negated
    return asserted


def score_decomposition(decomposition: Decomposition, asserted: dict[str, bool]) -> int:
    """How well a candidate decomposition agrees with what the advice says.

    +2 for including a cause the advice asserts, -2 for including one it explicitly denies, and -1
    for omitting an asserted one. Absent-from-text causes score 0 either way, since the advice is
    known to be partial and punishing them would make the rule worse, not more honest."""
    present = {c.cause for c in decomposition.components}
    score = 0
    for cause, is_asserted in asserted.items():
        if is_asserted and cause in present:
            score += 2
        elif is_asserted and cause not in present:
            score -= 1
        elif not is_asserted and cause in present:
            score -= 2
    return score


def best_decomposition_by_advice(
    decompositions: list[Decomposition], narration: str | None
) -> tuple[Decomposition | None, int]:
    """The rule's actual answer: the highest-scoring valid decomposition, and how many tied with it.

    The tie count is returned rather than hidden because it is the rule's own honest statement of
    where it ran out -- on a tie it is guessing, and a results table that reported only the winner
    would be quietly crediting those guesses. Ties are broken by fewest components (a real, defensible
    parsimony preference), so the returned answer is deterministic."""
    if not decompositions:
        return None, 0
    asserted = read_advice(narration)
    scored = [(score_decomposition(d, asserted), -len(d.components), d) for d in decompositions]
    best = max(s for s, _, _ in scored)
    tied = [t for t in scored if t[0] == best]
    tied.sort(key=lambda t: t[1], reverse=True)
    return tied[0][2], len(tied)
