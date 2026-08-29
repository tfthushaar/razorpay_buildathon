"""The cause taxonomy: what can actually move a settlement's net amount, and how each one is cited.

A settlement delta is rarely one thing. Real settlement arithmetic is compositional -- a fee charged
at the wrong contracted rate, plus a partial refund applied in the same cycle, plus a rolling reserve
withheld, plus FX rounding, all landing in one net number. This module names those components and,
critically, fixes the *citation format* each one must use.

The citation format is what makes a model's output verifiable rather than merely plausible. A
decomposition is not accepted because it reads well; it is accepted because (a) its components sum to
the observed delta within tolerance and (b) every `evidence_ref` resolves to a real object in the
batch whose actual properties support the amount claimed against it. Both are hard deterministic
checks that no amount of confident prose gets past. See app/resolver/verifier.py.

Evidence reference grammar (`kind:identifier[@qualifier]`):

    refund:rfnd_abc123              a real refund on THIS transaction's payment
    fee_schedule:upi@0.0040         a real rail + a fee rate, checked against the contracted schedule
    gst:0.18                        a real GST rate
    tds:0.0100                      a real TDS rate from the standard set
    reserve:0.0200                  a real rolling-reserve percentage from the standard set
    txn:order_xyz789                a real OTHER transaction in the same settlement batch
    narration:setl_abc123           a settlement that actually carries a free-text narration
    fx:INR                          this transaction's own currency

Anything else -- a malformed ref, a real-looking id that isn't in the batch, a rate that isn't in the
schedule, a refund belonging to a different payment -- fails grounding and the component is rejected.
"""

from typing import Literal

from pydantic import BaseModel

CauseType = Literal[
    "fee_rate_mismatch",
    "gst_on_fee_mismatch",
    "partial_refund",
    "duplicate_refund",
    "fx_rounding",
    "tds_deduction",
    "rolling_reserve",
    "netting_adjustment",
    "promotional_waiver",
]

CAUSE_TYPES: tuple[CauseType, ...] = (
    "fee_rate_mismatch",
    "gst_on_fee_mismatch",
    "partial_refund",
    "duplicate_refund",
    "fx_rounding",
    "tds_deduction",
    "rolling_reserve",
    "netting_adjustment",
    "promotional_waiver",
)
# `chargeback_hold` was in the first draft of this taxonomy and was removed rather than shipped
# unused: this project's synthetic data has no dispute record for such a component to cite, and a
# cause type with no groundable evidence_ref would be exactly the kind of decorative-but-unverifiable
# output the citation format exists to prevent. Adding it means adding real dispute objects first.

# Rates a real acquirer actually uses, so a candidate generator enumerating "what rate might they
# have applied instead" is enumerating a realistic set rather than an arbitrary one. These are the
# same constants the verifier grounds `tds:`/`reserve:` citations against, so a model cannot invent a
# 3.7% reserve and have it accepted.
STANDARD_TDS_RATES: tuple[float, ...] = (0.001, 0.005, 0.01, 0.02)
STANDARD_RESERVE_RATES: tuple[float, ...] = (0.005, 0.01, 0.02, 0.05)
STANDARD_GST_RATES: tuple[float, ...] = (0.0, 0.05, 0.12, 0.18, 0.28)

# Fee rates an acquirer plausibly applies by mistake -- a neighbouring rail's rate, a legacy rate, a
# blended rate. Kept separate from FEE_PCT (the contracted truth) on purpose: this is the set of
# WRONG rates worth hypothesising, and the resolver enumerates against it.
PLAUSIBLE_FEE_RATES: tuple[float, ...] = (0.0018, 0.0025, 0.0030, 0.0035, 0.0040, 0.0045, 0.0050, 0.0060, 0.0075, 0.0090)


class CauseCandidate(BaseModel):
    """One component of an explanation for a settlement delta.

    `amount` is signed and denominated in paise, and means "this cause's contribution to
    settlement_delta". Negative means it reduced what was settled (a fee, a refund, a withholding);
    positive means it increased it (a reversal, a credit).
    """

    cause: CauseType
    amount: int
    evidence_ref: str
    detail: str = ""

    def __hash__(self) -> int:  # lets a decomposition be deduplicated as a frozenset of components
        return hash((self.cause, self.amount, self.evidence_ref))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CauseCandidate):
            return NotImplemented
        return (self.cause, self.amount, self.evidence_ref) == (other.cause, other.amount, other.evidence_ref)


class Decomposition(BaseModel):
    """A complete candidate explanation: components that together account for the observed delta.

    `residual` is what the components fail to account for. A decomposition is *arithmetically valid*
    at tolerance t when abs(residual) <= t. Note that validity is not truth -- the whole point of the
    residual architecture is that many arithmetically valid decompositions can exist for one delta,
    and picking the true one among them is the part arithmetic cannot do.
    """

    components: list[CauseCandidate]
    observed_delta: int

    @property
    def total(self) -> int:
        return sum(c.amount for c in self.components)

    @property
    def residual(self) -> int:
        return self.observed_delta - self.total

    def is_valid_at(self, tolerance: int) -> bool:
        return abs(self.residual) <= tolerance

    def signature(self) -> frozenset[CauseCandidate]:
        """Identity of a decomposition for dedup purposes -- component order is meaningless."""
        return frozenset(self.components)

    def cause_multiset(self) -> tuple[tuple[CauseType, int], ...]:
        """The (cause, amount) pairs, sorted -- used to compare a proposal against ground truth
        without caring about which specific evidence_ref was cited for an interchangeable component."""
        return tuple(sorted((c.cause, c.amount) for c in self.components))


def decomposition_total(components: list[CauseCandidate]) -> int:
    return sum(c.amount for c in components)
