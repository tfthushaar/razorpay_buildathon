# Results

Every number this project cites, with the command that reproduces it. Nothing here is retyped from
a dashboard — each row is a real run against this repo's own committed evidence or a fresh batch.

## Core reconciliation

| Claim | Number | Reproduce |
|---|---|---|
| Batch size | 50,000 (mock) / 120 (real Ollama) | [50k run](evidence/50k-batch-run-2026-08-25.json) |
| Match rate, real provider | 99.3% of settlement value, 7 escalations of 120 | see "The calibrated-autonomy result" below |
| Match rate, mock provider | 86.0% reconciled, 18 escalations of 120 — mock deliberately escalates every LLM-routed category rather than auto-resolve on unearned trust | `run_batch(seed=42, main_n=120, stress_n=40, provider='mock')` |
| Throughput | 5,508 tx/sec (mock, 50k scale) — 2.58 tx/sec (real LLM, measured) | [SETUP.md](SETUP.md) |
| `netting_trap` | 59 distinct real cases, 98.3% accuracy, 91.0% Wilson lower bound, ₹4,86,473.13 auto-resolved | `python scripts/audit_calibration.py --db evidence/verified_calibration_history.db` |
| `duplicate_refund` | 37 distinct real cases, 100% accuracy | same command |
| `genuine_error` | 80.3% accuracy, stays escalated regardless — never auto-resolves by design | same command |

## The calibrated-autonomy result

After 8 real, honestly-accumulated Ollama batches, `netting_trap` cleared the 90% trust threshold
(95% Wilson lower bound 91.0%) and `duplicate_refund` did the same separately. `genuine_error` sat
at 80.3% across the same evidence and stayed escalated anyway — it never auto-resolves regardless of
measured accuracy, by construction, since a misclassification there should cost a human a glance, not
become a wrong autonomous action.

## Failure Recovery

| Experiment | Result | Reproduce |
|---|---|---|
| Time-to-revocation drill | 1 wrong decision (₹500) revoked a category's auto-resolve status after 40 clean decisions, even with the all-time aggregate still at 97.6% | `POST /api/drift/drill` |
| Regret in rupees | ₹0 realized regret across 8 real auto-resolved transactions so far — the realized cost of autonomy, not `amount_at_risk`'s forward-looking estimate | `GET /api/regret` |

## Fee-leak detection / tax-line matcher

| Claim | Number | Reproduce |
|---|---|---|
| Fee-leak review batch | 10 blended-rate overcharges (₹2,634.50 recoverable), 10 GST-wrong-base findings (₹23,158.96 miscalculated tax) | `run_fee_leak_detection(...)`, see `test_fee_leak.py` |
| False positive rate | 0 false positives across 260 ordinary transactions from the main/stress batches | `test_zero_false_positives_against_every_existing_category` |
| GSTR-2B match (seed=42, main_n=150) | 120 matched (₹2,740.19), 30 exceptions (₹444.17) across 3 disjoint mismatch kinds | `GET /api/gstr2b` |

## Forward cash forecaster

| Batch size | MAPE | Interval coverage |
|---|---|---|
| n=30 (dashboard default) | 9.1% | 93.3% |
| n=120 (API default) | 8.6% | 90.8% |
| n=160 | 4.1% | 90.6% |

The forecaster reuses the merchant's own known fee/SLA schedule (real reference data, not learned),
so it's exact to the paise on ~73% of transactions by construction (verified: 88/120 exact matches at
n=120). The reported MAPE/coverage come entirely from the ~27% with a refund, dispute, or timing
anomaly it structurally can't see in advance. Reproduce: `GET /api/forecast/backtest`.

## Where the rule beats the LLM, measured directly

`narrate_mock` — a 20-line rule with zero LLM calls — scores **100.0% across 519 real
narration-queue cases**, on all three categories the real narrator is reserved for:

```
cd backend && python scripts/measure_mock_narrator_accuracy.py
TOTAL: 519/519 = 100.0%
  duplicate_refund: 96/96 = 100.0%
  genuine_error: 117/117 = 100.0%
  netting_trap: 306/306 = 100.0%
```

The real Ollama/Groq narrator does not match this (98.3%/80.3% above). Cause, confirmed by reading
both sides: the same author wrote the generator's injectors and `check_batch_anomalies`'s detector,
so both share exact-match logic — there is no genuine ambiguity left for a classifier to resolve on
this task. The real-provider call earns autonomy here for reliability under conditions the rule never
faces (API failures, malformed tool arguments, a hallucinated id), not for resolving a case the rule
genuinely couldn't.

## One task the rule provably can't do

`check_batch_anomalies` only checks pairs — a netting pattern spanning 3+ transactions, where no
single pair cancels, is invisible to it by construction. Measured against a hand-built case (a target
transaction plus 10 others — one real 2-member group, 8 distractors — deltas varying per seed, no
other subset coincidentally cancelling, verified by brute force):

| Provider | Without a verification tool | With `verify_group_sum` |
|---|---|---|
| Groq (`openai/gpt-oss-20b`) | 1/8 (4/8 on a prior run — real sampling variance, both kept) | **8/8, both times run** |
| Ollama (`qwen2.5:7b-instruct`, local) | 0/8 | 1/8 |

The rule scores 0/8 on this task, always, structurally. Giving the model a way to verify its own
hypothesis before answering — rather than asserting a plausible-sounding one — closed most of the
gap for the capable model and made the result far more stable across repeated runs. Full raw evidence
for all four conditions, all 8 seeds each: [`multiway-netting-experiment-2026-08-28.json`](evidence/multiway-netting-experiment-2026-08-28.json).
Reproduce: `python scripts/generate_multiway_netting_evidence.py`.

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                                          # 207 tests
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
python scripts/measure_mock_narrator_accuracy.py
python scripts/generate_multiway_netting_evidence.py
```

Full reproduction notes, including why `backend/data/*.db` is gitignored and what to use instead:
see the comments in [`scripts/audit_calibration.py`](../backend/scripts/audit_calibration.py).
