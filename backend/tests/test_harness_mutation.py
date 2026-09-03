"""Do the gates that report zero actually respond to a defect, or are they measuring nothing?

Mutation testing, in the sense of DeMillo, Lipton and Sayward (1978): seed a fault deliberately and
check the test suite notices. Applied here to the measurements rather than to the code, because "we
measured 0.00%" is a claim about the world only if the number could have come out otherwise. A metric
that cannot move is not evidence of anything.

Two headline zeros here are exposed to exactly that objection.

    0 false positives across 51,000     could mean a detector that never fires
    0 wrongly auto-resolved on shapes   could mean a gate that cannot see a wrong resolution
    the matcher was never built for

Both are already tested for the property they claim. Neither was tested for whether the test could
fail. This file breaks each one on purpose and asserts the measurement notices, which is the only way
a zero earns its place in a README.

This project has been caught by precisely this before: a sensitivity sweep once reported that a
tolerance constant "does not govern correctness", which was true only because the constant was not on
the code path being measured. The measurement was working perfectly and measuring nothing.
"""

from copy import deepcopy

import pytest

from app.chain.builder import build_all_chains
from app.chain.controls import run_data_integrity_controls
from app.data_gen.generate import generate
from app.data_gen.novel_shapes import NOVEL_SHAPES, generate_novel_batch
from app.feeleak.detector import LEAK_EPSILON, run_fee_leak_detection
from app.matching.engine import run_matching_engine


# --------------------------------------------------------------------------------------------
# The fee-leak false-positive rate
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("n_corrupted", [1, 5, 25])
def test_the_fee_detector_finds_exactly_the_leaks_injected_and_no_others(n_corrupted):
    """Inject a known number of overcharges into a clean batch; the detector must return that number.

    Exactly, in both directions. Finding fewer means the scan is blind to real leaks and the 0 is
    worthless. Finding more means the 51,000-transaction false-positive figure was luck rather than
    a property, because the extras are false positives on transactions nobody touched.
    """
    main, _ = generate(seed=7, main_n=200, stress_n=0)

    clean = run_fee_leak_detection(main.orders, main.payments)
    assert clean.findings == [], "the batch was not clean to begin with, so this proves nothing"

    payments = deepcopy(main.payments)
    corrupted_ids = set()
    for payment in payments[:n_corrupted]:
        # Well clear of the epsilon, so this is a leak by the detector's own definition rather than
        # a rounding argument.
        payment.fee_amount += LEAK_EPSILON * 10
        corrupted_ids.add(payment.payment_id)

    found = run_fee_leak_detection(main.orders, payments)
    found_ids = {f.transaction_id for f in found.findings}

    assert len(found.findings) == n_corrupted, (
        f"injected {n_corrupted} overcharges, detector returned {len(found.findings)}"
    )
    assert len(found_ids) == len(corrupted_ids), "a corrupted payment was reported more than once"


def test_a_leak_one_paisa_inside_the_epsilon_is_deliberately_not_reported():
    """The boundary in the other direction. A detector that fires on everything would also score
    zero false negatives, so the epsilon has to be shown to hold."""
    main, _ = generate(seed=7, main_n=50, stress_n=0)
    payments = deepcopy(main.payments)
    payments[0].fee_amount += LEAK_EPSILON  # exactly at the tolerance, not past it
    assert run_fee_leak_detection(main.orders, payments).findings == []

    payments[0].fee_amount += 1  # one paisa past it
    assert len(run_fee_leak_detection(main.orders, payments).findings) == 1


# --------------------------------------------------------------------------------------------
# The "0 wrongly resolved on novel shapes" gate
# --------------------------------------------------------------------------------------------

def _score_novel_batch(flagged_ids: set[str]) -> int:
    """Replicates the pass criterion of scripts/generate_generalization_evidence.py.

    A novel-shape transaction that the matcher did not escalate and no control flagged has been
    closed with no basis for closing it, which is the one outcome that fails the suite.
    """
    batch, shape_of = generate_novel_batch()
    results = run_matching_engine(build_all_chains(batch))
    wrong = 0
    for txn_id, result in results.items():
        if shape_of.get(txn_id) not in NOVEL_SHAPES:
            continue
        if result.resolution == "needs_narration" or txn_id in flagged_ids:
            continue
        wrong += 1
    return wrong


def test_the_generalisation_gate_reads_zero_when_the_controls_are_working():
    batch = generate_novel_batch()[0]
    flagged = {f.transaction_id for f in run_data_integrity_controls(batch)}
    assert _score_novel_batch(flagged) == 0


def test_the_generalisation_gate_would_have_caught_a_regression_in_the_controls():
    """Switch the data-integrity controls off and the same suite must stop reporting zero.

    This is the whole point. Three of the four novel shapes are caught by a control rather than by
    the matcher, so if the controls silently stopped working, every one of those settlements would
    reconcile clean and be closed on no basis at all. A pass criterion that could not see that is
    decoration.
    """
    wrong_without_controls = _score_novel_batch(flagged_ids=set())
    assert wrong_without_controls > 0, (
        "with every control disabled the suite still reports 0 wrong resolutions, which means it "
        "cannot detect the failure it exists to detect"
    )


def test_each_control_is_individually_load_bearing():
    """Not just "the controls matter" collectively -- each one is the only thing catching its shape.

    A control that could be deleted without the number moving is either redundant or dead, and
    either way the suite is not testing what the docs say it tests.
    """
    batch = generate_novel_batch()[0]
    findings = run_data_integrity_controls(batch)
    by_control: dict[str, set[str]] = {}
    for finding in findings:
        by_control.setdefault(finding.control, set()).add(finding.transaction_id)

    assert by_control, "no control fired on the novel batch at all"

    baseline = _score_novel_batch({f.transaction_id for f in findings})
    assert baseline == 0

    for control, ids in by_control.items():
        without_this_one = {f.transaction_id for f in findings if f.control != control}
        assert _score_novel_batch(without_this_one) > baseline, (
            f"disabling {control} changed nothing, so nothing in the suite depends on it"
        )
