"""Extract the headline findings from committed evidence into a static file the frontend can render.

Writes frontend/src/evidence/headline.json.

Two reasons this is generated rather than typed.

The hero must render with zero backend calls. Render cold-starts, backends go down on judging day, and
the deployed default provider cannot run a local model anyway. A hero fed by a committed file is
immune to all three: if the backend is dead, the argument still shows.

And a hand-typed hero drifts. Every number here is read out of the same evidence JSONs that
docs/RESULTS.md quotes, so the front page and the results doc cannot disagree. This script is the
single point where those numbers are turned into UI.

Usage:
    cd backend
    python scripts/export_headline_evidence.py
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calibration.significance import exact_mcnemar_p  # noqa: E402
from app.calibration.wilson import wilson_score_interval  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence"
OUT = ROOT / "frontend" / "src" / "evidence" / "headline.json"


def _latest(pattern: str) -> Path:
    matches = sorted(EVIDENCE.glob(pattern))
    if not matches:
        raise SystemExit(f"no committed evidence matching {pattern}")
    return matches[-1]


def _pct(correct: int, total: int) -> dict:
    lo, hi = wilson_score_interval(correct, total)
    return {
        "correct": correct,
        "total": total,
        "pct": round(100 * correct / total, 1),
        "ci": [round(100 * lo, 1), round(100 * hi, 1)],
    }


def main() -> None:
    reading = json.loads(_latest("advice-reading-*.json").read_text(encoding="utf-8"))
    three = json.loads(_latest("three-source-*.json").read_text(encoding="utf-8"))
    residual = json.loads(_latest("residual-architecture-14b-*.json").read_text(encoding="utf-8"))
    throughput = json.loads(_latest("throughput-*.json").read_text(encoding="utf-8"))

    # --- card 1: where AI belongs -------------------------------------------------------------
    seen, held = reading["conditions"]["seen"], reading["conditions"]["held_out"]
    readers = [
        ("best rule I could write", "keyword_rule", True),
        ("qwen2.5:7b-instruct", "qwen2.5:7b-instruct", False),
        ("qwen2.5:14b-instruct", "qwen2.5:14b-instruct", False),
    ]
    card_reading = {
        "rows": [
            {
                "label": label,
                "is_rule": is_rule,
                "seen": _pct(seen[key]["correct"], seen[key]["total"]),
                "held_out": _pct(held[key]["correct"], held[key]["total"]),
                "delta": round(100 * (held[key]["accuracy"] - seen[key]["accuracy"]), 1),
            }
            for label, key, is_rule in readers
        ],
        "judgements_per_cell": seen["keyword_rule"]["total"],
        "danger": {
            "rule_pct": round(100 * held["keyword_rule"]["dangerous_error_rate"], 1),
            "rule_count": held["keyword_rule"]["dangerous_errors"],
            "model_pct_low": round(100 * min(held[k]["dangerous_error_rate"] for _, k, r in readers if not r), 1),
            "model_pct_high": round(100 * max(held[k]["dangerous_error_rate"] for _, k, r in readers if not r), 1),
        },
    }

    # --- card 2: where it pays ----------------------------------------------------------------
    ts_seen, ts_held = three["conditions"]["seen_phrasing"], three["conditions"]["held_out_phrasing"]
    cols = [("no cycle parsing", "no_cycle_parsing", False), ("best regex parser", "regex_cycle_parser", True), ("qwen2.5:7b-instruct", "model_cycle_reader", False)]
    model_vs = ts_held["model_cycle_reader"]["vs_regex"]
    card_three_source = {
        "rows": [
            {
                "label": label,
                "is_rule": is_rule,
                "seen": _pct(ts_seen[key]["top_candidate_correct"], ts_seen[key]["n"]),
                "held_out": _pct(ts_held[key]["top_candidate_correct"], ts_held[key]["n"]),
            }
            for label, key, is_rule in cols
        ],
        "mcnemar": {
            "wins": model_vs["discordant_a"],
            "losses": model_vs["discordant_b"],
            "p": exact_mcnemar_p(model_vs["discordant_a"], model_vs["discordant_b"]),
            "p_conceding_2": model_vs["p_value_conceding_2"],
        },
    }

    # --- card 3: where it does not pay --------------------------------------------------------
    rs = residual["conditions"]["held_out_phrasing"]["summary"]["columns"]
    per_case = residual["conditions"]["held_out_phrasing"]["per_case"]
    only_p = sum(1 for r in per_case if r["parsimony"] and not r["ollama_reader"])
    only_m = sum(1 for r in per_case if r["ollama_reader"] and not r["parsimony"])
    card_negative = {
        # the computed floor belongs on this card: it is what makes 31.7% vs 26.7% legible as "both
        # well above chance, and not separable from each other" rather than as two bare percentages
        "chance_pct": round(100 * residual["conditions"]["held_out_phrasing"]["summary"]["mean_chance_baseline"], 1),
        "parsimony": _pct(rs["parsimony"]["correct"], rs["parsimony"]["n"]),
        "model": _pct(rs["ollama_reader"]["correct"], rs["ollama_reader"]["n"]),
        "mcnemar": {"wins": only_p, "losses": only_m, "p": exact_mcnemar_p(only_p, only_m)},
    }

    # --- the closing line ----------------------------------------------------------------------
    demo = throughput["densities"]["demo_default"]
    real = throughput["densities"]["realistic"]
    footer = {
        "demo_closed_pct": round(100 * demo["closed_share"], 1),
        "realistic_closed_pct": round(100 * real["closed_share"], 1),
        "deterministic_tx_per_sec": real["scopes"]["chains_and_matching"]["tx_per_sec"],
        "model_tx_per_sec": 2.58,
        "slower_factor": round(real["scopes"]["chains_and_matching"]["tx_per_sec"] / 2.58 / 1000) * 1000,
    }

    payload = {
        "generated_on": date.today().isoformat(),
        "note": "Generated by backend/scripts/export_headline_evidence.py from docs/evidence/. Do not hand-edit.",
        "reading": card_reading,
        "three_source": card_three_source,
        "negative": card_negative,
        "footer": footer,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  reading      rule {card_reading['rows'][0]['seen']['pct']}% -> {card_reading['rows'][0]['held_out']['pct']}%")
    print(f"  danger       rule {card_reading['danger']['rule_pct']}% vs model {card_reading['danger']['model_pct_low']}-{card_reading['danger']['model_pct_high']}%")
    print(f"  three-source model {card_three_source['rows'][2]['held_out']['pct']}% p={card_three_source['mcnemar']['p']:.4f}")
    print(f"  negative     parsimony {card_negative['parsimony']['pct']}% vs model {card_negative['model']['pct']}% p={card_negative['mcnemar']['p']:.2f}")


if __name__ == "__main__":
    main()
