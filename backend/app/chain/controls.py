"""Data-integrity controls: defects the matching engine has no input to see.

The matching engine reconciles AMOUNTS through the causal chain. It never reads a settlement's
`settled_at` or its `utr`, so a whole class of real defect ties arithmetically and resolves clean.
Two of them were found by `scripts/generate_generalization_evidence.py` on its first run, against
shapes deliberately absent from the generator's taxonomy.

    DUPLICATE SETTLEMENT   `build_all_chains` keys settlements by payment_id in a dict
                           comprehension, so a payment settled inside two batches silently loses
                           one. The chain then ties perfectly against the survivor and the
                           reconciliation reports clean while the merchant has been paid twice.
                           That is real money and it was invisible.

    IMPOSSIBLE TIMING      A settlement dated before the capture it settles. The amount ties, so
                           nothing in the arithmetic notices that the money arrived before it
                           existed.

    RECYCLED REFERENCE     Two settlements carrying the same UTR. A payout reference is supposed to
                           identify one movement of money; when a bank recycles one, the evidence
                           looks right while pointing at a payout that already happened.

These are CONTROLS, not matching rules, and the distinction is the point. Tuning the matcher against
shapes the generalisation suite invented would destroy what that suite is for: it would stop
measuring generalisation and start measuring how fast I can special-case whatever it caught. A
control is different in kind -- it asserts an invariant that must hold of the data whatever the
matcher concludes, and it flags rather than resolves.

`stale_utr_reuse` was declared a blind spot in an earlier version of this file, on the argument that
a causal chain carries no UTR. That argument was right about the CHAIN and wrong about the batch: a
settlement carries a UTR, and "a payout reference identifies one payout" is an invariant that can be
checked without any matching heuristic at all. The blind spot was partly an artefact of the test --
the generator gave each of those settlements a fresh random UTR, which is not reuse -- and partly a
control that had not been written. Both are fixed.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.data_gen.schemas import SyntheticBatch


class ControlFinding(BaseModel):
    control: str  # "duplicate_settlement" | "impossible_timing" | "recycled_reference"
    transaction_id: str
    detail: str
    amount_at_risk: int  # smallest currency unit; the money the defect puts in question


def run_data_integrity_controls(batch: SyntheticBatch) -> list[ControlFinding]:
    """Every finding, in a stable order. Empty means the batch is internally consistent."""
    return sorted(
        _duplicate_settlements(batch) + _impossible_timing(batch) + _recycled_references(batch),
        key=lambda f: (f.control, f.transaction_id),
    )


def _duplicate_settlements(batch: SyntheticBatch) -> list[ControlFinding]:
    """One payment settled more than once. Which payout was the erroneous one is a question about
    the gateway's behaviour, not the statement's arithmetic, so this reports rather than resolves."""
    by_payment: dict[str, list] = {}
    for settlement in batch.settlements:
        by_payment.setdefault(settlement.payment_id, []).append(settlement)

    order_of_payment = {p.payment_id: p.order_id for p in batch.payments}
    findings = []
    for payment_id, settlements in by_payment.items():
        if len(settlements) < 2:
            continue
        extra = sorted(s.settlement_id for s in settlements)
        findings.append(
            ControlFinding(
                control="duplicate_settlement",
                transaction_id=order_of_payment.get(payment_id, payment_id),
                detail=f"payment settled {len(settlements)} times across batches: {', '.join(extra)}",
                # The duplicate payouts, not the legitimate first one.
                amount_at_risk=sum(s.settled_amount for s in settlements[1:]),
            )
        )
    return findings


def _impossible_timing(batch: SyntheticBatch) -> list[ControlFinding]:
    """A settlement dated before the capture it settles. Money cannot arrive before it exists."""
    captured_at = {p.payment_id: p.captured_at for p in batch.payments}
    order_of_payment = {p.payment_id: p.order_id for p in batch.payments}
    findings = []
    for settlement in batch.settlements:
        capture = captured_at.get(settlement.payment_id)
        if capture is None or settlement.settled_at >= capture:
            continue
        findings.append(
            ControlFinding(
                control="impossible_timing",
                transaction_id=order_of_payment.get(settlement.payment_id, settlement.payment_id),
                detail=f"settled {settlement.settled_at.isoformat()}, before its capture at {capture.isoformat()}",
                amount_at_risk=settlement.settled_amount,
            )
        )
    return findings


def _recycled_references(batch: SyntheticBatch) -> list[ControlFinding]:
    """The same UTR on more than one settlement.

    A payout reference is supposed to identify one movement of money. When a bank recycles one, every
    amount still ties and the reconciliation reports clean while two payouts claim the same evidence.
    Reported against every settlement sharing the reference, because which one is the impostor is a
    question for the bank rather than for this arithmetic.
    """
    by_utr: dict[str, list] = {}
    for settlement in batch.settlements:
        if settlement.utr:
            by_utr.setdefault(settlement.utr, []).append(settlement)

    order_of_payment = {p.payment_id: p.order_id for p in batch.payments}
    findings = []
    for utr, settlements in by_utr.items():
        if len(settlements) < 2:
            continue
        for settlement in settlements:
            findings.append(
                ControlFinding(
                    control="recycled_reference",
                    transaction_id=order_of_payment.get(settlement.payment_id, settlement.payment_id),
                    detail=f"UTR {utr} is claimed by {len(settlements)} settlements",
                    amount_at_risk=settlement.settled_amount,
                )
            )
    return findings
