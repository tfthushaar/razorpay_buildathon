"""Matching a settlement to the bank row that actually paid it — Layer 0 for the three-source problem.

Same architecture as the decomposition resolver, applied to a completely different failure: not "the
arithmetic has several answers" but "the *join* has several answers". A settlement and a bank credit
share no reliable key, so matching them means scoring candidates on the evidence that survives the
bank's mangling — a truncated or prefixed UTR, an amount that may have been rounded, a value date
that may have slipped, a merchant name rendered in some bank's house style.

    RESOLVED          exactly one bank row is a plausible match
    UNDER_DETERMINED  two or more are, and the scoring cannot separate them
    UNMATCHED         none is

The point of running this alongside the decomposition resolver is that the residual argument should
not depend on one hand-built task. If under-determination only ever showed up in compound settlement
arithmetic, a reader would be right to suspect the arithmetic was built to produce it. Entity
resolution is a different problem with different data and a different rule, and it produces the same
structure — which is evidence that the structure is a property of reconciliation rather than of this
generator.

The matcher below is written to WIN, like every other baseline in this project. It does not do a naive
exact join and declare defeat: it extracts UTR-looking tokens out of free text, tries suffix matching
at every truncation length the generator uses, tolerates the paise rounding the ERP applies and the
date slip a weekend causes, and scores merchant-name similarity across abbreviation styles. Where it
still cannot decide is reported as under-determination rather than as failure.
"""

import re
from typing import Literal

from pydantic import BaseModel

from app.data_gen.three_source import BankRow, SettlementRow

MatchStatus = Literal["RESOLVED", "UNDER_DETERMINED", "UNMATCHED"]

# Digit runs long enough to be a payment reference rather than an amount or a date fragment. Six is
# the shortest truncation the generator produces, so anything shorter is noise by construction.
_TOKEN = re.compile(r"\d{6,}")

# How far apart a settlement and a bank credit can be and still be the same payout. Both are set from
# what the generator actually does (up to 3 days late, up to 100 paise of rounding) rather than being
# tuned -- a matcher given a window wider than reality would manufacture ambiguity, and one given a
# narrower window would manufacture failure.
MAX_DATE_SLIP_DAYS = 3
MAX_AMOUNT_SLIP_PAISE = 100


class MatchCandidate(BaseModel):
    bank_row_id: str
    score: float
    utr_evidence: str
    amount_delta: int
    date_slip_days: int
    name_similarity: float
    cycle_agrees: bool | None = None  # True / False / None for agrees / disagrees / not stated


class MatchResult(BaseModel):
    settlement_id: str
    status: MatchStatus
    # EVERY bank row that passed the hard filters, best-scoring first -- not only the winners. Keeping
    # the losers is what lets "the rule picked the wrong row" be distinguished from "the right row was
    # never reachable", which are different failures with different fixes. An earlier version returned
    # only the top tie and made the two indistinguishable, which is how 10 ordinary ranking errors
    # briefly looked like the filter discarding the truth.
    candidates: list[MatchCandidate]
    tied_at_top: int = 1

    @property
    def ambiguity(self) -> int:
        return self.tied_at_top

    @property
    def chance_baseline(self) -> float:
        if self.status == "RESOLVED":
            return 1.0
        if self.status == "UNMATCHED" or not self.candidates:
            return 0.0
        return 1.0 / self.tied_at_top

    @property
    def tied(self) -> list[MatchCandidate]:
        return self.candidates[: self.tied_at_top]

    def best(self) -> MatchCandidate | None:
        return self.candidates[0] if self.candidates else None

    def reachable(self, bank_row_id: str) -> bool:
        """Whether a given row was scored at all -- the ceiling any chooser downstream is subject to."""
        return any(c.bank_row_id == bank_row_id for c in self.candidates)


def _name_similarity(merchant_id: str, description: str) -> float:
    """How much of the merchant's name survives into the bank's description.

    Character-bigram overlap rather than exact matching, because the whole difficulty is that every
    bank abbreviates differently -- 'merchant_003' can arrive as 'MERCHANT 003', 'MERCHANT003',
    'M003', 'Merchant-003' or 'MRCHNT003', and an exact comparison rejects four of those five.
    """
    a = merchant_id.upper().replace("_", "")
    b = re.sub(r"[^A-Z0-9]", "", description.upper())
    if not a or not b:
        return 0.0
    grams = {a[i : i + 2] for i in range(len(a) - 1)}
    if not grams:
        return 0.0
    return sum(1 for g in grams if g in b) / len(grams)


def _utr_match(settlement_utr: str, description: str) -> tuple[bool, str]:
    """Whether any reference token in the bank text is consistent with this settlement's UTR.

    Tries the whole UTR first, then every suffix length the generator truncates to. Suffix matching
    is what makes the problem interesting: it recovers the genuinely-truncated cases, and in doing so
    it stops being a unique key, because two different UTRs can share a tail.
    """
    for token in _TOKEN.findall(description):
        if token == settlement_utr:
            return True, f"exact:{token}"
        for keep in (8, 7, 6):
            if len(token) >= keep and settlement_utr.endswith(token[-keep:]):
                return True, f"suffix{keep}:{token}"
    return False, ""


# Best-effort extraction of a settlement-cycle reference out of bank free text. Written to win: it
# covers the canonical form, the separator-stripped form, the lowercased form, and the "cyc D of
# 2026-03-13" inversion. It will still miss house styles it has never seen -- which is the same
# authorship limit this project measured on remittance advice, reached here from a different
# direction. What it buys is measured (scripts/generate_three_source_evidence.py) rather than assumed.
_CYCLE_PATTERNS = (
    re.compile(r"C(\d{4}-\d{2}-\d{2})-([A-D])", re.I),
    re.compile(r"C(\d{8})([A-D])", re.I),
    re.compile(r"cyc\w*\s+([A-D])\s+of\s+(\d{4}-\d{2}-\d{2})", re.I),
    re.compile(r"batch\s+c?(\d{4}-\d{2}-\d{2})-([A-D])", re.I),
)


def _cycle_agrees(cycle_ref: str, description: str) -> bool | None:
    """True / False / None for agrees / disagrees / no cycle found in the text.

    None matters as much as the other two: about a third of banks carry no cycle reference at all, so
    absence is not evidence against a match and must not be scored as though it were."""
    if not cycle_ref:
        return None
    want_date, want_slot = cycle_ref.lstrip("C").rsplit("-", 1)
    for pattern in _CYCLE_PATTERNS:
        m = pattern.search(description)
        if not m:
            continue
        a, b = m.group(1), m.group(2)
        # the "cyc D of 2026-03-13" form puts the slot first
        date_part, slot_part = (b, a) if len(b) >= 8 else (a, b)
        date_part = date_part.replace("-", "")
        return date_part == want_date.replace("-", "") and slot_part.upper() == want_slot.upper()
    return None


def match_settlement(
    settlement: SettlementRow,
    bank_rows: list[BankRow],
    use_cycle_ref: bool = True,
    cycle_reader=None,
) -> MatchResult:
    """`cycle_reader(cycle_ref, description) -> bool | None` swaps out the regex cycle parser for
    anything else with the same contract (app/resolver/cycle_reader.py provides a model-backed one).
    Everything else -- filters, weights, tie-breaking -- is held identical, so a difference between
    two runs is attributable to the reading step alone."""
    candidates: list[MatchCandidate] = []

    for row in bank_rows:
        amount_delta = row.credit_amount - settlement.amount
        if abs(amount_delta) > MAX_AMOUNT_SLIP_PAISE:
            continue
        date_slip = (row.value_date.date() - settlement.value_date.date()).days
        if not -MAX_DATE_SLIP_DAYS <= date_slip <= MAX_DATE_SLIP_DAYS:
            continue
        hit, evidence = _utr_match(settlement.utr, row.description)
        if not hit:
            continue
        similarity = _name_similarity(settlement.merchant_id, row.description)

        # Deliberately coarse. A finer-grained score would break more ties, but it would be breaking
        # them on differences the data does not actually support -- a settlement two days late is not
        # meaningfully less likely than one a day late, and pretending otherwise converts honest
        # under-determination into a confident wrong answer. See docs/RESULTS.md.
        score = (
            (2.0 if evidence.startswith("exact") else 1.0)
            + (1.0 if amount_delta == 0 else 0.0)
            + (0.5 if date_slip == 0 else 0.0)
            + similarity
        )
        reader = cycle_reader or _cycle_agrees
        cycle = reader(settlement.cycle_ref, row.description) if use_cycle_ref else None
        if cycle is True:
            score += 3.0  # a matching cycle is the strongest single signal available
        elif cycle is False:
            score -= 3.0  # an explicitly DIFFERENT cycle is near-conclusive against
        candidates.append(
            MatchCandidate(
                bank_row_id=row.bank_row_id,
                score=round(score, 4),
                utr_evidence=evidence,
                amount_delta=amount_delta,
                date_slip_days=date_slip,
                name_similarity=round(similarity, 4),
                cycle_agrees=cycle,
            )
        )

    if not candidates:
        return MatchResult(settlement_id=settlement.settlement_id, status="UNMATCHED", candidates=[], tied_at_top=0)

    candidates.sort(key=lambda c: (-c.score, c.bank_row_id))
    top = candidates[0].score
    tied_at_top = sum(1 for c in candidates if c.score == top)
    status: MatchStatus = "RESOLVED" if tied_at_top == 1 else "UNDER_DETERMINED"
    return MatchResult(
        settlement_id=settlement.settlement_id,
        status=status,
        candidates=candidates,
        tied_at_top=tied_at_top,
    )


def match_all(
    settlements: list[SettlementRow], bank_rows: list[BankRow], use_cycle_ref: bool = True, cycle_reader=None
) -> dict[str, MatchResult]:
    return {
        s.settlement_id: match_settlement(s, bank_rows, use_cycle_ref=use_cycle_ref, cycle_reader=cycle_reader)
        for s in settlements
    }
