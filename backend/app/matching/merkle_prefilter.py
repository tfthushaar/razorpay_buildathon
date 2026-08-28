"""Wires the Merkle-tree divergence pre-filter (merkle.py) into the live matching pipeline as an
opt-in "Pass 0", run before Pass 1 (a stretch goal).

Correctness invariant this module exists to protect: the pre-filter must NEVER change what a
transaction resolves to, only how cheaply the "provably clean" majority gets there. It identifies
exactly the subset Pass 1 (engine.py's run_pass1) already treats as clean_match --
`ledger_gap == 0 and within_sla` -- using two independent per-key views (the ledger's expected
amount vs. the settlement's actual amount, tagged with the same SLA verdict) instead of building a
full CausalChain and running Pass 1's own per-transaction check. Every transaction NOT in that
provably-clean set still goes through the identical, unmodified build_chain + run_pass1 +
run_pass2 (+ narration) pipeline -- see test_merkle_prefilter.py's parity test, which asserts the
filtered and unfiltered paths produce byte-identical MatchResults for every transaction.

Why the two views are tagged with the SLA verdict (`"{amount}|OK"` / `"{amount}|LATE"`) rather
than comparing bare amounts: ledger_gap == 0 alone isn't Pass 1's criterion -- a transaction can
have matching amounts but a blown SLA (timing_lag), which must NOT be fast-pathed as clean. Tagging
makes the two views compare equal iff BOTH conditions hold, so "identical" under Merkle means
exactly "Pass 1 would resolve this as clean_match" -- no separate SLA check needed afterward.

Real, measured value versus theoretical: in THIS project's in-memory implementation (order,
payment, refund, settlement, and ledger records are already loaded as Python objects, not fetched
from separate services), a CausalChain is already cheap to build, so the wall-clock saving here is
bounded by how much of that cost is avoidable at all -- see BUILD_LOG.md for the honest, measured
number, not an assumed one. The architectural pattern is what would matter more in a real system
where ledger and settlement data live in different services: the pre-filter would eliminate 90%+ of
the cross-service record fetches needed to build a transaction at all, not just skip a comparison
that was already cheap in-process.
"""

from dataclasses import dataclass

from app.data_gen.fee_schedule import SLA_TOLERANCE_DAYS
from app.data_gen.schemas import SyntheticBatch
from app.matching.merkle import MerkleComparator, MerkleDiffResult


@dataclass
class PrefilterResult:
    provably_clean_order_ids: frozenset[str]  # ledger_gap == 0 AND within_sla, per Merkle
    merkle_diff: MerkleDiffResult  # for reporting comparisons_made vs. brute_force_comparisons


def run_merkle_prefilter(batch: SyntheticBatch) -> PrefilterResult:
    """Two views over the same order_id key space: what the ledger expects, and what the
    settlement actually paid out (tagged with the SLA verdict). Keys where both views match are
    provably Pass 1's clean_match -- see module docstring for why the tagging is needed."""
    payments_by_order = {p.order_id: p for p in batch.payments}
    settlements_by_payment = {s.payment_id: s for s in batch.settlements}
    ledger_by_order = {l.order_id: l for l in batch.ledger_entries}

    order_ids = [o.order_id for o in batch.orders]
    ledger_view: dict[str, str] = {}
    settlement_view: dict[str, str] = {}

    for order_id in order_ids:
        payment = payments_by_order[order_id]
        settlement = settlements_by_payment[payment.payment_id]
        ledger = ledger_by_order[order_id]

        tolerance = SLA_TOLERANCE_DAYS[settlement.rail]
        sla_tag = "OK" if settlement.sla_days <= tolerance else "LATE"

        ledger_view[order_id] = f"{ledger.expected_amount}|OK"
        settlement_view[order_id] = f"{settlement.settled_amount}|{sla_tag}"

    comparator = MerkleComparator(keys=order_ids)
    diff = comparator.diff(ledger_view, settlement_view)
    diverging = set(diff.diverging_keys)
    provably_clean = frozenset(oid for oid in order_ids if oid not in diverging)

    return PrefilterResult(provably_clean_order_ids=provably_clean, merkle_diff=diff)
