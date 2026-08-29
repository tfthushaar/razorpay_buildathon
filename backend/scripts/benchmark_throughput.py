"""Throughput, broken down by scope, with the hardware it was measured on.

Produces docs/evidence/throughput-<date>.json.

This exists because a published throughput figure changed from 5,508 tx/sec to 20,953 tx/sec between
two passes, and the reason was a change of scope rather than a change of speed. The old number timed
`run_batch`'s instrumented region (generation, chains, matching, mock narration). The new one timed
chains and matching alone. Both are true. Quoting them against each other is not.

So every scope is timed separately here and the components sum, which makes the relationship between
any two published figures checkable rather than assertable:

    generate          synthetic batch construction, not part of a production path
    build_chains      order -> payment -> fee -> tax -> refunds -> settlement per transaction
    matching          Pass 1/2, the deterministic resolver
    narrate_mock      the zero-LLM narrator over whatever needs_narration
    tool_context      cross-reference maps the narrator reads

Hardware is recorded because throughput is a hardware claim as much as a code claim. A reader
measuring 1.8x lower on their own machine should be able to see whether that is their CPU or my
arithmetic, and without a recorded baseline the honest answer is "probably hardware", which is not
good enough for a number carrying an economic argument.

Usage:
    cd backend
    python scripts/benchmark_throughput.py [--n 50000] [--repeats 3]
"""

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chain.builder import build_all_chains  # noqa: E402
from app.data_gen.generate import SyntheticDataGenerator  # noqa: E402
from app.matching.engine import run_matching_engine  # noqa: E402
from app.narrator.agent import narrate  # noqa: E402
from app.narrator.tools import build_tool_context  # noqa: E402

# Density matters more than any other parameter here, so both are always reported. The generator's
# default is deliberately denser than reality so every category is exercised at small n; a real
# settlement batch is overwhelmingly clean, and the share reaching a model is what the economic
# argument turns on.
DENSITIES = [("demo_default", 0.60), ("realistic", 0.97)]


def _hardware() -> dict:
    info = {
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "python": platform.python_version(),
        "logical_cpus": None,
        "physical_cpus": None,
    }
    try:
        import os

        info["logical_cpus"] = os.cpu_count()
    except Exception:  # noqa: BLE001
        pass
    try:
        import psutil  # type: ignore

        info["physical_cpus"] = psutil.cpu_count(logical=False)
    except Exception:  # noqa: BLE001
        pass  # psutil is not a dependency; logical count alone is enough to compare machines
    return info


def _time_one(n: int, clean_ratio: float) -> dict:
    gen = SyntheticDataGenerator(seed=42)

    t0 = time.perf_counter()
    batch = gen.generate_main_batch(n, clean_ratio=clean_ratio)
    t_generate = time.perf_counter() - t0

    t0 = time.perf_counter()
    chains = build_all_chains(batch)
    t_chains = time.perf_counter() - t0

    t0 = time.perf_counter()
    results = run_matching_engine(chains)
    t_matching = time.perf_counter() - t0

    t0 = time.perf_counter()
    context = build_tool_context(batch, chains)
    t_context = time.perf_counter() - t0

    queue = [tid for tid, r in results.items() if r.resolution == "needs_narration"]
    t0 = time.perf_counter()
    for txn_id in queue:
        narrate(chains[txn_id], context, provider="mock")
    t_narrate = time.perf_counter() - t0

    closed = len(results) - len(queue)
    return {
        "generate": t_generate,
        "build_chains": t_chains,
        "matching": t_matching,
        "tool_context": t_context,
        "narrate_mock": t_narrate,
        "n": len(results),
        "closed_deterministically": closed,
        "reaching_a_model": len(queue),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50_000)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    hw = _hardware()
    print(f"hardware: {hw['platform']}")
    print(f"          {hw['processor']}, {hw['logical_cpus']} logical CPUs, python {hw['python']}")
    print(f"n={args.n:,} per run, {args.repeats} repeats, reporting the median\n")

    out = {"generated_on": date.today().isoformat(), "n": args.n, "repeats": args.repeats, "hardware": hw, "densities": {}}

    for label, clean_ratio in DENSITIES:
        runs = [_time_one(args.n, clean_ratio) for _ in range(args.repeats)]
        med = {k: statistics.median([r[k] for r in runs]) for k in ("generate", "build_chains", "matching", "tool_context", "narrate_mock")}
        n = runs[0]["n"]
        closed = runs[0]["closed_deterministically"]
        queue = runs[0]["reaching_a_model"]

        # the two published scopes, named so they can never be quoted against each other again
        chains_matching = med["build_chains"] + med["matching"]
        run_batch_region = med["generate"] + med["build_chains"] + med["matching"] + med["tool_context"] + med["narrate_mock"]

        entry = {
            "clean_ratio": clean_ratio,
            "n": n,
            "closed_deterministically": closed,
            "reaching_a_model": queue,
            "closed_share": round(closed / n, 4),
            "component_seconds": {k: round(v, 4) for k, v in med.items()},
            "scopes": {
                "chains_and_matching": {
                    "seconds": round(chains_matching, 4),
                    "tx_per_sec": round(n / chains_matching),
                    "covers": "build_chains + matching",
                },
                "run_batch_timed_region": {
                    "seconds": round(run_batch_region, 4),
                    "tx_per_sec": round(n / run_batch_region),
                    "covers": "generate + build_chains + matching + tool_context + narrate_mock",
                    "note": "the scope behind the previously published 5,508 tx/sec figure",
                },
            },
        }
        out["densities"][label] = entry

        print(f"=== {label} (clean_ratio={clean_ratio}) ===")
        print(f"  {closed:,}/{n:,} closed deterministically ({closed / n:.1%}), {queue:,} reach a model")
        for k, v in med.items():
            print(f"    {k:<16} {v:7.3f}s")
        print(f"  chains + matching only          {chains_matching:7.3f}s = {n / chains_matching:,.0f} tx/sec")
        print(f"  run_batch timed region          {run_batch_region:7.3f}s = {n / run_batch_region:,.0f} tx/sec\n")

    path = Path(args.out) if args.out else Path(__file__).resolve().parents[2] / "docs" / "evidence" / f"throughput-{date.today().isoformat()}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
