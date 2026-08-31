"""The single-shot rule, and the fact that it is a rule rather than an intention."""

import json

import pytest

from app import final_holdout
from app.final_holdout import FINAL_SEEDS, HoldoutAlreadyScored, claim


@pytest.fixture
def temp_final(tmp_path, monkeypatch):
    monkeypatch.setattr(final_holdout, "FINAL_DIR", tmp_path)
    return tmp_path


def test_a_holdout_can_be_scored_once(temp_final):
    path = claim("reading", {"accuracy": 0.61})
    assert path.exists()
    assert json.loads(path.read_text())["accuracy"] == 0.61


def test_scoring_it_twice_raises_instead_of_overwriting(temp_final):
    """The whole mechanism. A second run must fail loudly, not quietly produce a nicer number."""
    claim("reading", {"accuracy": 0.61})
    with pytest.raises(HoldoutAlreadyScored):
        claim("reading", {"accuracy": 0.99})


def test_the_first_number_survives_the_second_attempt(temp_final):
    claim("reading", {"accuracy": 0.61})
    with pytest.raises(HoldoutAlreadyScored):
        claim("reading", {"accuracy": 0.99})
    assert json.loads((temp_final / "reading.json").read_text())["accuracy"] == 0.61


def test_each_experiment_has_its_own_single_shot(temp_final):
    claim("reading", {"a": 1})
    claim("three_source", {"a": 2})
    assert final_holdout.already_scored("reading")
    assert not final_holdout.already_scored("qa")


def test_the_final_seeds_are_untouched_by_any_existing_experiment():
    """These must not collide with a seed any committed script already uses, or the set is not
    held out at all."""
    used = {1, 2, 3, 7, 42, 100, 101, 102, 111, 777, 909, 1337}
    assert set(FINAL_SEEDS.values()) & used == set()
    assert all(s > 20_000_000 for s in FINAL_SEEDS.values())
