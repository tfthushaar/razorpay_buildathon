"""Match weights estimated from data instead of chosen by hand.

The interesting assertion is `test_a_field_that_agrees_everywhere_carries_almost_no_weight`. That is
the whole argument for the model over hand-tuning, and on this project's own data it fires on the
merchant-name comparator, which I had been weighting up to 1.0.
"""

import pytest

from app.data_gen.three_source import generate_three_source_batch
from app.resolver.fellegi_sunter import FIELDS, compare, fit_weights


@pytest.fixture(scope="module")
def fitted():
    batch = generate_three_source_batch(seed=7, n=150)
    return batch, fit_weights(batch.settlements, batch.bank_rows, batch.truth)


# --- the weights are estimated, and they disagree with mine -------------------------------------------


def test_every_field_gets_a_weight(fitted):
    _, weights = fitted
    assert set(weights.fields) == set(FIELDS)
    assert weights.n_matches > 0 and weights.n_non_matches > 0


def test_an_exact_utr_is_the_heaviest_signal(fitted):
    """It agrees on about half of true matches and almost no false one. The hand-tuned scorer gave it
    2.0 against 1.0 for a partial; the data says it is worth several times more than anything else."""
    _, weights = fitted
    utr = weights.fields["utr_exact"].agree_weight
    assert utr > max(weights.fields[f].agree_weight for f in FIELDS if f != "utr_exact")


def test_a_field_that_agrees_everywhere_carries_almost_no_weight(fitted):
    """The Fellegi-Sunter argument in one assertion. Merchant-name similarity agrees on roughly as
    many non-matches as matches, so m/u is near 1 and the log-odds contribution is near zero -- yet
    the hand-tuned scorer added the raw similarity, up to a full point, on every candidate."""
    _, weights = fitted
    name = weights.fields["name_close"]
    assert abs(name.m - name.u) < 0.10, "this fixture no longer exercises an uninformative field"
    assert abs(name.agree_weight) < 0.25


def test_agreement_is_worth_more_than_disagreement_is_worth_nothing(fitted):
    """Sanity on the sign convention: agreeing on an informative field must help, disagreeing must
    hurt. A sign error here would silently invert the whole scorer."""
    _, weights = fitted
    for field in ("utr_exact", "amount_exact"):
        assert weights.fields[field].agree_weight > 0
        assert weights.fields[field].disagree_weight < 0


def test_probabilities_stay_inside_the_open_unit_interval(fitted):
    """A field agreeing on every observed match would give m=1 and an infinite weight."""
    _, weights = fitted
    for field in FIELDS:
        assert 0.0 < weights.fields[field].m < 1.0
        assert 0.0 < weights.fields[field].u < 1.0


# --- the comparator sees the same candidates the hand-tuned scorer does --------------------------------


def test_pairs_outside_the_blocking_window_are_not_compared(fitted):
    batch, _ = fitted
    settlement = batch.settlements[0]
    far = [r for r in batch.bank_rows if abs(r.credit_amount - settlement.amount) > 10_000]
    if not far:
        pytest.skip("no out-of-window row in this batch")
    assert compare(settlement, far[0]) is None


def test_comparisons_are_all_booleans(fitted):
    batch, _ = fitted
    for settlement in batch.settlements[:20]:
        for row in batch.bank_rows:
            result = compare(settlement, row)
            if result is None:
                continue
            assert set(result) == set(FIELDS)
            assert all(isinstance(v, bool) for v in result.values())


# --- fitting and scoring never share a batch ------------------------------------------------------------


def test_estimated_weights_beat_the_hand_tuned_ones_out_of_sample(fitted):
    """Fitted on seed 7, scored on seed 42, because estimating m and u from the pairs you then score
    measures memorisation -- the same discipline the calibrated forecast interval follows."""
    from app.resolver.entity_resolution import match_all

    _, weights = fitted
    batch = generate_three_source_batch(seed=42, n=150)

    hand = match_all(batch.settlements, batch.bank_rows, use_cycle_ref=False)
    hand_correct = sum(
        1 for sid, r in hand.items() if r.candidates and r.candidates[0].bank_row_id == batch.truth.get(sid)
    )

    fs_correct = 0
    for settlement in batch.settlements:
        scored = []
        for row in batch.bank_rows:
            comparisons = compare(settlement, row)
            if comparisons is None:
                continue
            scored.append((weights.score(comparisons), row.bank_row_id))
        if scored and max(scored)[1] == batch.truth.get(settlement.settlement_id):
            fs_correct += 1

    assert fs_correct >= hand_correct, f"estimated weights scored {fs_correct} against hand-tuned {hand_correct}"
