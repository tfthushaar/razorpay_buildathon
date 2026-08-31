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

import json
from datetime import date
from pathlib import Path
from typing import Any

FINAL_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "final"

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
