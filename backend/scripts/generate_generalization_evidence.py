"""Does the matcher stay safe on defect shapes nobody designed it for?

Produces docs/evidence/generalization-<date>.json.

Every accuracy figure in RESULTS is scored against categories the matching engine's rules were
written knowing. That measures implementation, not generalisation. This runs the ordinary pipeline
over four shapes absent from the taxonomy entirely and scores the only question that matters on
input it does not understand: did it stay safe, or did it guess?

Three outcomes per transaction, and only one is a failure:

    resolved correctly   it generalised. A bonus, never the pass criterion.
    escalated            the right answer on input it does not understand
    RESOLVED WRONGLY     the failure. A machine closed a case it had no basis to close.

A fourth would be worse: a transaction in neither the resolved set nor the queue, silently dropped.

THE GATE IS NOT AN ACCURACY FLOOR. Escalating all 32 novel transactions passes. What fails is one
wrong resolution or one dropped transaction, on the argument this project makes throughout: in
reconciliation a false positive is materially worse than an escalation.

The 12 in-distribution controls must still resolve, so a pipeline that escalated everything could
not pass this by refusing to work.

Usage:
    cd backend
    python scripts/generate_generalization_evidence.py
"""

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains  # noqa: E402
from app.data_gen.novel_shapes import NOVEL_SHAPES, generate_novel_batch  # noqa: E402

from app.chain.controls import run_data_integrity_controls  # noqa: E402
from app.matching.engine import run_matching_engine  # noqa: E402

# No declared blind spots. An earlier version declared `stale_utr_reuse` unfixable here, on the
# argument that a causal chain carries no UTR. That was right about the chain and wrong about the
# batch: a settlement carries a UTR, and "a payout reference identifies one payout" is an invariant
# checkable without any matching heuristic. The declaration was also propping up a badly built
# shape -- the generator gave each of those settlements a fresh random UTR, so there was no reuse to
# detect. Both fixed; the dict stays so a future blind spot has to be declared rather than ignored.
BLIND_SPOTS: dict[str, str] = {}


def main() -> None:
    batch, shape_of = generate_novel_batch()
    chains = build_all_chains(batch)
    results = run_matching_engine(chains)

    # A transaction is handled safely if the matcher escalated it OR a data-integrity control
    # flagged it. Controls assert invariants the matcher has no input to check -- it never reads
    # settled_at or a UTR -- and they flag rather than resolve.
    flagged = {f.transaction_id for f in run_data_integrity_controls(batch)}

    per_shape = defaultdict(lambda: {"n": 0, "resolved": 0, "resolved_wrongly": 0, "escalated": 0, "flagged_by_control": 0, "categories": defaultdict(int)})
    dropped = []

    for txn_id, result in results.items():
        shape = shape_of.get(txn_id, "unknown")
        row = per_shape[shape]
        row["n"] += 1
        if result.resolution == "needs_narration":
            row["escalated"] += 1
        elif txn_id in flagged:
            row["flagged_by_control"] += 1
        else:
            row["resolved"] += 1
            row["categories"][result.category or "?"] += 1
            # A novel shape has no correct resolution: the engine cannot have had a basis for one,
            # and no control caught it either, so this one went through clean.
            if shape in NOVEL_SHAPES:
                row["resolved_wrongly"] += 1

    for txn_id in chains:
        if txn_id not in results:
            dropped.append(txn_id)

    novel_wrong = sum(per_shape[s]["resolved_wrongly"] for s in NOVEL_SHAPES if s not in BLIND_SPOTS)
    blind_spot_wrong = sum(per_shape[s]["resolved_wrongly"] for s in BLIND_SPOTS)
    controls = per_shape["clean_match"]
    controls_resolved = controls["resolved"]
    passed = novel_wrong == 0 and not dropped and controls_resolved == controls["n"]

    print(f"{'shape':<24}{'n':>4}{'resolved':>10}{'WRONG':>7}{'escalated':>11}{'flagged':>9}")
    for shape in ("clean_match", *NOVEL_SHAPES):
        r = per_shape[shape]
        print(f"{shape:<24}{r['n']:>4}{r['resolved']:>10}{r['resolved_wrongly']:>7}{r['escalated']:>11}{r['flagged_by_control']:>9}")

    print("\n  pass = no wrong resolution outside a declared blind spot, nothing dropped, controls still resolve")
    print(f"  novel transactions: {sum(per_shape[s]['n'] for s in NOVEL_SHAPES)}, resolved wrongly: {novel_wrong}")
    for shape, why in BLIND_SPOTS.items():
        print(f"  DECLARED BLIND SPOT: {shape} -- {per_shape[shape]['resolved_wrongly']} of {per_shape[shape]['n']} resolved clean. {why}")
    print(f"  controls resolved:  {controls_resolved}/{controls['n']}")
    print(f"  silently dropped:   {len(dropped)}")
    print(f"\n  {'PASS' if passed else 'FAIL'}")

    payload = {
        "generated_on": date.today().isoformat(),
        "seed": 909,
        "pass_criterion": "no wrong resolution outside a declared blind spot, nothing dropped, controls still resolve",
        "passed": passed,
        "novel_transactions": sum(per_shape[s]["n"] for s in NOVEL_SHAPES),
        "novel_resolved_wrongly": novel_wrong,
        "blind_spots": {k: {"reason": v, "resolved_wrongly": per_shape[k]["resolved_wrongly"]} for k, v in BLIND_SPOTS.items()},
        "controls_resolved": controls_resolved,
        "controls_total": controls["n"],
        "silently_dropped": len(dropped),
        "per_shape": {k: {**v, "categories": dict(v["categories"])} for k, v in per_shape.items()},
    }
    out = Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"generalization-{date.today().isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    if not passed:
        raise SystemExit("GATE FAILED: see the table above")


if __name__ == "__main__":
    main()
