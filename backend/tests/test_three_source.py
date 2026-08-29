"""Tests for three-source reconciliation and the entity-resolution matcher.

This exists as a check on the residual argument itself. If under-determination only ever appeared in
compound settlement arithmetic, it would be fair to suspect the arithmetic was built to produce it.
Entity resolution is a different problem, different data, different rule -- and
`test_identical_twins_are_genuinely_indistinguishable_without_the_cycle` is the assertion that it
produces the same structure for the same reason.
"""

import pytest

from app.data_gen.three_source import ThreeSourceGenerator, generate_three_source_batch
from app.resolver.entity_resolution import (
    MAX_AMOUNT_SLIP_PAISE,
    MAX_DATE_SLIP_DAYS,
    _cycle_agrees,
    _name_similarity,
    _utr_match,
    match_all,
    match_settlement,
)


# --- the generator ------------------------------------------------------------------------------


def test_every_settlement_has_exactly_one_true_bank_row():
    b = generate_three_source_batch(seed=42, n=60)
    assert len(b.truth) == len(b.settlements)
    ids = {r.bank_row_id for r in b.bank_rows}
    for sid, bank_id in b.truth.items():
        assert bank_id in ids, f"{sid} points at a bank row that does not exist"
    # a bank row is claimed by at most one settlement
    assert len(set(b.truth.values())) == len(b.truth)


def test_decoys_exist_and_are_not_claimed_by_any_settlement():
    b = generate_three_source_batch(seed=42, n=60)
    claimed = set(b.truth.values())
    assert len(b.bank_rows) > len(claimed), "no decoy rows -- matching would be trivial"


def test_identical_twins_are_genuinely_indistinguishable_without_the_cycle():
    """The load-bearing property: for an identical twin pair, merchant, amount and date are all
    equal, so no structured field separates them. Only the cycle reference does, and the bank carries
    it only sometimes and only as free text."""
    b = generate_three_source_batch(seed=42, n=60, identical_twin_rate=0.3)
    twins = [s for s in b.settlements if "identical_twin" in b.corruptions.get(s.settlement_id, [])]
    assert twins, "no identical twins generated"
    by_key: dict[tuple, list] = {}
    for s in twins:
        by_key.setdefault((s.merchant_id, s.amount, s.value_date.date()), []).append(s)
    pairs = [v for v in by_key.values() if len(v) >= 2]
    assert pairs, "identical twins did not actually share merchant/amount/date"
    for group in pairs:
        assert len({s.cycle_ref for s in group}) == len(group), "twins must differ on cycle, or nothing separates them"


def test_generation_is_deterministic_per_seed():
    a = generate_three_source_batch(seed=7, n=40)
    b = generate_three_source_batch(seed=7, n=40)
    assert [r.description for r in a.bank_rows] == [r.description for r in b.bank_rows]
    assert a.truth == b.truth


def test_held_out_cycle_phrasing_defeats_the_regex_parser_entirely():
    """The held-out bank must actually be held out, or the generalisation number measures nothing."""
    g = ThreeSourceGenerator(seed=5)
    for style in g._CYCLE_STYLES_HELDOUT:
        rendered = style("SOME BANK DESCRIPTION", "C2026-03-13-D")
        assert _cycle_agrees("C2026-03-13-D", rendered) is None, rendered


def test_seen_cycle_phrasing_is_parseable_by_the_regex_parser():
    """The complement: the seen bank must be parseable, or the comparison is not a fair one."""
    g = ThreeSourceGenerator(seed=5)
    for style in g._CYCLE_STYLES:
        rendered = style("SOME BANK DESCRIPTION", "C2026-03-13-D")
        assert _cycle_agrees("C2026-03-13-D", rendered) is True, rendered


# --- matcher primitives -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utr,description,expect",
    [
        ("144734155720", "REF 144734155720 ACME", True),
        ("144734155720", "NEFT/34155720 ACME", True),
        ("144734155720", "CR-155720-ACME", True),
        ("144734155720", "REF 999999999999 ACME", False),
        ("144734155720", "ACME SETTLEMENT", False),
    ],
)
def test_utr_matching_handles_truncation_and_prefixes(utr, description, expect):
    assert _utr_match(utr, description)[0] is expect


def test_name_similarity_survives_bank_abbreviation_styles():
    for rendered in ("MERCHANT 003", "MERCHANT003", "Merchant-003", "MRCHNT003"):
        assert _name_similarity("merchant_003", f"REF 123456 {rendered}") > 0.5, rendered


def test_cycle_agrees_distinguishes_disagreement_from_absence():
    assert _cycle_agrees("C2026-03-13-D", "PAYOUT CYCLE C2026-03-13-D") is True
    assert _cycle_agrees("C2026-03-13-D", "PAYOUT CYCLE C2026-03-13-A") is False
    assert _cycle_agrees("C2026-03-13-D", "PAYOUT, no cycle here") is None


def test_absent_cycle_is_not_scored_as_evidence_against():
    """A third of banks carry no cycle at all; treating that as disagreement would reject good matches."""
    b = generate_three_source_batch(seed=42, n=30)
    settlement = b.settlements[0]
    rows = [r for r in b.bank_rows if r.bank_row_id == b.truth[settlement.settlement_id]]
    with_cycle = match_settlement(settlement, rows, use_cycle_ref=True)
    without = match_settlement(settlement, rows, use_cycle_ref=False)
    if with_cycle.candidates and without.candidates:
        assert with_cycle.candidates[0].score >= without.candidates[0].score


# --- the matcher as Layer 0 ----------------------------------------------------------------------


def test_matcher_reports_all_scored_candidates_not_only_winners():
    """Regression: returning only the top tie made 'picked the wrong row' indistinguishable from
    'the right row was never reachable', and ten ordinary ranking errors briefly looked like the
    filter discarding the truth."""
    b = generate_three_source_batch(seed=42, n=60)
    results = match_all(b.settlements, b.bank_rows)
    multi = [r for r in results.values() if len(r.candidates) > r.tied_at_top]
    assert multi, "no result kept a non-winning candidate -- reachability cannot be measured"


def test_truth_is_reachable_for_every_settlement():
    """The ceiling on any chooser downstream. If the truth is filtered out, no reader can recover it."""
    b = generate_three_source_batch(seed=42, n=60)
    results = match_all(b.settlements, b.bank_rows)
    unreachable = [sid for sid, r in results.items() if not r.reachable(b.truth[sid])]
    assert unreachable == [], f"{len(unreachable)} settlements had their true bank row filtered out"


def test_chance_baseline_is_one_over_tied_at_top():
    b = generate_three_source_batch(seed=42, n=60)
    for r in match_all(b.settlements, b.bank_rows).values():
        if r.status == "UNDER_DETERMINED":
            assert r.chance_baseline == pytest.approx(1.0 / r.tied_at_top)
            assert r.tied_at_top >= 2
        elif r.status == "RESOLVED":
            assert r.chance_baseline == 1.0


def test_cycle_parsing_helps_on_seen_phrasing_and_not_at_all_on_held_out():
    """The finding this whole module exists to establish, as a standing assertion."""
    seen = generate_three_source_batch(seed=42, n=120, held_out_cycle_phrasing=False)
    held = generate_three_source_batch(seed=42, n=120, held_out_cycle_phrasing=True)

    def acc(batch, use_cycle):
        res = match_all(batch.settlements, batch.bank_rows, use_cycle_ref=use_cycle)
        return sum(1 for sid, r in res.items() if r.best() and r.best().bank_row_id == batch.truth[sid]) / len(res)

    assert acc(seen, True) > acc(seen, False), "cycle parsing must help on the phrasing it was written for"
    assert acc(held, True) == pytest.approx(acc(held, False)), "on unseen phrasing it must buy nothing"


def test_a_pluggable_reader_replaces_the_regex_without_touching_anything_else():
    """The control that makes the model column interpretable: swapping the reader changes the reading
    and nothing else."""
    b = generate_three_source_batch(seed=42, n=40, held_out_cycle_phrasing=True)
    always_absent = match_all(b.settlements, b.bank_rows, cycle_reader=lambda c, d: None)
    regex = match_all(b.settlements, b.bank_rows, use_cycle_ref=True)
    # the regex reads nothing on held-out phrasing, so it must agree with a reader that reads nothing
    assert [r.status for r in always_absent.values()] == [r.status for r in regex.values()]
