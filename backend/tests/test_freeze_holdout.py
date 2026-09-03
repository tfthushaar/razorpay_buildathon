"""The frozen holdouts still hash to what was recorded, and the freeze can tell when they don't.

`claim()` refuses to overwrite a scored holdout, which stops it being re-scored. Nothing stopped it
being re-written -- a published single-shot figure could have been edited by hand and no test would
have noticed. The manifest closes that, and this asserts the manifest is doing its job rather than
sitting there looking reassuring.

Same discipline as everywhere else here: a guard that cannot fail is not a guard, so the tamper
detection is tested by actually tampering.
"""

import json

import pytest

from app.final_holdout import (
    FINAL_DIR,
    FREEZE_DOC,
    HOLDOUT_SOURCES,
    MANIFEST,
    REPO,
    HoldoutTampered,
    assert_results_intact,
    sha256_of,
    verify,
)


def test_the_manifest_exists_and_covers_every_scored_holdout():
    assert MANIFEST.exists(), "run: python scripts/freeze_holdout.py"
    frozen = set(json.loads(MANIFEST.read_text(encoding="utf-8"))["holdouts"])
    scored = {p.stem for p in FINAL_DIR.glob("*.json") if p.name != MANIFEST.name}
    assert scored == frozen, f"scored but not frozen: {scored - frozen}"


def test_every_published_holdout_still_hashes_to_what_was_recorded():
    """The one hard rule. A single-shot number is whatever was written on the day."""
    assert_results_intact()
    for row in verify():
        assert row["result_ok"], f"{row['name']} was altered after it was scored"


def test_the_recorded_source_hashes_are_real_history_and_not_invented():
    """Every file the manifest names must exist and its recorded hash must be 64 hex characters
    taken from git, not a placeholder. A manifest that certifies a state nobody can check is worse
    than no manifest, because it looks like evidence."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, entry in manifest["holdouts"].items():
        assert entry["sources"], f"{name} froze no sources, so nothing about it can drift-check"
        assert set(entry["sources"]) == set(HOLDOUT_SOURCES[name])
        for rel, sha in entry["sources"].items():
            assert (REPO / rel).exists(), f"{name} names a source that no longer exists: {rel}"
            assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
        assert len(entry["result_sha256"]) == 64
        assert len(entry["scored_at_commit"]) >= 7


def test_tampering_with_a_published_holdout_is_detected(tmp_path, monkeypatch):
    """Edit a scored result and the check must fail. Without this, every assertion above passes
    equally well against a verifier that returns True unconditionally."""
    name = sorted(json.loads(MANIFEST.read_text(encoding="utf-8"))["holdouts"])[0]
    original = (FINAL_DIR / f"{name}.json").read_bytes()
    try:
        payload = json.loads(original)
        payload["tampered"] = "a nicer number"
        (FINAL_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

        assert any(not r["result_ok"] for r in verify())
        with pytest.raises(HoldoutTampered):
            assert_results_intact()
    finally:
        (FINAL_DIR / f"{name}.json").write_bytes(original)

    # and the repo is left exactly as it was found
    assert_results_intact()


def test_source_drift_is_reported_rather_than_raising():
    """Drift is a fact with a shelf life, not a failure. It must never break the build, or the
    honest thing to do -- change the generator and disclose it -- becomes the thing nobody does.

    Both holdouts currently have drifted sources, and that is a true statement about this repo, not
    a problem with it.
    """
    rows = verify()
    assert rows, "nothing frozen"
    assert_results_intact()  # does not raise, despite the drift below
    assert any(not r["sources_ok"] for r in rows), (
        "no holdout shows source drift; if that became true this test should be deleted rather "
        "than weakened, but check first that the manifest is not simply stale"
    )


def test_the_human_readable_freeze_is_not_stale():
    """FREEZE.md is generated from the manifest, so it can drift out of date exactly the way the
    front-page evidence file did. Every hash the manifest holds must appear in the document a reader
    is actually shown, or the receipt and the record disagree."""
    assert FREEZE_DOC.exists(), "run: python scripts/freeze_holdout.py"
    doc = FREEZE_DOC.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, entry in manifest["holdouts"].items():
        assert name in doc, f"{name} is frozen but absent from FREEZE.md"
        assert entry["result_sha256"] in doc, f"FREEZE.md does not carry the recorded hash for {name}"
        for sha in entry["sources"].values():
            assert sha[:16] in doc, f"FREEZE.md is missing a source hash for {name}"
