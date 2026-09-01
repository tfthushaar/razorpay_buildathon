"""The front page is generated from committed evidence, so it can go wrong in two silent ways.

Both happened on 2026-09-01 and only one of them crashed.

The loud one: `_latest("three-source-*.json")` began matching a seed-stability sweep with an entirely
different schema, and failed as a KeyError three functions away from the glob that caused it.

The quiet one is why this file exists. `advice-reading-*.json` was resolving to a two-column
second-family run, which would have dropped two readers off the front page without raising anything
at all. And the committed `headline.json` was three days stale, so the deployed hero was showing a
three-source figure the docs had already retracted. Nothing failed. Nothing could have.
"""

import importlib.util
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SCRIPT = BACKEND / "scripts" / "export_headline_evidence.py"
COMMITTED = ROOT / "frontend" / "src" / "evidence" / "headline.json"


def _module():
    spec = importlib.util.spec_from_file_location("export_headline_evidence", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_pattern_spanning_two_experiments_is_refused():
    """The exact glob that broke, kept as the test. These files share a prefix and not a schema."""
    with pytest.raises(SystemExit) as e:
        _module()._latest("three-source-*.json")
    assert "different experiments" in str(e.value)


def test_a_named_pattern_still_resolves():
    assert _module()._latest("three-source-qwen7b-*.json").name.startswith("three-source-qwen7b-")


def test_the_newest_is_chosen_by_date_and_not_by_string_order():
    """`three-source-qwen14b-` vs `three-source-qwen7b-` sorts on "14b" against "7b", so whole-name
    ordering returns the wrong file for reasons that have nothing to do with when it was run."""
    mod = _module()
    stem, newest = "z-fixture", None
    tmp = []
    try:
        for day in ("2026-01-02", "2026-01-10", "2026-01-09"):
            path = mod.EVIDENCE / f"{stem}-{day}.json"
            path.write_text("{}", encoding="utf-8")
            tmp.append(path)
        newest = mod._latest(f"{stem}-*.json")
        assert newest.name == f"{stem}-2026-01-10.json"
    finally:
        for path in tmp:
            path.unlink(missing_ok=True)


def test_undated_evidence_is_refused_rather_than_ranked():
    mod = _module()
    path = mod.EVIDENCE / "z-fixture-undated.json"
    path.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(SystemExit) as e:
            mod._latest("z-fixture-undated*.json")
        assert "YYYY-MM-DD" in str(e.value)
    finally:
        path.unlink(missing_ok=True)


def test_the_committed_front_page_still_matches_the_evidence(tmp_path, monkeypatch, capsys):
    """The staleness guard. Regenerate into a temp file and diff against what is committed.

    This is the check that was missing: a hero fed by committed evidence is only immune to a dead
    backend if the committed file is current, and nothing was verifying that. Everything but the
    generation date must match, or the front page is quoting a number the evidence no longer says.
    """
    mod = _module()
    out = tmp_path / "headline.json"
    monkeypatch.setattr(mod, "OUT", out)
    mod.main()
    capsys.readouterr()

    fresh = json.loads(out.read_text(encoding="utf-8"))
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    fresh.pop("generated_on", None)
    committed.pop("generated_on", None)
    assert fresh == committed, (
        "frontend/src/evidence/headline.json is out of date with docs/evidence/. "
        "Run: cd backend && python scripts/export_headline_evidence.py"
    )
