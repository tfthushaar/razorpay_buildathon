"""Record what each scored holdout was, and what produced it, as hashes anyone can re-check.

Writes docs/evidence/final/freeze.json and the human-readable FREEZE.md beside it.

`app/final_holdout.py` already refuses to overwrite a scored holdout, which stops it being
re-SCORED. It does nothing about it being re-WRITTEN, and it says nothing about whether the code
that produced the number still exists. Both gaps are real: the three-source holdout was scored on
2026-09-01 and the generator underneath it changed hours later, which was caught by remembering to
write a paragraph rather than by anything that would have failed.

So each entry records the SHA256 of the answer file, and the SHA256 of every file that builds or
scores its input **as those files stood at the commit that added the answer**. Those historical
hashes are read out of git rather than typed, so this cannot quietly certify a state that never
existed. `final_holdout.verify()` recomputes both and reports what moved.

An entry is written once. Re-freezing an existing holdout raises, for the same reason scoring it
twice does.

Usage:
    cd backend
    python scripts/freeze_holdout.py            # write or extend the manifest
    python scripts/freeze_holdout.py --check    # verify only, changes nothing
    python scripts/freeze_holdout.py --reproduce  # also re-run each one and diff the number
"""

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.final_holdout import (  # noqa: E402
    FINAL_DIR,
    FREEZE_DOC,
    HOLDOUT_SOURCES,
    MANIFEST,
    REPO,
    sha256_of,
    verify,
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def _adding_commit(rel: str) -> str:
    """The commit that first added a path -- the moment that holdout was scored."""
    out = _git("log", "--diff-filter=A", "--format=%H", "-1", "--", rel).strip()
    if not out:
        raise SystemExit(f"{rel} is not committed, so there is no scoring commit to freeze against")
    return out


def _sha_at(commit: str, rel: str) -> str | None:
    """Hash a file as it stood at a commit. Read from git, never reconstructed by hand."""
    r = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=REPO, capture_output=True)
    if r.returncode != 0:
        return None
    import hashlib

    return hashlib.sha256(r.stdout).hexdigest()


def build_entry(name: str) -> dict:
    path = FINAL_DIR / f"{name}.json"
    rel_result = path.relative_to(REPO).as_posix()
    commit = _adding_commit(rel_result)
    payload = json.loads(path.read_text(encoding="utf-8"))

    sources = {}
    for rel in HOLDOUT_SOURCES.get(name, []):
        at_scoring = _sha_at(commit, rel)
        if at_scoring is None:
            raise SystemExit(f"{rel} did not exist at {commit[:7]}; the source list for {name} is wrong")
        sources[rel] = at_scoring

    return {
        "seed": payload.get("seed"),
        "scored_on": payload.get("scored_on", str(date.today())),
        "scored_at_commit": commit[:7],
        "result_sha256": sha256_of(path),
        "sources": sources,
    }


def reproduces(name: str, recorded: dict) -> bool | None:
    """Re-run a holdout and report whether it still produces the number that was published.

    This is NOT re-scoring, and the distinction is the whole point of the single-shot rule rather
    than a technicality. The published figure is never replaced by what comes back here: it is
    compared against it. What the rule forbids is swapping a recorded answer for a nicer one; what
    it cannot sensibly forbid is knowing whether the answer still holds.

    A hash tells you the code moved. Only this tells you the number did -- and the two come apart:
    the reading holdout's source changed while its result reproduces to the case.
    """
    from score_final_holdout import score_reading, score_three_source

    scorers = {"reading": score_reading, "three_source": score_three_source}
    if name not in scorers:
        return None
    fresh = scorers[name](recorded["seed"])
    return all(
        fresh.get(cond) == {k: v for k, v in cols.items()}
        for cond, cols in ((c, recorded[c]) for c in fresh)
    )


def render_markdown(manifest: dict, rows: list[dict]) -> str:
    by_name = {r["name"]: r for r in rows}
    out = [
        "# Frozen holdouts",
        "",
        "Each of these was scored once, on a seed no experiment had touched, and whatever came out",
        "is what ships. This file is the receipt: the hash of the answer, and the hashes of the code",
        "that produced it as that code stood on the day.",
        "",
        "Re-check without trusting any of it:",
        "",
        "```bash",
        "cd backend && python scripts/freeze_holdout.py --check",
        "```",
        "",
        "`result` moving means a published number was edited after its single shot. That is an error.",
        "",
        "`sources` moving means the code underneath changed. That is a reason to look, not a verdict:",
        "one of the two holdouts below has drifted sources and still produces its number exactly, and",
        "the other does not reproduce at all. Only `--reproduce` distinguishes them, and it never",
        "rewrites a published figure -- it compares against it.",
        "",
    ]
    for name, entry in sorted(manifest["holdouts"].items()):
        row = by_name.get(name, {})
        out += [
            f"## {name}",
            "",
            f"- **Seed** `{entry['seed']}` — scored {entry['scored_on']} at commit `{entry['scored_at_commit']}`",
            f"- **Result** `{entry['result_sha256']}`",
            f"  — {'intact' if row.get('result_ok') else '**DOES NOT MATCH**'}",
            "- **Sources, as they stood at that commit:**",
            "",
            "| File | SHA256 at scoring | Now |",
            "|---|---|---|",
        ]
        for rel, sha in sorted(entry["sources"].items()):
            current = REPO / rel
            now = sha256_of(current) if current.exists() else None
            state = "unchanged" if now == sha else "**changed**"
            out.append(f"| `{rel}` | `{sha[:16]}…` | {state} |")
        out.append("")
        if row.get("drifted_sources"):
            reproduced = row.get("reproduces")
            if reproduced is True:
                out += [
                    "> The code underneath changed, and this holdout **still produces the number**",
                    "> above, case for case. A changed hash is a reason to check, not a verdict --",
                    "> here the change turned out not to touch the result.",
                    "",
                ]
            elif reproduced is False:
                out += [
                    "> The code underneath changed and this holdout **no longer reproduces**. The",
                    "> figure stands as a record of what happened on the day it was scored and no",
                    "> longer describes what this code would do now. It is not re-scored: replacing",
                    "> a held-out answer once it became inconvenient is the exact thing the",
                    "> single-shot rule exists to prevent.",
                    "",
                ]
            else:
                out += [
                    "> The code underneath changed. Whether the number still comes out has not been",
                    "> checked here -- run with `--reproduce`.",
                    "",
                ]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; write nothing")
    ap.add_argument("--reproduce", action="store_true", help="also re-run each holdout and report whether its number still comes out")
    args = ap.parse_args()

    if not args.check:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"holdouts": {}}
        for path in sorted(FINAL_DIR.glob("*.json")):
            if path.name == MANIFEST.name:
                continue
            name = path.stem
            if name in manifest["holdouts"]:
                continue  # frozen once, like it was scored once
            manifest["holdouts"][name] = build_entry(name)
            print(f"froze {name}")
        manifest["generated_on"] = str(date.today())
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rows = verify()
    if not rows:
        raise SystemExit("nothing frozen yet")

    if args.reproduce:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        for r in rows:
            recorded = json.loads((FINAL_DIR / f"{r['name']}.json").read_text(encoding="utf-8"))
            r["reproduces"] = reproduces(r["name"], recorded)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not args.check:
        FREEZE_DOC.write_text(render_markdown(manifest, rows) + "\n", encoding="utf-8")

    print(f"\n{'holdout':<14}{'seed':>10}  {'result':<12}{'sources'}")
    failed = False
    for r in rows:
        result = "intact" if r["result_ok"] else "ALTERED"
        sources = "unchanged" if r["sources_ok"] else f"changed: {', '.join(Path(p).name for p in r['drifted_sources'])}"
        line = f"{r['name']:<14}{r['seed']:>10}  {result:<12}{sources}"
        if "reproduces" in r:
            line += f"   [number {'still reproduces' if r['reproduces'] else 'NO LONGER reproduces'}]"
        print(line)
        failed |= not r["result_ok"]

    if not args.check:
        print(f"\nWrote {MANIFEST}\nWrote {FREEZE_DOC}")
    if failed:
        raise SystemExit("a scored holdout no longer matches its recorded hash")


if __name__ == "__main__":
    main()
