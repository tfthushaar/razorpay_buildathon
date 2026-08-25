"""Tests for the ERP journal line generator (app/erp/journal.py) and its export formats
(app/erp/exporters.py).

The one property that matters most: every generated journal entry must balance (debits == credits)
by construction, for every category the generator produces, not just clean ones -- a real
accountant importing an unbalanced entry is the single worst failure mode this module could have.
Checked across all 8 categories from a real generated batch, not just hand-picked examples.
"""

import xml.etree.ElementTree as ET
from csv import DictReader
from io import StringIO

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.erp.exporters import to_generic_csv, to_tally_xml, to_zoho_books_csv
from app.erp.journal import generate_journal_entries, generate_journal_entry, tds_note


def _real_chains_by_category(main_n=200):
    main, _ = generate(seed=42, main_n=main_n, stress_n=0)
    chains = build_all_chains(main)
    gt = {g.transaction_id: g.true_label for g in main.ground_truth}
    by_category: dict[str, list] = {}
    for txn_id, chain in chains.items():
        by_category.setdefault(gt[txn_id], []).append(chain)
    return by_category


def test_every_category_produces_a_balanced_journal_entry():
    by_category = _real_chains_by_category()
    assert len(by_category) == 8, "expected all 8 categories to appear in a 200-record batch"
    for category, chains in by_category.items():
        for chain in chains[:5]:  # a real sample from each, not just one
            entry = generate_journal_entry(chain)
            assert entry.is_balanced, f"{category} ({chain.transaction_id}) produced an unbalanced entry"


def test_clean_match_has_no_suspense_line():
    by_category = _real_chains_by_category()
    for chain in by_category["clean_match"][:10]:
        entry = generate_journal_entry(chain)
        assert not any(l.account == "Reconciliation Suspense" for l in entry.lines)


def test_an_unresolved_gap_produces_a_real_visible_suspense_line():
    by_category = _real_chains_by_category()
    for chain in by_category["genuine_error"][:5]:
        entry = generate_journal_entry(chain)
        suspense_lines = [l for l in entry.lines if l.account == "Reconciliation Suspense"]
        assert suspense_lines, f"{chain.transaction_id} has an unexplained settlement_delta but no suspense line"
        line = suspense_lines[0]
        net_suspense = line.debit - line.credit
        assert net_suspense == -chain.settlement_delta


def test_finalized_flag_controls_the_pending_review_note():
    by_category = _real_chains_by_category()
    chain = by_category["clean_match"][0]
    finalized = generate_journal_entry(chain, finalized=True)
    pending = generate_journal_entry(chain, finalized=False)
    assert finalized.note is None
    assert pending.note is not None and "pending" in pending.note.lower()
    # finalized status must not change the actual accounting -- same lines, same balance
    assert finalized.lines == pending.lines


def test_generate_journal_entries_respects_the_finalized_id_set():
    by_category = _real_chains_by_category()
    chains = {c.transaction_id: c for c in by_category["clean_match"][:3] + by_category["genuine_error"][:2]}
    finalized_ids = {list(by_category["clean_match"])[0].transaction_id}
    entries = generate_journal_entries(chains, finalized_ids)
    finalized_map = {e.transaction_id: e.finalized for e in entries}
    assert finalized_map[list(by_category["clean_match"])[0].transaction_id] is True
    others_finalized = [v for k, v in finalized_map.items() if k != list(by_category["clean_match"])[0].transaction_id]
    assert all(v is False for v in others_finalized)


def test_tds_note_is_none_by_default_and_only_appears_for_ecommerce_operators():
    by_category = _real_chains_by_category()
    chain = by_category["clean_match"][0]
    assert tds_note(chain) is None
    assert tds_note(chain, is_ecommerce_operator=False) is None
    note = tds_note(chain, is_ecommerce_operator=True)
    assert note is not None
    assert "393" in note
    assert chain.transaction_id in note


def test_tally_xml_is_well_formed_and_balances_per_voucher():
    by_category = _real_chains_by_category()
    chains = by_category["clean_match"][:3] + by_category["partial_refund"][:2] + by_category["duplicate_refund"][:2]
    entries = [generate_journal_entry(c) for c in chains]
    xml_str = to_tally_xml(entries)

    root = ET.fromstring(xml_str)  # raises if malformed
    assert root.tag == "ENVELOPE"
    vouchers = root.findall(".//VOUCHER")
    assert len(vouchers) == len(entries)

    for voucher in vouchers:
        ledger_entries = voucher.findall("LEDGERENTRIES.LIST")
        total = 0.0
        for le in ledger_entries:
            amount = float(le.find("AMOUNT").text)
            is_positive = le.find("ISDEEMEDPOSITIVE").text
            # per Tally's own sample format, a debit (ISDEEMEDPOSITIVE=Yes) carries a NEGATIVE
            # amount and a credit (No) carries a positive one -- verified against
            # help.tallysolutions.com/sample-xml/ before writing the exporter.
            if is_positive == "Yes":
                assert amount <= 0, "a debit line's AMOUNT should be negative in Tally's format"
            else:
                assert amount >= 0, "a credit line's AMOUNT should be positive in Tally's format"
            total += amount
        assert abs(total) < 0.01, "each voucher's ledger entries should net to zero"


def test_zoho_csv_has_correct_columns_and_row_count():
    by_category = _real_chains_by_category()
    chains = by_category["clean_match"][:3]
    entries = [generate_journal_entry(c) for c in chains]
    csv_str = to_zoho_books_csv(entries)

    reader = DictReader(StringIO(csv_str))
    rows = list(reader)
    assert reader.fieldnames == ["Journal Date", "Journal Number", "Account", "Debit", "Credit", "Description", "Reference Number"]
    assert len(rows) == sum(len(e.lines) for e in entries)


def test_generic_csv_round_trips_debit_credit_amounts():
    by_category = _real_chains_by_category()
    chain = by_category["fee_deduction"][0]
    entry = generate_journal_entry(chain)
    csv_str = to_generic_csv([entry])

    reader = DictReader(StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == len(entry.lines)
    total_debit = sum(float(r["debit"]) for r in rows)
    total_credit = sum(float(r["credit"]) for r in rows)
    assert abs(total_debit - total_credit) < 0.01
