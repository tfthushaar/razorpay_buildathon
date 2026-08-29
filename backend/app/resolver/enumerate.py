"""Layer 0's best deterministic effort: build every cause a delta could plausibly have, then find
every combination of them that actually accounts for it.

This module has to be genuinely strong, because the entire residual argument rests on it. If the
candidate pool were thin or the search shallow, "the resolver couldn't do it" would just mean "I
didn't try hard enough" -- and a reader would be right to say so. So the pool enumerates against the
real contracted fee schedule, the real refunds on the payment, the real other transactions in the
settlement batch, and the standard TDS/reserve/GST rate sets an acquirer actually uses; and the
search is exhaustive over subsets up to `max_components`, not a heuristic.

The one thing the search deliberately does NOT do is generate physically impossible combinations.
A transaction was settled at exactly one fee rate, so two different `fee_rate_mismatch` hypotheses
cannot both be true simultaneously; likewise one TDS rate, one reserve rate, one FX rounding. Without
that exclusivity rule the enumerator would happily return "charged at 0.30% AND at 0.45% AND at
0.60%" and every ambiguity count in this project would be inflated by combinations no one would ever
propose. Refunds and netting partners are genuinely repeatable, so they are exempt. This makes the
reported ambiguity counts conservative -- the honest direction to err in, since a smaller k makes the
model's job on the residual look *harder*, not easier.
"""

from itertools import combinations

from app.chain.builder import CausalChain
from app.data_gen.fee_schedule import FEE_PCT, GST_RATE
from app.resolver.causes import (
    PLAUSIBLE_FEE_RATES,
    STANDARD_GST_RATES,
    STANDARD_RESERVE_RATES,
    STANDARD_TDS_RATES,
    CauseCandidate,
    CauseType,
    Decomposition,
)

# Causes where exactly one instance can be physically true at a time (see module docstring).
_MUTUALLY_EXCLUSIVE: frozenset[CauseType] = frozenset(
    {"fee_rate_mismatch", "gst_on_fee_mismatch", "tds_deduction", "rolling_reserve", "fx_rounding", "promotional_waiver"}
)

# A hard ceiling on returned decompositions. A delta with thousands of valid explanations is still
# just "extremely under-determined" for reporting purposes, and enumerating all of them buys nothing
# but memory. When this trips, ResolverOutput.truncated is set so no downstream number silently
# treats a capped count as an exact one.
MAX_DECOMPOSITIONS = 5000


def build_candidate_pool(chain: CausalChain, context, include_netting: bool = True) -> list[CauseCandidate]:
    """Every cause that could plausibly have contributed to this transaction's settlement delta.

    `context` is a narrator ToolContext (duck-typed rather than imported, to keep this module free of
    a narrator dependency -- the resolver runs before and independently of any model).
    """
    pool: list[CauseCandidate] = []
    # hops[0] is order_to_capture, so hops[0].actual is the CAPTURED amount -- the base every
    # percentage withholding (fee, TDS, reserve) is actually computed against. An earlier version of
    # this line read hops[1].actual, which is post-FEE, and every percentage candidate in the pool
    # was consequently computed off a base that was already net of the fee. It looked fine and the
    # pool was still full of plausible numbers; it just never contained the true ones. Caught by
    # measuring how often the resolver recovers a KNOWN ground-truth decomposition (11/60) rather
    # than by anything the pool itself looked like -- see scripts/generate_residual_evidence.py,
    # which keeps that recovery check as a standing assertion for exactly this reason.
    base = chain.hops[0].actual if chain.hops else 0

    # --- fee charged at a rate other than the contracted one -------------------------------------
    contracted_rate = FEE_PCT.get(chain.rail, 0.0)
    contracted_fee = round(base * contracted_rate)
    for rate in PLAUSIBLE_FEE_RATES:
        if abs(rate - contracted_rate) < 1e-9:
            continue
        hypothetical_fee = round(base * rate)
        delta_contribution = contracted_fee - hypothetical_fee  # more fee charged => settled less
        if delta_contribution == 0:
            continue
        pool.append(
            CauseCandidate(
                cause="fee_rate_mismatch",
                amount=delta_contribution,
                evidence_ref=f"fee_schedule:{chain.rail}@{rate:.4f}",
                detail=f"fee applied at {rate * 100:.2f}% instead of the contracted {contracted_rate * 100:.2f}%",
            )
        )

    # --- GST computed at a non-standard rate, or on the wrong base --------------------------------
    correct_gst = round(chain.fee_amount * GST_RATE)
    for rate in STANDARD_GST_RATES:
        if abs(rate - GST_RATE) < 1e-9:
            continue
        hypothetical = round(chain.fee_amount * rate)
        contribution = correct_gst - hypothetical
        if contribution == 0:
            continue
        pool.append(
            CauseCandidate(
                cause="gst_on_fee_mismatch",
                amount=contribution,
                evidence_ref=f"gst:{rate:.2f}",
                detail=f"GST on fee computed at {rate * 100:.0f}% instead of {GST_RATE * 100:.0f}%",
            )
        )
    gst_on_gross = round(base * GST_RATE)
    if gst_on_gross != correct_gst:
        pool.append(
            CauseCandidate(
                cause="gst_on_fee_mismatch",
                amount=correct_gst - gst_on_gross,
                evidence_ref=f"gst:{GST_RATE:.2f}",
                detail="GST computed on the gross captured amount instead of on the fee",
            )
        )

    # --- refunds actually on record for this payment ----------------------------------------------
    for refund_id, amount in _refunds_for(chain, context):
        pool.append(
            CauseCandidate(
                cause="partial_refund",
                amount=-amount,
                evidence_ref=f"refund:{refund_id}",
                detail=f"refund {refund_id} applied against this settlement",
            )
        )
        pool.append(
            CauseCandidate(
                cause="duplicate_refund",
                amount=-amount,
                evidence_ref=f"refund:{refund_id}",
                detail=f"refund {refund_id} applied a SECOND time (already deducted upstream)",
            )
        )

    # --- withholdings an acquirer applies as a percentage of the captured amount -------------------
    for rate in STANDARD_TDS_RATES:
        amount = round(base * rate)
        if amount:
            pool.append(
                CauseCandidate(
                    cause="tds_deduction",
                    amount=-amount,
                    evidence_ref=f"tds:{rate:.4f}",
                    detail=f"TDS withheld at {rate * 100:.2f}% of captured amount",
                )
            )
    for rate in STANDARD_RESERVE_RATES:
        amount = round(base * rate)
        if amount:
            pool.append(
                CauseCandidate(
                    cause="rolling_reserve",
                    amount=-amount,
                    evidence_ref=f"reserve:{rate:.4f}",
                    detail=f"rolling reserve withheld at {rate * 100:.2f}% of captured amount",
                )
            )

    # --- small residual movers ---------------------------------------------------------------------
    for cents in (-3, -2, -1, 1, 2, 3):
        pool.append(
            CauseCandidate(
                cause="fx_rounding",
                amount=cents,
                evidence_ref=f"fx:{chain.currency}",
                detail=f"currency conversion rounding of {cents} paise",
            )
        )

    # --- another transaction in the same settlement batch offsetting this one ----------------------
    if include_netting:
        for other_id, other_delta in _batch_partners(chain, context):
            if other_delta == 0:
                continue
            pool.append(
                CauseCandidate(
                    cause="netting_adjustment",
                    amount=-other_delta,
                    evidence_ref=f"txn:{other_id}",
                    detail=f"netted against {other_id} (delta {other_delta})",
                )
            )

    # --- a waiver the settlement's own free text asserts --------------------------------------------
    if getattr(chain, "bank_narration", None):
        pool.append(
            CauseCandidate(
                cause="promotional_waiver",
                amount=chain.fee_amount + chain.tax_amount,
                evidence_ref=f"narration:{chain.settlement_id}",
                detail="settlement narration asserts a fee waiver applied this cycle",
            )
        )

    return pool


def _refunds_for(chain: CausalChain, context) -> list[tuple[str, int]]:
    """Refund (id, amount) pairs on this transaction's payment. Prefers a real refund-object map when
    the context carries one, and falls back to the chain's own recorded refund ids."""
    by_payment = getattr(context, "refund_objects_by_payment", None)
    if by_payment is not None:
        return [(r_id, amt) for r_id, amt in by_payment.get(chain.payment_id, [])]
    amounts = getattr(context, "refund_amounts_by_payment", {}).get(chain.payment_id, [])
    ids = chain.refund_ids or [f"unknown_{i}" for i in range(len(amounts))]
    return list(zip(ids, amounts))


def _batch_partners(chain: CausalChain, context) -> list[tuple[str, int]]:
    ids = getattr(context, "transaction_ids_by_settlement_batch", {}).get(chain.settlement_batch_id, [])
    chains = getattr(context, "chains", {})
    out = []
    for other_id in ids:
        if other_id == chain.transaction_id:
            continue
        other = chains.get(other_id)
        if other is not None:
            out.append((other_id, other.settlement_delta))
    return out


def _violates_exclusivity(combo: tuple[CauseCandidate, ...]) -> bool:
    seen: set[CauseType] = set()
    for c in combo:
        if c.cause in _MUTUALLY_EXCLUSIVE:
            if c.cause in seen:
                return True
            seen.add(c.cause)
    return False


def enumerate_decompositions(
    observed_delta: int,
    pool: list[CauseCandidate],
    tolerance: int = 0,
    max_components: int = 4,
    limit: int = MAX_DECOMPOSITIONS,
) -> tuple[list[Decomposition], bool]:
    """Every subset of `pool` of size 1..max_components summing to `observed_delta` within
    `tolerance`. Returns (decompositions, truncated).

    Exhaustive by construction, with the physical-exclusivity rule from the module docstring applied.
    Deduplicated on the component set, so two orderings of the same explanation count once.
    """
    found: list[Decomposition] = []
    seen: set[frozenset[CauseCandidate]] = set()
    truncated = False

    for size in range(1, max_components + 1):
        for combo in combinations(pool, size):
            total = sum(c.amount for c in combo)
            if abs(observed_delta - total) > tolerance:
                continue
            if _violates_exclusivity(combo):
                continue
            sig = frozenset(combo)
            if sig in seen:
                continue
            seen.add(sig)
            found.append(Decomposition(components=list(combo), observed_delta=observed_delta))
            if len(found) >= limit:
                return found, True

    return found, truncated
