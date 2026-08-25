"""Matching engine (spec §6.3).

Pass 1: exact match (ledger amount == settlement amount, settled within the rail's normal SLA
window). Pass 2 (unresolved only): a *deterministic* structured diff — is the delta explained by
a known fee, a refund on record, FX rounding noise, or a timing lag within tolerance? Only what
Pass 2 cannot explain gets sent to the agentic narrator (spec §6.4).

This ordering is a deliberate cost decision, not just an engineering one: in the main batch,
Pass 1 + Pass 2 resolve clean_match, fee_deduction, partial_refund, timing_lag, and
currency_rounding entirely without an LLM call — only duplicate_refund, netting_trap, and
genuine_error (the "genuinely hard" ~15%) ever reach the narrator. See BUILD_LOG.md.
"""

from typing import Literal

from pydantic import BaseModel

from app.chain.builder import ROUNDING_EPSILON, CausalChain

Resolution = Literal["clean_pass1", "auto_resolved_deterministic", "needs_narration"]

Category = Literal[
    "clean_match",
    "timing_lag",
    "fee_deduction",
    "partial_refund",
    "duplicate_refund",
    "netting_trap",
    "currency_rounding",
    "genuine_error",
]


class MatchResult(BaseModel):
    transaction_id: str
    resolution: Resolution
    category: Category | None = None
    confidence: float | None = None
    reasoning: str | None = None
    chain: CausalChain


def _resolved(chain: CausalChain, category: Category, confidence: float, reasoning: str) -> MatchResult:
    resolution: Resolution = "clean_pass1" if category == "clean_match" and confidence >= 0.99 else "auto_resolved_deterministic"
    return MatchResult(
        transaction_id=chain.transaction_id,
        resolution=resolution,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        chain=chain,
    )


def _needs_narration(chain: CausalChain) -> MatchResult:
    return MatchResult(transaction_id=chain.transaction_id, resolution="needs_narration", chain=chain)


def _rupees(paise: int) -> str:
    return f"Rs.{paise / 100:,.2f}"


def _clean_match_result(chain: CausalChain) -> MatchResult:
    return _resolved(
        chain,
        "clean_match",
        1.0,
        f"Ledger and settlement match exactly ({_rupees(chain.actual_settled_amount)}); "
        f"settled in {chain.sla_actual_days}d, within the normal window for {chain.rail}.",
    )


def run_pass1(chain: CausalChain) -> MatchResult | None:
    if chain.ledger_gap == 0 and chain.within_sla:
        return _clean_match_result(chain)
    return None


def run_pass2(chain: CausalChain) -> MatchResult:
    delta = chain.settlement_delta

    if delta != 0 and abs(delta) <= ROUNDING_EPSILON:
        return _resolved(
            chain,
            "currency_rounding",
            0.95,
            f"Settlement differs from the computed post-fee/refund amount by only {delta} units "
            f"({chain.currency}) — consistent with FX rounding, not a real gap.",
        )

    if delta == 0:
        # settlement is fully explained by the fee/tax/refunds already on record — any remaining
        # gap is purely the merchant's ledger not having been updated yet.
        gap = chain.ledger_gap
        if gap > 0 and gap == chain.fee_amount + chain.tax_amount:
            return _resolved(
                chain,
                "fee_deduction",
                0.97,
                f"{_rupees(chain.computed_expected_settlement + chain.fee_amount + chain.tax_amount)} captured -> "
                f"{_rupees(chain.fee_amount)} fee + {_rupees(chain.tax_amount)} tax -> "
                f"{_rupees(chain.actual_settled_amount)} settled. Ledger hasn't been updated for the deduction yet.",
            )
        if chain.refund_total > 0 and gap == chain.refund_total:
            return _resolved(
                chain,
                "partial_refund",
                0.96,
                f"Settlement is short of the ledger's expectation by exactly the refund amount "
                f"({_rupees(chain.refund_total)}); ledger not yet updated for the refund.",
            )
        if gap == 0 and not chain.within_sla:
            return _resolved(
                chain,
                "timing_lag",
                0.9,
                f"Amounts match exactly at every hop; settlement landed at {chain.sla_actual_days}d vs "
                f"a nominal {chain.sla_nominal_days}d for {chain.rail} — outside normal variance, but not a money problem.",
            )
        if gap == 0 and chain.within_sla:
            return _resolved(chain, "clean_match", 0.99, "Fully consistent chain on closer inspection.")
        return _needs_narration(chain)

    return _needs_narration(chain)


def run_matching_engine(
    chains: dict[str, CausalChain], merkle_clean_ids: frozenset[str] | None = None
) -> dict[str, MatchResult]:
    """`merkle_clean_ids` (optional): transaction_ids the Merkle pre-filter (matching/
    merkle_prefilter.py) has already proven satisfy Pass 1's own clean_match condition
    (ledger_gap == 0 and within_sla), from a cheaper two-view comparison than building the
    chain-aware check would need. For those ids, skip straight to the same clean_match result
    Pass 1 would have produced — see test_merkle_prefilter.py's parity test for why this never
    changes the result, only how it's reached."""
    results: dict[str, MatchResult] = {}
    for txn_id, chain in chains.items():
        if merkle_clean_ids is not None and txn_id in merkle_clean_ids:
            result = _clean_match_result(chain)
        else:
            result = run_pass1(chain)
            if result is None:
                result = run_pass2(chain)
        results[txn_id] = result
    return results
