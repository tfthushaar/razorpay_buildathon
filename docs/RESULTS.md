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
| Time-to-revocation drill | 1 wrong decision (₹500) revoked a category's auto-resolve status after 40 clean decisions, even with the all-time aggregate still at 97.6% | `curl -X POST localhost:8000/api/drift/drill -H 'Content-Type: application/json' -d '{}'` |
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
`app/forecast/blind_backtest.py`) the predictor never sees. This batch has no refunds or timing
anomalies at all — schedule drift is the *only* source of error here, unlike the non-blind number
above, whose error comes almost entirely from the ~27% with a refund/dispute/anomaly — so the two
numbers answer different questions, not the same one twice. Measured over seeds 1–20 at n=120:

| Metric | Mean | Range across seeds |
|---|---|---|
| MAPE | 0.11% | 0.02%–0.17% |
| Interval coverage | 56.5% | 3%–100% |

The amount forecast barely moves (a fee is a small fraction of settled value even with real-rate
drift), but interval coverage is highly sensitive to SLA drift specifically — a few days of real
timing drift can push the actual settlement date entirely outside the predictor's own narrow
tolerance window. Reproduce a single seed: `GET /api/forecast/blind-backtest?seed=42&n=120` (45.8%
coverage — seed 1 is the top of the 20-seed range at 100%, seed 4 the bottom at 3.3%; seed 42 is
picked here for being unremarkable, not for being representative of any particular tail).

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

## A task the rule provably can't do, shipped as a real product category

`check_batch_anomalies` only checks pairs — a netting pattern spanning 3+ transactions, where no
single pair cancels, is invisible to it by construction. `multiway_netting_trap`
(`app/data_gen/generate.py`, opt-in via `enable_multiway_netting`) brings this into the real decision
loop: `list_batch_deltas`/`verify_group_sum` wired into the production narrator's own `TOOL_SCHEMAS`,
gated behind calibration like every other category. Measured on real generated batches (150
transactions each, small dedicated groups — not the hand-built stress test below):

| Provider | Accuracy |
|---|---|
| mock | 0/42 — structural, confirmed empirically (still 100% on every other category in the same batches) |
| Ollama (`qwen2.5:7b-instruct`) | 5/7 |
| Groq (`openai/gpt-oss-20b`) | 6/7 |

Reproduce: `python scripts/measure_mock_narrator_accuracy_multiway.py`,
`python scripts/generate_multiway_netting_trap_production_evidence.py`. Raw evidence:
[`multiway-netting-trap-production-2026-08-29.json`](evidence/multiway-netting-trap-production-2026-08-29.json).

## The same task, at real settlement-batch scale (500-800 transactions)

The product category above uses small, dedicated groups by design (calibration needs many
independent small cases, not one giant puzzle — see [ARCHITECTURE.md](ARCHITECTURE.md)). A separate
experiment (`app/narrator/multiway_netting_scale_experiment.py`) tests the same underlying capability
at a scale a real high-volume merchant's settlement batch could actually have. The original
hypothesis was that raw context size would be the wall; what was actually measured is two different
failure modes on two different providers:

- **Ollama fails at every scale tested (20 through 760 transactions), 0/36 across the whole sweep** —
  and not from context overflow. The raw tool-call traces show it accumulating an ever-growing
  candidate list across rounds instead of searching small subsets systematically, confirmed as a
  reasoning-strategy limit (not a token-budget one) by Groq solving the identical n=20 case correctly
  on the first attempt.
- **Groq hits a real, literal wall — later, and as a hard error, though not the exact one first
  assumed.** Solves n=20 correctly (2/2), gives a mix of correct answers and empty/unparseable
  responses by n=100, then at n≥200 every call in this sweep returned a real `429` — but the error
  message itself (`"Rate limit reached... on tokens per day (TPD): Limit 200000, Used 199594..."`)
  shows this is the account's free-tier **daily token quota**, exhausted by this session's own
  cumulative Groq usage across every earlier phase, not a per-request context-size limit specific to
  large batches. A genuinely isolated n=400 call (fresh quota) does return `413 Request too large`
  for `openai/gpt-oss-20b` — confirmed directly in an earlier manual check — so the context-size wall
  is real too, just confounded with quota exhaustion in this particular sweep's own committed run.
  Disclosed as the honest, if messier, finding rather than smoothed into a single clean threshold.
- **A magnitude pre-filter does not cleanly rescue either failure mode**, measured directly: loose
  enough to rarely discard the real answer (10x tolerance), it barely narrows the candidate set
  against this experiment's own uniformly-distributed distractor deltas (494 of 499 shown at n=500).
  Tight enough to actually shrink the request (1.5x) pushes the real-answer discard rate over 40%.

Reproduce: `python scripts/generate_multiway_netting_scale_evidence.py`. Raw evidence:
[`multiway-netting-scale-experiment-2026-08-29.json`](evidence/multiway-netting-scale-experiment-2026-08-29.json).

## The strongest deterministic rule actually built for this task

Not a claim that a rule "could theoretically be extended" — real k-sum algorithms
(`app/narrator/multiway_netting_optimal_solver.py`): 2-sum via a hash pass (O(n)), 3-sum via sort +
two-pointer (O(n²)), 4-sum via meet-in-the-middle (O(n²)) — replacing brute force's O(n^k), correctness-
checked against the brute-force solver on identical inputs before any speed claim was trusted.

| n_total | Optimal solver | Brute force |
|---|---|---|
| 100 | 0.00002s | 0.0005s |
| 500 | 0.00007s | 0.0213s |
| 1,000 | 0.00006s | skipped — already shown impractical |
| 5,000 | 0.00039s | skipped |

The real frontier isn't compute time, it's disambiguation. At this project's own delta range
(±999,931 paise), the optimal solver reliably finds the TRUE constructed group up to `n_total=1000`
(100% across 30 seeds), then degrades — 77% at 2,000, 53% at 3,000, 27% by 5,000 — because a
spurious-but-genuinely-valid coincidental match becomes more likely than the real one (a
birthday-paradox effect in a finite integer range, not a speed problem; every "wrong" answer still
genuinely cancels the target, just isn't the one constructed).

Reproduce: `python scripts/generate_multiway_netting_optimal_solver_evidence.py`. Raw evidence:
[`multiway-netting-optimal-solver-2026-08-29.json`](evidence/multiway-netting-optimal-solver-2026-08-29.json).

## Breaking the "shared author" problem

On `duplicate_refund`/`netting_trap`, mock scores 100% because the same author wrote both the
generator's injectors and `check_batch_anomalies`'s detector to the same exact-match definition — see
"Where the rule beats the LLM" below. Held-out near-miss variants
(`enable_held_out_variants`, `app/data_gen/generate.py`) are still genuinely the same true category,
perturbed by a small, disclosed epsilon the exact-match rule can never confirm:

| Provider | Accuracy |
|---|---|
| mock | 0/101 — expected, confirmed empirically |
| Ollama (`qwen2.5:7b-instruct`) | 0/21 |

Ollama does **not** generalize past the rule's brittleness here either — but the raw reasoning traces
show a more interesting failure than "can't do arithmetic": several traces correctly notice the
near-cancellation, then the model's own `verify_group_sum` call (a strict exact-zero check, correct
for `multiway_netting_trap`) reports the candidate doesn't cancel exactly, and the model — following
its own instruction to never assert an unverified explanation — appropriately declines rather than
guess. The same cautious tool-use discipline this project credits elsewhere works against success on
this specific task, a real tool-design tension, not a reasoning failure.

Reproduce: `python scripts/generate_held_out_variant_evidence.py`. Raw evidence:
[`held-out-variant-evidence-2026-08-29.json`](evidence/held-out-variant-evidence-2026-08-29.json).

## A category that genuinely requires reading, not a lookup

`narration_explained` (`enable_narration_explained`): a delta explained only by the settlement's own
free-text remarks field (`Settlement.bank_narration`, eight varied, realistically messy templates) —
never by any structured field or delta-arithmetic a rule could check at any scale, not even the
combinatorial `multiway_netting_trap` machinery.

| Provider | Accuracy |
|---|---|
| mock | 0/64 — never calls `read_bank_narration`, structural |
| Ollama (`qwen2.5:7b-instruct`) | **10/10** |

No tool-design tension here (unlike the held-out variants above) — reading comprehension over free
text has no strict-verification step to conflict with, so the model's own capability is free to work,
cleanly.

Reproduce: `python scripts/generate_narration_explained_evidence.py`. Raw evidence:
[`narration-explained-evidence-2026-08-29.json`](evidence/narration-explained-evidence-2026-08-29.json).

## Which model, measured — not an anecdote

Compares `qwen2.5:7b-instruct` against `qwen2.5:14b-instruct` (both confirmed pulled/running locally)
on the two categories actually shown to be hard above — deliberately not re-sweeping the easy
categories, where every model size is already expected to score ~100%.

| Category | 7b | 14b |
|---|---|---|
| `multiway_netting_trap` | 4/7 | **1/7** |
| `narration_explained` | 4/5 | 5/5 |

The larger model does *worse* on the tool-budget-constrained task: reading the raw traces, 14b
explores more per case (redundant `recall_similar_resolutions` calls, checking irrelevant tools) and
more often runs out of the same 6-round budget before converging. On the pure-reading task, with no
budget tension, the larger model's extra capacity has room to help. Reported as measured, not tuned —
the honest, apples-to-apples comparison under an identical budget is the finding, not a number to
optimize away.

Reproduce: `python scripts/generate_multi_model_evidence.py`. Raw evidence:
[`multi-model-evidence-2026-08-29.json`](evidence/multi-model-evidence-2026-08-29.json).

## Real load, measured against a live server

Not the in-process `TestClient` concurrency tests already use — real HTTP requests against a
genuinely running server (`scripts/load_test.py`), `POST /api/run`, 3 requests per worker at each
concurrency level:

| Concurrency | Requests | Succeeded | Errors | Mean latency |
|---|---|---|---|---|
| 1 | 3 | 3 | 0 | 2.157s |
| 8 | 24 | 24 | 0 | 2.726s |
| 32 | 96 | 96 | 0 | 4.750s |

100% success at every concurrency level tested, zero errors. Latency degrades gracefully (roughly
2.2x at 32x the concurrency), not catastrophically. This doesn't prove single-instance SQLite scales
indefinitely — it means "this architecture would fall over under real concurrent load" isn't
supported by what was actually measured in this range (see [LIMITATIONS.md](LIMITATIONS.md)).

Reproduce: start the server, then `python scripts/load_test.py`. Full table:
[`load-test-2026-08-29.txt`](evidence/load-test-2026-08-29.txt).

## The original hand-built experiment, kept for context

Before `multiway_netting_trap` shipped as a real category, this project measured the same underlying
task with a smaller, hand-built stress case (a target transaction plus 10 others — one real 2-member
group, 8 distractors — deltas varying per seed, no other subset coincidentally cancelling, verified
by brute force):

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
fewer), 1 more returned unparseable content — leaving 3 that actually answered, of which 1 was
correct. Ollama's *non-verified* baseline had 0 empty responses in 8, but 2 were unparseable/garbled
(the same non-answer category as Groq's, above) and the other 6 answered and were simply wrong — a
different failure shape than the verified condition's non-convergence.

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
cd backend && python -m pytest tests/ -v                                          # 280 tests
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
python scripts/measure_mock_narrator_accuracy.py
python scripts/measure_mock_narrator_accuracy_multiway.py
python scripts/generate_multiway_netting_trap_production_evidence.py
python scripts/generate_multiway_netting_optimal_solver_evidence.py
python scripts/generate_held_out_variant_evidence.py
python scripts/generate_narration_explained_evidence.py
```

Full reproduction notes, including why `backend/data/*.db` is gitignored and what to use instead:
see the comments in [`scripts/audit_calibration.py`](../backend/scripts/audit_calibration.py).
