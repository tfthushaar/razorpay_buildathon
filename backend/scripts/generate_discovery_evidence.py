"""Generates a committed, checkable evidence file for category discovery (upgrade build Phase 3):
runs a real batch with `enable_discovery=True` against a real (non-mocked) provider, collects every
genuine_error case's real proposal, and dumps the raw result to docs/evidence/ -- so the claim "this
project proposes a named candidate category instead of just giving up" is independently verifiable
from a committed file, not asserted in prose, mirroring generate_verified_evidence.py's approach for
the calibration-history evidence.

Usage:
    cd backend
    python scripts/generate_discovery_evidence.py [--provider ollama] [--seed 42] [--main-n 150]
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline import run_batch

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="ollama", choices=["ollama", "groq", "mock"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--main-n", type=int, default=150)
    args = parser.parse_args()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"--- running seed={args.seed} main_n={args.main_n} provider={args.provider}, discovery enabled ---")
    result = run_batch(seed=args.seed, main_n=args.main_n, stress_n=0, provider=args.provider, enable_discovery=True)

    genuine_error_count = sum(1 for e in result.escalations if e.category == "genuine_error")
    print(f"genuine_error escalations: {genuine_error_count}")
    print(f"category proposals returned: {len(result.category_proposals)}")
    for p in result.category_proposals:
        print(f"  {p.transaction_id}: proposed_name={p.proposed_name!r} confidence={p.confidence:.2f}")

    out_json = EVIDENCE_DIR / f"discovery-{args.provider}-run-{date.today().isoformat()}.json"
    out_json.write_text(
        json.dumps(
            {
                "seed": args.seed,
                "main_n": args.main_n,
                "provider": args.provider,
                "genuine_error_escalation_count": genuine_error_count,
                "category_proposals": [p.model_dump() for p in result.category_proposals],
            },
            indent=2,
        )
    )
    print(f"\nWrote {out_json} (commit this -- it's the reproducible evidence for category discovery)")


if __name__ == "__main__":
    main()
