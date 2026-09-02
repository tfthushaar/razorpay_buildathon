"""Counts stated in prose must match what the files actually contain.

Every one of these has drifted at least once, and one of them drifted four times: the test count
has been published as 543, 567, 580 and 585 while the suite was a different size each time. They are
cosmetic and they all understate the work, which is exactly why nobody notices them -- a number that
flatters gets checked, a number that undersells gets believed.

The worst case is the pair a reader sees together: README said "Seventeen incidents" while
WHAT_BROKE.md's own first line said "Fourteen". Whichever a judge trusts, one of them is wrong on
the same screen, and it costs more credibility than the count itself is worth.

So the prose is asserted against the artefact rather than against my memory of it.
"""

import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent

WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}


def _stated(text: str, noun: str) -> list[int]:
    """Every count stated in prose for `noun`, written as a word or as digits."""
    found = []
    for m in re.finditer(rf"\b([A-Za-z]+|\d+)\s+{noun}\b", text, re.I):
        token = m.group(1).lower()
        if token.isdigit():
            found.append(int(token))
        elif token in WORDS:
            found.append(WORDS[token])
    return found


def _sections(path: Path) -> int:
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")])


def test_every_stated_incident_count_matches_what_what_broke_carries():
    actual = _sections(ROOT / "docs" / "WHAT_BROKE.md")
    for name in ("README.md", "docs/WHAT_BROKE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for stated in _stated(text, "incidents"):
            assert stated == actual, f"{name} says {stated} incidents; WHAT_BROKE.md carries {actual}"


def test_every_stated_limit_count_matches_what_limitations_carries():
    actual = _sections(ROOT / "docs" / "LIMITATIONS.md")
    for name in ("README.md", "docs/LIMITATIONS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for stated in _stated(text, "limits"):
            assert stated == actual, f"{name} says {stated} limits; LIMITATIONS.md carries {actual}"


def test_every_stated_test_count_matches_the_suite():
    """Counted by collecting, not by counting `def test_` -- parametrised cases are real cases and a
    reader running the command in the README sees the collected number, not the function count."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--collect-only", "-p", "no:cacheprovider"],
        cwd=BACKEND, capture_output=True, text=True, timeout=300,
    ).stdout
    m = re.search(r"(\d+) tests collected", out)
    assert m, f"could not read a collected count from pytest output: {out[-400:]!r}"
    actual = int(m.group(1))

    for name in ("README.md", "docs/RESULTS.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for stated in _stated(text, "tests"):
            assert stated == actual, (
                f"{name} says {stated} tests; the suite collects {actual}. "
                "Update the prose, or explain why the two differ."
            )
