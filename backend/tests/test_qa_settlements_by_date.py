"""`settlements_by_date`, and the split it is allowed to change.

`find_transactions_by_date` needs a date already in hand. "Which day was busiest" does not supply one,
and nothing could group settlements to work it out, so the benchmark recorded a miss for every
provider on that question. The capability was genuinely absent.

The mock router deliberately gets no new keyword. Its date regex already reaches the seen phrasing
("how many settled on 2026-01-19"); a rule author who had only seen that phrasing would not have
anticipated "busiest payout day", so inventing a cue for it would hand the baseline a word it never
earned and destroy the seen/held-out contrast.
"""

import pytest

from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.qa.agent import _AGGREGATE_KEYWORDS, _ANOMALY_KEYWORDS, TOOL_SCHEMAS, _execute_tool
from app.qa.benchmark import QUESTIONS, build_questions, busiest_settlement_date
from app.qa.tools import build_settled_at_index, settlements_by_date
from app.narrator.tools import build_tool_context


@pytest.fixture(scope="module")
def fixture():
    batch, _ = generate(seed=1, main_n=120, stress_n=0)
    chains = build_all_chains(batch)
    return batch, chains, build_settled_at_index(batch), build_tool_context(batch, chains)


def test_the_tool_agrees_with_the_benchmarks_own_ground_truth(fixture):
    """Both pick the busiest date. If they broke ties differently, a tie would score as a wrong
    answer about something other than the count."""
    batch, chains, settled_at, _ = fixture
    result = settlements_by_date(chains, settled_at)
    assert result["busiest_date"] == busiest_settlement_date(batch, settled_at)


def test_the_busiest_count_matches_the_question_it_answers(fixture):
    batch, chains, settled_at, _ = fixture
    spec = {s.kind: s for s in build_questions(batch, settled_at)}["busiest_date"]
    truth = spec.truth(batch, chains, settled_at)
    assert settlements_by_date(chains, settled_at)["busiest_date_count"] == truth.expected_number


def test_every_settlement_lands_in_exactly_one_day_bucket(fixture):
    _, chains, settled_at, _ = fixture
    result = settlements_by_date(chains, settled_at)
    assert sum(d["count"] for d in result["by_date"]) == len(settled_at)
    assert len({d["date"] for d in result["by_date"]}) == result["n_dates"]


def test_dates_come_back_sorted(fixture):
    _, chains, settled_at, _ = fixture
    dates = [d["date"] for d in settlements_by_date(chains, settled_at)["by_date"]]
    assert dates == sorted(dates)


def test_an_empty_batch_returns_no_busiest_date():
    assert settlements_by_date({}, {})["busiest_date"] is None


def test_the_tool_is_reachable_through_dispatch(fixture):
    _, _, settled_at, context = fixture
    result = _execute_tool("settlements_by_date", {}, context, settled_at)
    assert result["n_dates"] > 0


def test_the_tool_is_declared_to_the_model(fixture):
    assert "settlements_by_date" in {s["function"]["name"] for s in TOOL_SCHEMAS}


# --- the split the new tool must not quietly break -------------------------------------------------


def test_the_mock_router_gained_no_cue_for_the_held_out_wording():
    """If "busiest" or "payout day" entered a keyword list, the rule would answer a question its
    author never saw, and the generalisation gap would stop measuring anything."""
    for keyword in _AGGREGATE_KEYWORDS + _ANOMALY_KEYWORDS:
        assert "busiest" not in keyword
        assert "payout" not in keyword


def test_every_held_out_phrasing_still_avoids_every_router_keyword():
    """Re-asserted here because three questions were added alongside this tool."""
    for spec in QUESTIONS:
        lowered = spec.held_out.lower()
        for keyword in _AGGREGATE_KEYWORDS + _ANOMALY_KEYWORDS:
            assert keyword not in lowered, f"{spec.kind!r} held-out phrasing leaked {keyword!r}"


def test_the_question_set_grew(fixture):
    """Thirty answers per condition supported the headline and nothing finer."""
    assert len(QUESTIONS) >= 9
