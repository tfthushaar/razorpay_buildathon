"""Deterministic verification of a proposed decomposition.

A label is a claim you can only check against an answer key. A decomposition is a claim you can check
against the data itself, and that difference is the entire reason this project stopped asking the
model for a category and started asking it for an attribution. Two hard checks, neither of which any
amount of confident prose gets past:

  ARITHMETIC   the components must sum to the observed delta within tolerance
  GROUNDING    every component's `evidence_ref` must resolve to a real object in this batch, AND
               that object's actual properties must support the amount claimed against it

The second check is the one that matters, and it is stricter than it first sounds. It is not "does
this refund id exist" -- it is "does this refund id exist, on THIS payment, for THIS amount". A model
can cite a real refund from a different payment, or cite a real refund and claim an amount it does
not have, and both are caught here. That closes the failure this project measured repeatedly in the
multi-way netting experiments: a confident narrative citing real-looking identifiers whose numbers
never actually added up.

What a failed check produces is a SPECIFIC complaint, not a rejection -- `failure_feedback()` names
which component failed and why, and app/narrator/attribution.py hands that back to the model for
another attempt. Confidence therefore stops being a number the model asserts about itself and
becomes a fact about what happened: how many verification rounds its answer survived.
"""

import re

from pydantic import BaseModel

from app.chain.builder import CausalChain
from app.data_gen.fee_schedule import FEE_PCT, GST_RATE
from app.resolver.causes import (
    PLAUSIBLE_FEE_RATES,
    STANDARD_GST_RATES,
    STANDARD_RESERVE_RATES,
    STANDARD_TDS_RATES,
    CauseCandidate,
)

_REF = re.compile(r"^(?P<kind>[a-z_]+):(?P<ident>[^@]+)(?:@(?P<qual>.+))?$")

# Percentage-derived amounts are recomputed here and compared against what the model claimed. An
# exact equality would fail on a legitimate half-paise rounding difference between the generator's
# round() and a recomputation, so citations are allowed this much slack -- small enough that it
# cannot launder a wrong rate into a right one (the gap between adjacent standard rates is orders of
# magnitude larger), large enough that correct arithmetic is never rejected on a rounding artefact.
_GROUNDING_SLACK = 2


class ComponentVerdict(BaseModel):
    component: CauseCandidate
    grounded: bool
    reason: str


class VerificationResult(BaseModel):
    transaction_id: str
    observed_delta: int
    claimed_total: int
    residual: int
    tolerance: int
    sums: bool
    verdicts: list[ComponentVerdict]

    @property
    def all_grounded(self) -> bool:
        return all(v.grounded for v in self.verdicts)

    @property
    def passed(self) -> bool:
        return self.sums and self.all_grounded and bool(self.verdicts)

    def failure_feedback(self) -> str:
        """A specific, actionable complaint -- what to hand back to the model for another attempt.

        Deliberately says what is wrong and never what the answer is. Telling the model the true
        residual is fine (it can read the delta itself); telling it which cause to pick instead would
        make the verification loop a leak of the answer key rather than a check on the model, and
        every accuracy number downstream would be measuring the loop, not the model."""
        problems: list[str] = []
        for v in self.verdicts:
            if not v.grounded:
                problems.append(f"- component {v.component.cause} citing {v.component.evidence_ref!r}: {v.reason}")
        if not self.sums:
            problems.append(
                f"- the components you gave sum to {self.claimed_total}, but the observed delta is "
                f"{self.observed_delta}. They are off by {self.residual}, which exceeds the tolerance "
                f"of {self.tolerance}."
            )
        if not self.verdicts:
            problems.append("- no components were given at all; an empty decomposition explains nothing.")
        return "\n".join(problems)


def _fee_base(chain: CausalChain) -> int:
    return chain.hops[0].actual if chain.hops else 0


def _close(a: int, b: int) -> bool:
    return abs(a - b) <= _GROUNDING_SLACK


def _ground_component(c: CauseCandidate, chain: CausalChain, context) -> tuple[bool, str]:
    m = _REF.match(c.evidence_ref or "")
    if not m:
        return False, "evidence_ref is not in the required 'kind:identifier[@qualifier]' form"
    kind, ident, qual = m.group("kind"), m.group("ident"), m.group("qual")
    base = _fee_base(chain)

    if kind == "refund":
        refunds = getattr(context, "refund_objects_by_payment", {}).get(chain.payment_id, [])
        match = next((amt for rid, amt in refunds if rid == ident), None)
        if match is None:
            return False, f"no refund {ident!r} exists on this transaction's payment ({chain.payment_id})"
        if not _close(abs(c.amount), match):
            return False, f"refund {ident!r} is for {match}, but this component claims {abs(c.amount)}"
        return True, "refund exists on this payment for the claimed amount"

    if kind == "fee_schedule":
        if ident != chain.rail:
            return False, f"cites rail {ident!r} but this transaction settled on {chain.rail!r}"
        try:
            rate = float(qual) if qual else None
        except ValueError:
            return False, f"fee rate {qual!r} is not a number"
        if rate is None or not any(abs(rate - r) < 1e-9 for r in (*PLAUSIBLE_FEE_RATES, FEE_PCT[chain.rail])):
            return False, f"{rate} is not a rate this acquirer uses for {chain.rail}"
        expected = round(base * FEE_PCT[chain.rail]) - round(base * rate)
        if not _close(c.amount, expected):
            return False, f"a fee applied at {rate} moves the delta by {expected}, not {c.amount}"
        return True, f"rate is real for {chain.rail} and the amount recomputes correctly"

    if kind == "gst":
        try:
            rate = float(ident)
        except ValueError:
            return False, f"GST rate {ident!r} is not a number"
        if not any(abs(rate - r) < 1e-9 for r in STANDARD_GST_RATES):
            return False, f"{rate} is not a real GST slab"
        on_fee = round(chain.fee_amount * GST_RATE) - round(chain.fee_amount * rate)
        on_gross = round(chain.fee_amount * GST_RATE) - round(base * GST_RATE)
        if not (_close(c.amount, on_fee) or _close(c.amount, on_gross)):
            return False, f"GST at {rate} moves the delta by {on_fee} (or {on_gross} if computed on the gross), not {c.amount}"
        return True, "GST rate is a real slab and the amount recomputes correctly"

    for kind_name, rates in (("tds", STANDARD_TDS_RATES), ("reserve", STANDARD_RESERVE_RATES)):
        if kind == kind_name:
            try:
                rate = float(ident)
            except ValueError:
                return False, f"{kind_name} rate {ident!r} is not a number"
            if not any(abs(rate - r) < 1e-9 for r in rates):
                return False, f"{rate} is not a standard {kind_name} rate"
            expected = -round(base * rate)
            if not _close(c.amount, expected):
                return False, f"{kind_name} at {rate} of {base} is {expected}, not {c.amount}"
            return True, f"{kind_name} rate is standard and the amount recomputes correctly"

    if kind == "txn":
        chains = getattr(context, "chains", {})
        other = chains.get(ident)
        if other is None:
            return False, f"no transaction {ident!r} exists in this batch"
        same_batch = other.settlement_batch_id == chain.settlement_batch_id
        if not same_batch:
            return False, f"{ident!r} settled in batch {other.settlement_batch_id!r}, not this one — it cannot net against this transaction"
        if not _close(c.amount, -other.settlement_delta):
            return False, f"{ident!r} has delta {other.settlement_delta}, so netting against it contributes {-other.settlement_delta}, not {c.amount}"
        return True, "real transaction in the same settlement batch with the offsetting delta claimed"

    if kind == "narration":
        if ident != chain.settlement_id:
            return False, f"cites settlement {ident!r} but this transaction settled as {chain.settlement_id}"
        if not getattr(chain, "bank_narration", None):
            return False, "this settlement carries no narration text, so nothing there can support a waiver"
        if not _close(c.amount, chain.fee_amount + chain.tax_amount):
            return False, f"a full fee waiver is worth {chain.fee_amount + chain.tax_amount} here, not {c.amount}"
        return True, "settlement carries real narration text and the waiver amount matches fee + tax"

    if kind == "fx":
        if ident != chain.currency:
            return False, f"cites currency {ident!r} but this transaction is in {chain.currency}"
        if abs(c.amount) > 3:
            return False, f"{c.amount} is far too large to be conversion rounding"
        return True, "currency matches and the amount is rounding-scale"

    return False, f"{kind!r} is not a kind of evidence this system can check"


def verify_decomposition(
    chain: CausalChain, context, components: list[CauseCandidate], tolerance: int = 10
) -> VerificationResult:
    claimed_total = sum(c.amount for c in components)
    residual = chain.settlement_delta - claimed_total
    verdicts = []
    for c in components:
        grounded, reason = _ground_component(c, chain, context)
        verdicts.append(ComponentVerdict(component=c, grounded=grounded, reason=reason))
    return VerificationResult(
        transaction_id=chain.transaction_id,
        observed_delta=chain.settlement_delta,
        claimed_total=claimed_total,
        residual=residual,
        tolerance=tolerance,
        sums=abs(residual) <= tolerance,
        verdicts=verdicts,
    )
