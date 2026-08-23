"""Naive baseline reconciler (spec §6.7).

Deliberately dumb: pure amount equality between the ledger's expected amount and the
settlement's actual amount. No chain logic, no date/SLA awareness, no LLM, no categories,
no confidence, no reasoning — this is what most flat reconciliation tooling actually does, and
it's the thing the real system is measured against for lift.

Its blind spots are the point, not a bug: it has zero tolerance for FX rounding noise (flags
harmless drift as a "mismatch"), and it has no notion of settlement timing at all, so a
transaction that's stuck well beyond its rail's normal SLA window still reads as "clean" as
long as the eventual amount matches — a silent false negative a real finance controller would
care about.
"""

from pydantic import BaseModel

from app.chain.builder import CausalChain


class BaselineResult(BaseModel):
    transaction_id: str
    clean: bool
    ledger_gap: int


def run_naive_baseline(chains: dict[str, CausalChain]) -> dict[str, BaselineResult]:
    return {
        txn_id: BaselineResult(transaction_id=txn_id, clean=(chain.ledger_gap == 0), ledger_gap=chain.ledger_gap)
        for txn_id, chain in chains.items()
    }
