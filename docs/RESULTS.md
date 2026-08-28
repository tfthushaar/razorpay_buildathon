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
| `netting_trap` | 59 distinct real cases, 98.3% accuracy, 91.0% Wilson lower bound, ₹4,86,473.13 auto-resolved | `cd backend && python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db` |
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
| Time-to-revocation drill | 1 wrong decision (₹500) revoked a category's auto-resolve status after 40 clean decisions, even with the all-time aggregate still at 97.6% | `curl -X POST localhost:8000/api/drift/drill -d '{}'` |
| Regret in rupees | ₹0 realized regret across 8 real auto-resolved transactions in the committed evidence db — the realized cost of autonomy, not `amount_at_risk`'s forward-looking estimate | `GET /api/regret` reads whatever history has actually accumulated locally — 0/0 on a fresh clone until real batches run; the 8 above is `verified_calibration_history.db`'s own accumulated total |

## Cross-run memory for `recall_similar_resolutions`

Live-verified against this project's own accumulated `backend/data/audit_log.db` (gitignored, local
only): a brand-new run's first call for each category, before that run had narrated anything of its
own —

| Category | Prior count seen | Avg confidence |
|---|---|---|
| `genuine_error` | 612 | 0.315 |
| `netting_trap` | 834 | 0.856 |
| `duplicate_refund` | 428 | 0.904 |

Reproduce: run a batch twice against the same `AuditLogger` db path (`POST /api/run` twice against a
running server uses this automatically), then `GET /api/audit?run_id=<second_run_id>` and inspect any
narrated entry's `tool_calls_json` for the `recall_similar_resolutions` call.

## Fee-leak detection / tax-line matcher

| Claim | Number | Reproduce |
|---|---|---|
| Fee-leak review batch (seed=42, n=20) | 7 blended-rate overcharges, 7 GST-wrong-base, 6 GST-wrong-rate — ₹1,497.40 recoverable fees, ₹15,181.65 miscalculated tax | `run_fee_leak_detection(...)`, see `test_fee_leak.py` |
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

**A genuinely-blind backtest, separately.** The numbers above share one schedule between the
predictor and the batch that scores it — a real gap, since a merchant's actual contracted rate or
real settlement timing can drift from what a platform's own schedule still assumes.
`GET /api/forecast/blind-backtest` scores the same predictor against a self-contained batch whose
real settlements are computed with a hidden, per-rail fee-rate/SLA-day drift (up to 15% / 2 days,
`app/forecast/blind_backtest.py`) the predictor never sees. Measured over seeds 1–20 at n=120:

| Metric | Mean | Range across seeds |
|---|---|---|
| MAPE | 0.11% | 0.02%–0.17% |
| Interval coverage | 56.5% | 3%–100% |

The amount forecast barely moves (a fee is a small fraction of settled value even with real-rate
drift), but interval coverage is highly sensitive to SLA drift specifically — a few days of real
timing drift can push the actual settlement date entirely outside the predictor's own narrow
tolerance window. Reproduce a single seed: `GET /api/forecast/blind-backtest?seed=1&n=120`.

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

The raw fractions overstate how much of this is reasoning failure — read all 32 raw responses, not
just the scores. **Groq without verification**: 4 of 8 turns returned empty/unparseable content (a
non-answer, not a wrong one); of the 4 that actually answered, 1 was correct. **With verification,
all 8 answered and all 8 were correct** — the tool didn't just raise accuracy, it eliminated the
non-answers entirely. **Ollama with verification**: 4 of 8 never converged within the tool-call
budget (the extra round-trip the tool adds gives a small model more chances to get stuck, not
fewer); of the 4 that did answer, 1 was correct. Ollama's *non-verified* baseline never failed to
answer (0 non-answers in 8) — it was simply wrong or garbled every time it didn't get it right,
a different failure shape than the verified condition's non-convergence.

One specific Ollama failure is worth naming directly: asked to investigate `order_f9d807a89e11`,
the model queried the tool with the id `f9d807a89e11` (missing the `order_` prefix — its own
error, not a garbled tool result), got back a real, correct `{"error": "no transaction ... in this
batch"}`, and then answered *"this transaction is alone in its settlement batch"* — narrating a
tool error as a confirmed finding rather than recognizing the lookup had failed. This is the exact
failure mode the production narrator's schema/id validation exists to catch (see
[WHAT_BROKE.md](WHAT_BROKE.md)); this experiment's own harness has no equivalent guard, since it's
a one-off measurement, not shipped code.

Full raw evidence for all four conditions, all 8 seeds each:
[`multiway-netting-experiment-2026-08-28.json`](evidence/multiway-netting-experiment-2026-08-28.json).
Reproduce: `python scripts/generate_multiway_netting_evidence.py`.

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                                          # 221 tests
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
python scripts/measure_mock_narrator_accuracy.py
python scripts/generate_multiway_netting_evidence.py
```

Full reproduction notes, including why `backend/data/*.db` is gitignored and what to use instead:
see the comments in [`scripts/audit_calibration.py`](../backend/scripts/audit_calibration.py).
