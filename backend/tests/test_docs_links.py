"""Every link in the documentation resolves, including its anchor.

WHY THIS EXISTS AS A TEST RATHER THAN A SCRIPT I RUN. Two links shipped broken and survived fifteen
passes of an ad-hoc checker. Both had a destination wrapped across a line break by a paragraph
re-wrapper:

    ([METHODS.md](METHODS.md#the-autonomy-gate-and-
    why-wilson-was-the-wrong-bound))

A CommonMark destination cannot contain a newline, so GitHub renders that as literal text. The reader
sees half a URL at the end of one line and the rest at the start of the next, on the sentence
carrying the gate result.

The checker missed it for the reason bad checkers usually miss things: its pattern did not match
across newlines, so a wrapped link was not a failure, it was invisible. The rule this file follows is
that a link it cannot parse is a FAILURE, never a skip. `test_no_link_destination_contains_a_newline`
is the specific guard; `test_every_bracket_pair_parses_as_a_link` is the general one that would have
caught it even without knowing the failure mode.
"""

import re
from pathlib import Path

import pytest

DOCS_ROOT = Path(__file__).resolve().parents[2]

# A well-formed inline link: no whitespace at all inside the destination.
WELL_FORMED = re.compile(r"\[[^\]]*\]\((?P<dest>[^)\s]+)\)")
# Anything shaped like a link, however malformed, so a broken one cannot slip past unnoticed.
LINK_SHAPED = re.compile(r"\][ ]*\((?P<dest>[^)]*)\)", re.DOTALL)


# Named files go stale the moment one is moved, and the failure is silent: the checker keeps passing
# on a smaller set. WHAT_BROKE.md moved to the repo root and BUILD_LOG.md moved into docs/, and the
# old hard-coded list would simply have stopped checking one of them. Globbing both locations cannot
# drift that way, and `test_the_important_docs_are_actually_being_checked` fails loudly if a move
# ever takes one out of range entirely.
def _markdown_files() -> list[Path]:
    seen, files = set(), []
    for path in sorted(DOCS_ROOT.joinpath("docs").glob("*.md")) + sorted(DOCS_ROOT.glob("*.md")):
        if path.exists() and path.resolve() not in seen:
            seen.add(path.resolve())
            files.append(path)
    return files


def _headings(path: Path) -> set[str]:
    """GitHub's anchor slugs: lowercase, punctuation dropped, spaces to hyphens."""
    slugs = set()
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", path.read_text(encoding="utf-8")):
        text = re.sub(r"[`*_]", "", heading)
        slugs.add(re.sub(r"[^a-z0-9\s-]", "", text.lower()).strip().replace(" ", "-"))
    return slugs


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_no_link_destination_contains_a_newline(path: Path):
    """The exact bug. A wrapped destination renders as literal text, not a link."""
    broken = re.findall(r"\][ ]*\([^)]*\n[^)]*\)", path.read_text(encoding="utf-8"))
    assert not broken, f"{path.name}: destination wrapped across a line break: {broken}"


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_every_bracket_pair_parses_as_a_link(path: Path):
    """The general guard. Anything link-shaped must also be well formed, so a malformed destination
    fails loudly instead of being skipped by a stricter pattern that never matched it."""
    text = path.read_text(encoding="utf-8")
    unparseable = [
        m.group(0)[:90].replace("\n", "\\n")
        for m in LINK_SHAPED.finditer(text)
        if m.group("dest").strip() and re.search(r"\s", m.group("dest").strip())
    ]
    assert not unparseable, f"{path.name}: link destination contains whitespace: {unparseable}"


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_relative_file_links_resolve(path: Path):
    missing = []
    for match in WELL_FORMED.finditer(path.read_text(encoding="utf-8")):
        dest = match.group("dest")
        if dest.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = (path.parent / dest.split("#", 1)[0]).resolve()
        if not target.exists():
            missing.append(dest)
    assert not missing, f"{path.name}: links to files that do not exist: {missing}"


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_anchors_resolve_against_their_target_headings(path: Path):
    """The half the old checker truncated away. It split on '#' and only verified the file existed,
    so a correct file with a wrong anchor passed."""
    bad = []
    for match in WELL_FORMED.finditer(path.read_text(encoding="utf-8")):
        dest = match.group("dest")
        if dest.startswith(("http://", "https://", "mailto:")) or "#" not in dest:
            continue
        file_part, anchor = dest.split("#", 1)
        target = (path.parent / file_part).resolve() if file_part else path
        if not target.exists() or target.suffix != ".md":
            continue
        if anchor and anchor not in _headings(target):
            bad.append(f"{dest} (no heading in {target.name})")
    assert not bad, f"{path.name}: anchors with no matching heading: {bad}"


def test_the_checker_would_catch_the_bug_it_was_written_for():
    """A checker nobody has seen fail is a checker nobody knows works."""
    wrapped = "see ([METHODS.md](METHODS.md#the-autonomy-gate-and-\nwhy-wilson-was-the-wrong-bound))"
    assert re.findall(r"\][ ]*\([^)]*\n[^)]*\)", wrapped), "the newline guard no longer fires"
    assert not WELL_FORMED.search(wrapped), "a wrapped destination must not read as well formed"


def test_the_important_docs_are_actually_being_checked():
    """A link checker that quietly stops seeing a file is worse than not having one."""
    names = {f.name for f in _markdown_files()}
    for required in ("README.md", "WHAT_BROKE.md", "RESULTS.md", "LIMITATIONS.md", "BUILD_LOG.md"):
        assert required in names, f"{required} is not being link-checked -- did it move?"
