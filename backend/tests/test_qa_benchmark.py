"""Tests for the Q&A ground-truth benchmark.

`test_held_out_phrasings_avoid_every_router_keyword` is the load-bearing one. The seen/held-out split
only means something if the held-out questions genuinely avoid the vocabulary the mock router was
built around. Without that assertion the generalisation gap would be measuring nothing, exactly as it
would in the reading experiment without its equivalent check.
"""

from datetime import datetime

import pytest

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.qa.agent import _AGGREGATE_KEYWORDS, _ANOMALY_KEYWORDS, _DATE_RE
from app.qa.benchmark import (
    QUESTIONS,
    GroundTruth,
    build_questions,
    busiest_settlement_date,
    extract_ids_from_text,
    score_answer,
)
from app.qa.tools import build_settled_at_index


def _batch(seed: int = 1, n: int = 80):
    batch, _ = generate(seed=seed, main_n=n, stress_n=0)
    chains = build_all_chains(batch)
    return batch, chains, build_settled_at_index(batch)


# --- the held-out split has to be real ------------------------------------------------------------


def test_held_out_phrasings_avoid_every_router_keyword():
    """The mock routes on a nine-word keyword list and a date regex, both of which I wrote. If a
    held-out question contains any of them the split measures nothing."""
    for spec in QUESTIONS:
        lowered = spec.held_out.lower()
        for keyword in _ANOMALY_KEYWORDS + _AGGREGATE_KEYWORDS:
            assert keyword not in lowered, f"{spec.kind!r} held-out phrasing contains router keyword {keyword!r}"
        assert not _DATE_RE.search(spec.held_out), f"{spec.kind!r} held-out phrasing contains a date literal"


def test_seen_phrasings_do_reach_the_router():
    """The complement. If the seen questions did not trigger the router either, the comparison would
    be between two kinds of failure rather than between authorship and comprehension."""
    batch, chains, settled_at = _batch()
    triggered = 0
    for spec in build_questions(batch, settled_at):
        lowered = spec.seen.lower()
        if _DATE_RE.search(spec.seen) or any(k in lowered for k in _ANOMALY_KEYWORDS + _AGGREGATE_KEYWORDS):
            triggered += 1
    assert triggered >= 3, "the seen phrasings barely reach the router, so the split is not a fair contrast"


def test_seen_and_held_out_ask_the_same_question():
    """Same ground-truth function on both sides, so the two columns are comparable."""
    for spec in QUESTIONS:
        assert spec.seen != spec.held_out or spec.kind == "busiest_date"
        assert callable(spec.truth)


# --- ground truth is derived, not asserted ---------------------------------------------------------


def test_ground_truth_comes_from_the_generators_answer_key():
    batch, chains, settled_at = _batch()
    specs = {s.kind: s for s in build_questions(batch, settled_at)}
    flagged = specs["flagged_count"].truth(batch, chains, settled_at)
    expected = {g.transaction_id for g in batch.ground_truth if g.true_label in ("duplicate_refund", "netting_trap")}
    assert flagged.expected_ids == expected
    assert flagged.expected_number == len(expected)


def test_batch_size_truth_matches_the_batch():
    batch, chains, settled_at = _batch(n=80)
    specs = {s.kind: s for s in build_questions(batch, settled_at)}
    assert specs["batch_size"].truth(batch, chains, settled_at).expected_number == len(chains)


def test_busiest_date_question_is_filled_in_per_batch():
    batch, chains, settled_at = _batch()
    specs = {s.kind: s for s in build_questions(batch, settled_at)}
    assert busiest_settlement_date(batch, settled_at) in specs["busiest_date"].seen


def test_ground_truth_is_deterministic_per_seed():
    a = _batch(seed=3)
    b = _batch(seed=3)
    for spec_a, spec_b in zip(build_questions(*[a[0], a[2]]), build_questions(*[b[0], b[2]])):
        assert spec_a.truth(a[0], a[1], a[2]) == spec_b.truth(b[0], b[1], b[2])


# --- scoring -----------------------------------------------------------------------------------------


def test_numeric_scoring_accepts_a_thousands_separator():
    truth = GroundTruth(expected_number=1200, expected_ids=set())
    assert score_answer("There were 1,200 transactions.", [], truth, set())["numeric_correct"]


def test_numeric_scoring_does_not_credit_a_substring_match():
    """'10' inside '104' must not count, or the score is meaningless on small numbers."""
    truth = GroundTruth(expected_number=10, expected_ids=set())
    assert not score_answer("There were 104 transactions.", [], truth, set())["numeric_correct"]


def test_fabricated_ids_are_counted_against_the_real_batch():
    truth = GroundTruth(expected_number=None, expected_ids=set())
    scored = score_answer("see order_aaaaaa and order_bbbbbb", ["order_aaaaaa", "order_bbbbbb"], truth, {"order_aaaaaa"})
    assert scored["n_fabricated"] == 1
    assert scored["fabricated_ids"] == ["order_bbbbbb"]


def test_ids_named_only_in_prose_still_count_as_citations():
    """A model that names a transaction in its answer has cited it as far as a reader is concerned,
    whatever the structured field says."""
    assert extract_ids_from_text("I looked at order_1a2b3c4d and it was clean.") == {"order_1a2b3c4d"}


def test_citation_score_is_one_for_an_exact_set_and_zero_for_a_disjoint_one():
    truth = GroundTruth(expected_number=None, expected_ids={"order_aaaaaa", "order_bbbbbb"})
    universe = {"order_aaaaaa", "order_bbbbbb", "order_cccccc"}
    exact = score_answer("", ["order_aaaaaa", "order_bbbbbb"], truth, universe)
    disjoint = score_answer("", ["order_cccccc"], truth, universe)
    assert exact["citation_jaccard"] == 1.0
    assert disjoint["citation_jaccard"] == 0.0


def test_citation_score_is_absent_when_no_ids_were_expected():
    truth = GroundTruth(expected_number=5, expected_ids=set())
    assert score_answer("5 transactions", [], truth, set())["citation_jaccard"] is None


# --- the mock router's own behaviour ------------------------------------------------------------------


def test_the_keyword_router_loses_accuracy_on_held_out_phrasing():
    """The finding this benchmark exists to establish, as a standing assertion: the mock's advantage
    on the seen column is the router's vocabulary, not comprehension."""
    from app.narrator.tools import build_tool_context
    from app.qa.agent import answer_question

    batch, chains, settled_at = _batch(seed=1, n=80)
    context = build_tool_context(batch, chains)

    def numeric_hits(attr: str) -> int:
        hits = 0
        for spec in build_questions(batch, settled_at):
            truth = spec.truth(batch, chains, settled_at)
            if truth.expected_number is None:
                continue
            answer = answer_question(getattr(spec, attr), context, settled_at, provider="mock")
            if score_answer(answer.answer, answer.cited_transaction_ids, truth, set(chains))["numeric_correct"]:
                hits += 1
        return hits

    assert numeric_hits("seen") > numeric_hits("held_out")


@pytest.mark.parametrize("kind", [q.kind for q in QUESTIONS])
def test_every_question_has_a_usable_ground_truth(kind):
    batch, chains, settled_at = _batch()
    spec = {s.kind: s for s in build_questions(batch, settled_at)}[kind]
    truth = spec.truth(batch, chains, settled_at)
    assert truth.expected_number is not None or truth.expected_ids
