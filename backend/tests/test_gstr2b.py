"""Tests for the GSTR-2B tax-line matcher (app/erp/gstr2b.py) -- the "tax-line matcher" track
direction, completed. Real batch + real chains, same pattern test_fee_leak.py and test_journal.py
already use; the simulated GSTR-2B counterpart is the one deliberately-synthetic piece (see the
module docstring for why a real reconciliation needs an independent second side)."""

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.erp.gstr2b import generate_simulated_gstr2b, match_against_gstr2b, to_gstr2b_format


def _real_chains(main_n=120):
    main, _ = generate(seed=42, main_n=main_n, stress_n=0)
    return build_all_chains(main)


def test_to_gstr2b_format_includes_every_itc_eligible_transaction_exactly_once():
    chains = _real_chains()
    formatted = to_gstr2b_format(chains)
    expected_ids = {c.transaction_id for c in chains.values() if c.tax_amount > 0}

    entry_ids = [e["transaction_id"] for e in formatted["part_a_eligible_itc"]]
    assert set(entry_ids) == expected_ids
    assert len(entry_ids) == len(set(entry_ids)), "no transaction should appear twice"
    assert formatted["part_b_ineligible_itc"] == [], "our own books never record an ineligible ITC line by construction"
    assert formatted["total_eligible_itc"] == sum(c.tax_amount for c in chains.values() if c.tax_amount > 0)


def test_to_gstr2b_format_amounts_match_the_real_journal_tax_amount():
    chains = _real_chains()
    formatted = to_gstr2b_format(chains)
    by_txn = {c.transaction_id: c for c in chains.values()}
    for entry in formatted["part_a_eligible_itc"]:
        chain = by_txn[entry["transaction_id"]]
        assert entry["itc_amount"] == chain.tax_amount
        assert entry["taxable_value"] == chain.fee_amount


def test_simulated_gstr2b_is_reproducible_for_the_same_seed():
    chains = _real_chains()
    first = generate_simulated_gstr2b(chains, seed=7)
    second = generate_simulated_gstr2b(chains, seed=7)
    assert [e.model_dump() for e in first] == [e.model_dump() for e in second]


def test_simulated_gstr2b_never_exceeds_our_own_eligible_transaction_count():
    """The generator can omit a transaction (not-yet-filed) but never invents one that doesn't
    exist in our own books -- a real GSTR-2B can't report an invoice from a transaction that never
    happened on our side."""
    chains = _real_chains()
    our_eligible_ids = {c.transaction_id for c in chains.values() if c.tax_amount > 0}
    simulated = generate_simulated_gstr2b(chains, seed=7)
    simulated_ids = {e.transaction_id for e in simulated}
    assert simulated_ids.issubset(our_eligible_ids)
    assert len(simulated) < len(our_eligible_ids), "the not-yet-filed rate should omit at least one entry at this batch size"


def test_match_report_classifies_every_exception_kind_at_a_large_enough_batch():
    chains = _real_chains(main_n=300)  # large enough that all three low-probability mismatch kinds should fire at least once
    simulated = generate_simulated_gstr2b(chains, seed=7)
    report = match_against_gstr2b(chains, simulated)

    kinds_seen = {e.kind for e in report.exceptions}
    assert kinds_seen == {"missing_in_gstr2b", "amount_mismatch", "blocked_credit"}, f"expected all three exception kinds at n=300, got {kinds_seen}"

    total_eligible = sum(1 for c in chains.values() if c.tax_amount > 0)
    assert report.matched_count + len(report.exceptions) == total_eligible
    assert report.matched_count > 0, "the vast majority of transactions should still match cleanly"


def test_a_missing_entry_is_reported_as_an_exception_with_no_gstr2b_amount():
    chains = _real_chains()
    exception_free_simulation = generate_simulated_gstr2b(chains, seed=7)
    # force one specific real transaction to be "not yet filed" by removing it from the simulation
    txn_id = next(c.transaction_id for c in chains.values() if c.tax_amount > 0)
    forced_missing = [e for e in exception_free_simulation if e.transaction_id != txn_id]

    report = match_against_gstr2b(chains, forced_missing)
    missing = [e for e in report.exceptions if e.transaction_id == txn_id]
    assert missing and missing[0].kind == "missing_in_gstr2b"
    assert missing[0].gstr2b_itc_amount is None
    assert missing[0].our_itc_amount == chains[txn_id].tax_amount


def test_matched_itc_amount_only_counts_transactions_that_actually_matched():
    chains = _real_chains()
    simulated = generate_simulated_gstr2b(chains, seed=7)
    report = match_against_gstr2b(chains, simulated)

    exception_ids = {e.transaction_id for e in report.exceptions}
    expected_matched_amount = sum(c.tax_amount for c in chains.values() if c.tax_amount > 0 and c.transaction_id not in exception_ids)
    assert report.matched_itc_amount == expected_matched_amount
    assert report.exception_itc_amount == sum(e.our_itc_amount for e in report.exceptions)
