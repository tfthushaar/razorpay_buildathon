"""The holdout rule, enforced by code rather than by my memory of it.

Every held-out figure published in RESULTS was re-measured across passes. The reading experiment,
three-source and the Q&A benchmark have each been run many times while the system around them
changed, and the numbers that survived are the ones I kept. That is multiple testing on a held-out
set: the intervals are narrower than they should be, and the point estimates are optimistic by an
amount nobody has measured.

The fix is a set that is scored exactly once. Not once per pass, not once per interesting change --
once, on seeds no experiment has ever touched, with whatever comes out being what ships.

A rule that depends on remembering it is not a rule. `claim()` writes to docs/evidence/final/ and
REFUSES to overwrite, so a second run of the same experiment fails loudly instead of quietly
producing a nicer number. Deleting the file to re-run is possible and is exactly the kind of thing
that leaves a trace in git history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FINAL_DIR = REPO / "docs" / "evidence" / "final"
MANIFEST = FINAL_DIR / "freeze.json"
FREEZE_DOC = FINAL_DIR / "FREEZE.md"

# The files that decide what each holdout's number comes out as: the ones that build its input and
# the ones that score it. "Has this result been invalidated?" is not answerable from the result file
# alone -- the answer lives in whether these changed underneath it, which is exactly what happened to
# three_source and was caught by reading prose rather than by anything mechanical.
HOLDOUT_SOURCES = {
    "reading": [
        "backend/scripts/generate_reading_evidence.py",
    ],
    "three_source": [
        "backend/app/data_gen/three_source.py",
        "backend/app/resolver/entity_resolution.py",
        "backend/app/resolver/fellegi_sunter.py",
    ],
    "qa": [
        "backend/app/qa/agent.py",
    ],
}

# Seeds no experiment in this repository has ever been run against. The existing scripts use 1-12,
# 42, 100-111, 777, 909 and 1337; these are outside all of them.
FINAL_SEEDS = {
    "reading": 20260901,
    "three_source": 20260902,
    "qa": 20260903,
}


class HoldoutAlreadyScored(RuntimeError):
    """Raised when a final holdout file already exists.

    Re-running it to get a better number is the exact failure this module exists to prevent, so
    this is an error rather than a warning or an overwrite.
    """


def claim(name: str, payload: dict[str, Any]) -> Path:
    """Write one final-holdout result, once.

    The file is the claim. If it exists, this experiment has already had its single shot and the
    published number stands, whatever a re-run would say.
    """
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    path = FINAL_DIR / f"{name}.json"
    if path.exists():
        raise HoldoutAlreadyScored(
            f"{path.name} already exists: this holdout has been scored. Re-running it to obtain a "
            f"different number is what the single-shot rule exists to prevent. The published figure "
            f"stands. If you genuinely need to re-run, delete the file deliberately -- git will "
            f"record that you did."
        )
    path.write_text(
        json.dumps({"scored_on": date.today().isoformat(), "seed": FINAL_SEEDS.get(name), **payload}, indent=2),
        encoding="utf-8",
    )
    return path


def already_scored(name: str) -> bool:
    return (FINAL_DIR / f"{name}.json").exists()


def sha256_of(path: Path) -> str:
    """Hashed on raw bytes. Line endings are part of the file, and pretending otherwise would make
    the hash agree with itself across platforms while disagreeing with what is actually committed."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HoldoutTampered(RuntimeError):
    """Raised when a scored holdout file no longer hashes to what the manifest recorded.

    Refusing to overwrite stops a holdout being re-SCORED. It does nothing about a holdout being
    re-WRITTEN, and a rule that only guards one of those is not guarding much.
    """


def verify() -> list[dict[str, Any]]:
    """Re-hash every frozen holdout and report what has moved since it was scored.

    Two independent questions, and the second is the one that has already caught something:

        result_ok    does the answer file still hash to what was recorded? If not, the published
                     number was edited after the single shot was taken.
        sources_ok   do the files that produce and score it still hash to what they did at the
                     scoring commit? If not, the number stands as a record of what happened that
                     day and no longer describes what this code would do now.

    A drifted source is not tampering and is not an error. It is a fact about the result's shelf
    life, and it is reported rather than hidden, because the alternative is a holdout that looks
    authoritative long after the thing it measured stopped existing.
    """
    if not MANIFEST.exists():
        return []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for name, entry in sorted(manifest["holdouts"].items()):
        path = FINAL_DIR / f"{name}.json"
        result_now = sha256_of(path) if path.exists() else None
        drifted = []
        for rel, recorded in sorted(entry["sources"].items()):
            current = REPO / rel
            now = sha256_of(current) if current.exists() else None
            if now != recorded:
                drifted.append(rel)
        rows.append(
            {
                "name": name,
                "seed": entry["seed"],
                "scored_on": entry["scored_on"],
                "scored_at_commit": entry["scored_at_commit"],
                "result_ok": result_now == entry["result_sha256"],
                "result_sha256": entry["result_sha256"],
                "result_sha256_now": result_now,
                "sources_ok": not drifted,
                "drifted_sources": drifted,
            }
        )
    return rows


def assert_results_intact() -> None:
    """The half that is a hard rule. Source drift is reported; an edited answer file is an error."""
    broken = [r for r in verify() if not r["result_ok"]]
    if broken:
        raise HoldoutTampered(
            "a scored holdout no longer matches its recorded hash: "
            + ", ".join(r["name"] for r in broken)
            + ". The single-shot number is whatever was written that day; if the file needed to "
            "change, the change belongs in git history with a reason, not in a silent edit."
        )
