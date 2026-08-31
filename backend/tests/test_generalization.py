"""The generalisation suite, and the assertion that keeps it honest.

`test_the_traps_are_still_loaded` is the load-bearing one. A generalisation suite that quietly stops
generalising keeps printing PASS forever: if a later change to the generator made the novel shapes
resolvable by ordinary means, the table would go green and mean nothing. So the traps are asserted
to still be traps -- the amounts still tie, so nothing but the intended defect distinguishes them.
"""

import pytest

from app.chain.builder import build_all_chains
from app.chain.controls import run_data_integrity_controls
from app.data_gen.generate import generate
from app.data_gen.novel_shapes import NOVEL_SHAPES, generate_novel_batch
from app.matching.engine import run_matching_engine


@pytest.fixture(scope="module")
def novel():
    batch, shape_of = generate_novel_batch()
    return batch, shape_of, build_all_chains(batch)


# --- the traps have to still be traps -----------------------------------------------------------


def test_the_traps_are_still_loaded(novel):
    """Each novel shape must still tie arithmetically. A shape whose amount stopped matching would
    be caught by ordinary delta logic, and the suite would be testing nothing."""
    batch, shape_of, chains = novel
    for txn_id, shape in shape_of.items():
        if shape in ("clean_match", "bank_fee_deduction"):
            continue  # bank_fee_deduction is meant to break the amount; that is its whole point
        assert chains[txn_id].settlement_delta == 0, (
            f"{shape} no longer ties arithmetically, so it is no longer testing generalisation"
        )


def test_the_novel_shapes_are_absent_from_the_generator_taxonomy():
    """If one of these ever became a real category, the matcher would be written knowing it and
    this suite would stop measuring generalisation."""
    from app.data_gen.schemas import TrueLabel
    from typing import get_args

    known = set(get_args(TrueLabel))
    assert set(NOVEL_SHAPES) & known == set()


def test_controls_are_present_so_escalating_everything_could_not_pass(novel):
    """In-distribution controls must still resolve. A pipeline that refused all work would
    otherwise satisfy a no-wrong-match gate trivially."""
    batch, shape_of, chains = novel
    results = run_matching_engine(chains)
    controls = [t for t, s in shape_of.items() if s == "clean_match"]
    assert controls
    assert all(results[t].resolution != "needs_narration" for t in controls)


# --- the data-integrity controls ----------------------------------------------------------------


def test_a_transaction_settled_twice_is_caught(novel):
    """build_all_chains keys settlements by payment_id in a dict comprehension, so the second
    settlement was silently discarded and the chain tied perfectly against the survivor. Real money
    paid twice, reported as a clean reconciliation."""
    batch, shape_of, _ = novel
    findings = {f.transaction_id for f in run_data_integrity_controls(batch) if f.control == "duplicate_settlement"}
    doubles = {t for t, s in shape_of.items() if s == "double_settlement"}
    assert doubles and doubles <= findings


def test_a_settlement_dated_before_its_capture_is_caught(novel):
    batch, shape_of, _ = novel
    findings = {f.transaction_id for f in run_data_integrity_controls(batch) if f.control == "impossible_timing"}
    post_dated = {t for t, s in shape_of.items() if s == "post_dated_settlement"}
    assert post_dated and post_dated <= findings


def test_the_controls_are_silent_on_an_ordinary_batch():
    """A control that fires on clean data is worse than no control: it trains the reader to ignore
    it. Checked across several seeds, not one."""
    for seed in (1, 42, 100):
        batch, _ = generate(seed=seed, main_n=120, stress_n=0)
        assert run_data_integrity_controls(batch) == []


def test_a_control_reports_the_money_it_puts_in_question(novel):
    batch, _, _ = novel
    for finding in run_data_integrity_controls(batch):
        assert finding.amount_at_risk > 0
        assert finding.detail


def test_duplicate_settlement_charges_only_the_extra_payouts(novel):
    """The first settlement is legitimate. Only what was paid on top of it is at risk."""
    batch, shape_of, _ = novel
    by_payment = {}
    for s in batch.settlements:
        by_payment.setdefault(s.payment_id, []).append(s)
    order_of = {p.payment_id: p.order_id for p in batch.payments}

    for finding in run_data_integrity_controls(batch):
        if finding.control != "duplicate_settlement":
            continue
        settlements = next(v for k, v in by_payment.items() if order_of.get(k) == finding.transaction_id)
        assert finding.amount_at_risk == sum(s.settled_amount for s in settlements[1:])
        assert finding.amount_at_risk < sum(s.settled_amount for s in settlements)
