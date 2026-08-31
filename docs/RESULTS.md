# Results

Every number with the command that reproduces it, strongest first. Each section is a table, one
paragraph on what it means, and a link.

Derivations live in [METHODS.md](METHODS.md) and are not needed to read this file: the confidence
sequence behind the autonomy gate, the estimated match weights, the semantic-entropy null, and why
the forecast interval over-covers. Superseded experiments:
[RESULTS_SUPERSEDED.md](RESULTS_SUPERSEDED.md).

Comparisons run over identical cases, so the tests are paired rather than a comparison of two
independent intervals.

## Reading remittance advice: model vs. regex

Only the reading stage is compared. The rule is written to win: fragment splitting, cause keywords,
and a 29-entry negation-cue list assembled with full sight of the generator's phrasing.

| Reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| best keyword rule | 95.2% [92.8, 96.9] | 61.7% [56.9, 66.2] | -33.6 |
| `qwen2.5:7b-instruct` | 79.8% [75.7, 83.3] | 72.6% [68.2, 76.7] | -7.1 |
| `qwen2.5:14b-instruct` | 86.9% [83.3, 89.8] | 81.7% [77.7, 85.1] | -5.2 |
| `openai/gpt-oss-20b` | 92.1% [89.2, 94.4] | 96.2% [93.9, 97.6] | +4.0 |

420 judgements per cell, 95% Wilson intervals; on held-out the rule's interval overlaps no model's.
Held-out keeps the cause vocabulary and changes only how applied-versus-not is said, and a test
asserts it contains none of the rule's cues. `gpt-oss-20b` is a second family whose `keyword_rule`
column reproduces the committed one exactly, so the finding is about models rather than about qwen.

The two failure modes are not interchangeable.

| Reader | Condition | Reads a denial as a confirmation | Misses a mention |
|---|---|---|---|
| keyword rule | seen | 6 (1.4%) | 0 |
| keyword rule | held-out | 161 (**38.3%**) | 0 |
| `qwen2.5:7b` | held-out | 15 (3.6%) | 69 |
| `qwen2.5:14b` | held-out | 14 (3.3%) | 47 |
| `openai/gpt-oss-20b` | held-out | 1 (0.2%) | 14 |

On unfamiliar phrasing the rule asserts a charge the text explicitly denies in 38.3% of judgements,
against 0.2% to 3.6% for the models. In a system that files recovery claims against an acquirer that
is a false claim about money; the models miss the mention instead, which escalates the case.

Reproduce: `python scripts/generate_reading_evidence.py`. Raw:
[`advice-reading-2026-08-29.json`](evidence/advice-reading-2026-08-29.json),
[`advice-reading-gpt-oss-20b-2026-08-30.json`](evidence/advice-reading-gpt-oss-20b-2026-08-30.json).

## Three-source matching

A settlement report, a bank statement and an ERP ledger joined on nothing reliable. The hard case is
two payouts to the same merchant, same amount, same day: every structured field stops discriminating
at once, and only the free-text cycle reference remains.

| Cycle reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| none | 91.3% [85.7, 94.9] | 88.0% [81.8, 92.3] | -3.3 |
| none, weights estimated not chosen | 90.0% [84.2, 93.8] | 91.3% [85.7, 94.9] | +1.3 |
| best regex parser | 98.7% [95.3, 99.6] | 88.0% [81.8, 92.3] | -10.7 |
| `qwen2.5:7b-instruct` | 98.0% [94.3, 99.3] | 94.0% [89.0, 96.8] | -4.0 |

150 settlements against 180 bank rows, the true row reachable in 150/150 for every column.

The regex wins on seen phrasing. On held-out it scores 88.0%, identical to not parsing the cycle at
all, because its patterns match zero descriptions; the model wins 13 paired cases and loses 4, exact
McNemar **p = 0.049**, moving to p = 0.33 if two cases are conceded. Row two replaces my hand-chosen
match weights with log-odds estimated from data, lifting the structured-only baseline to 91.3%, so
the model's real margin is 2.7 points and not 6
([METHODS.md](METHODS.md#match-weights-estimated-instead-of-chosen)).

Reproduce: `python scripts/generate_three_source_evidence.py --n 120`. The three original columns
reproduce their published values exactly at that n, which is what makes the fourth comparable. Raw:
[`three-source-2026-08-30.json`](evidence/three-source-2026-08-30.json).

## End to end on the residual

Layer 0 enumerates every arithmetically valid decomposition, and all columns choose from the identical
shuffled option list.

| Strategy | Seen phrasing | Held-out phrasing |
|---|---|---|
| chance, computed as 1/k | 6.3% | 6.1% |
| best keyword rule | 42.4% [30.5, 55.2] | 8.3% [3.6, 18.1] |
| model, whole option list (7b) | 5.1% [1.7, 14.0] | 5.0% [1.7, 13.7] |
| model reader (7b) | 25.4% [15.9, 38.1] | 20.0% [11.8, 31.8] |
| model reader (14b) | 35.6% [24.6, 48.3] | 26.7% [17.1, 39.0] |
| parsimony, ignores the advice | 25.4% [15.9, 38.1] | 31.7% [21.3, 44.2] |

59 to 60 under-determined cases per condition, the true answer inside the 40-option window in every
one. The keyword rule collapses to 8.3% against a 6.1% floor.

Handing the model the whole option list scores at chance: Layer 0 has already done the arithmetic,
and re-deriving a subset-sum over 30 candidates is what it is worst at. Splitting the job so it only
reads takes 7b from 5.1% to 25.4% on identical data. Parsimony scores 31.7% against the 14b reader's
26.7%, paired p = 0.55, so reading did not help where it competes with a structural prior.

Tolerance-based matching could plausibly have manufactured this ambiguity. It did not: with zero
rounding noise and zero tolerance, exact integer arithmetic, 51 of 60 compound cases are still
under-determined ([METHODS.md](METHODS.md#where-the-ambiguity-comes-from)).

Reproduce: `python scripts/generate_residual_evidence.py`. Raw:
[`residual-architecture-2026-08-29.json`](evidence/residual-architecture-2026-08-29.json).

## Fee leakage and GST on fees

A transaction can reconcile perfectly and still have been charged wrongly, because reconciliation
compares the settlement against the records and never the fee against the contract. Neither Razorpay
Recon nor Settlement Insights performs that check.

| Pattern in `app/feeleak/detector.py` | What it catches | Found in a 20-transaction sample |
|---|---|---|
| `blended_rate_overcharge` | a flat card-grade rate applied to UPI or netbanking | 7 |
| `gst_wrong_base` | GST computed on the gross captured amount instead of the fee | 7 |
| `gst_wrong_rate` | a real GST slab applied instead of 18% | 6 |

₹1,497.40 recoverable in fees and ₹15,181.65 in miscalculated tax on that sample. The amounts are
synthetic; the rates are real. False positive rate is **0 across 51,000** ordinary transactions in
0.06s, which is what makes the check safe to run unattended.

GST on the gateway fee is Input Tax Credit, routinely buried in one "gateway charges" ledger line.
`app/erp/journal.py` splits it onto its own ITC-eligible line, ₹2,139.72 per 120-transaction batch:
money already lost on transactions that reconciled correctly.

## Throughput, and why the model is only allowed on the residual

Scope is stated per row, because a previously published 5,508 tx/sec figure and a 20,953 tx/sec one
measured different things. 50,000 transactions, median of 3 repeats.

| Density | Closed without a model | Reaching a model | chains + matching | full `run_batch` region |
|---|---|---|---|---|
| demo default, 60% clean | 85.0% | 15.0% | 17,424 tx/sec | 7,011 tx/sec |
| realistic, 97% clean | 98.9% | 1.1% | 20,513 tx/sec | 11,136 tx/sec |

Two corrections. The 5,508 figure was the wider scope at demo density, never comparable to a chains-
and-matching number, and 20,953 and 27,531 in an earlier version came from single unrepeated runs.
Measured on Windows 11, AMD Zen 3, 16 logical CPUs, Python 3.12.10.

A real model runs at 2.58 tx/sec, about 8,000 times slower than deterministic matching, and that gap
is the argument for the architecture. At 100,000 transactions a day, 98,880 resolve
deterministically in about 5 seconds while the 1,120 reaching a model take 7 minutes; everything
through the model would take 10.8 hours.

Reproduce: `python scripts/benchmark_throughput.py`. Raw:
[`throughput-2026-08-29.json`](evidence/throughput-2026-08-29.json).

## Forecasting

`predict_settlement` computes net as `captured - fee - tax`, exact for an ordinary transaction and
wrong otherwise, so `app/forecast/forecastability.py` declines the rest. Every reason is decidable
from Order, Payment and Refund alone, never from a Settlement that does not exist yet.

| Scored population | n | exact | median err | mean err | p95 |
|---|---|---|---|---|---|
| what it forecasts | 1,795 | **80.8%** | 0.00% | 3.18% | 6.27% |
| what it refuses | 205 | 0.0% | 77.85% | 107.22% | 315.02% |
| everything | 2,000 | 72.5% | 0.00% | 13.87% | 84.14% |

Refusing 10.2% of the batch separates those rows, and does not improve date coverage because every
reason firing is amount-related. Five numbers rather than one because the mean sits far from the
middle: 83% of it comes from five rows out of 1,795.

| Interval | Coverage | Mean width | Claims |
|---|---|---|---|
| SLA window | 87.2% | 1.14 d | nothing |
| calibrated | **93.8%** | 2.40 d | 90% |
| calibrated | 96.6% | 3.57 d | 95% |

The SLA window states no confidence level, so no coverage figure can falsify it. The calibrated
interval states one, is verified out of sample from 50% to 99%, and over-covers by up to 7.5 points,
which is a property of a near-discrete lag distribution rather than a tuning error
([METHODS.md](METHODS.md#why-the-forecast-interval-over-covers)).

125 date misses are `timing_lag` and 100 amount misses are `genuine_error`, neither predictable from
an Order and a Payment. A business-day window was the obvious next build, but 29.8% of settlements
land on a weekend against 28.6% for uniform.

Throughput: **455,955 predictions/sec**. Reproduce:
`python scripts/generate_forecast_evidence.py`. Raw:
[`forecast-2026-08-30.json`](evidence/forecast-2026-08-30.json).

## Settlement Q&A

Nine questions per batch, each with an answer computed from the batch itself, scored on the number,
the ids cited, and any cited id that does not exist.

| Provider | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| keyword rule | 87.5% | **0.0%** | -87.5 pts |
| `qwen2.5:7b-instruct` | 65.0% | **62.5%** | -2.5 pts |

5 seeds, 40 numeric answers per cell, and a test asserts the held-out column contains none of the
rule's vocabulary. Fabricated transaction ids: 0 of 45 in all four conditions. Per question on held-
out the model takes `busiest_date` 5/5 where it scored 0/5 before, which is `settlements_by_date`
arriving; it still scores 0/5 on three questions whose tool could answer them, so that miss is
comprehension.

Fabricated transaction ids: 0 of 45 in all four conditions.

Reproduce: `python scripts/generate_qa_evidence.py`. Raw:
[`qa-2026-08-30.json`](evidence/qa-2026-08-30.json).

## Core reconciliation

| Claim | Number |
|---|---|
| Match rate, real provider, demo density | 99.3% of settlement value, 7 escalations of 120 |
| Match rate, mock provider, demo density | 86.0%, 18 escalations of 120 |
| `netting_trap` | 59 distinct real cases, 98.3% [91.0, 99.7] |
| `duplicate_refund` | 37 distinct real cases, 100% [90.6, 100.0] |
| `genuine_error` | 66 distinct real cases, 80.3% [69.2, 88.1], never auto-resolves by design |
| Auto-resolved with no human review, under the superseded fixed-n gate | 59 distinct cases, ₹4,86,473.13 of synthetic value. None would qualify under the gate that replaced it |
| Adversarial stress batch | 40/40 handled, 0 wrongly auto-resolved |

These rows are at the generator's demo density of 60% clean, denser than reality so every category
is exercised at n=120. Every rupee figure is generated; the mechanism is the claim. A 20-line rule
with zero LLM calls scores 519/519 on the three categories, which is why the model's value there is
reliability under failure.

**Nothing currently clears the gate.** Both were reported as having cleared 90% after 8 batches,
measured with a Wilson bound recomputed after every batch, which is optional stopping. Under a bound
valid at every stopping time they are 88.4% and 85.6%. What the system can safely automate is 59.3%
of decisions at a 1.0% error rate, at a gate of 0.85
([METHODS.md](METHODS.md#the-autonomy-gate-and-why-wilson-was-the-wrong-bound)).

Reproduce: `python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db`.

## Everything else

| Result | Number | Reproduce |
|---|---|---|
| Semantic entropy as an escalation signal | AUROC 0.633, permutation p = 0.0505, so not distinguishable from chance at n=59 ([METHODS.md](METHODS.md#semantic-entropy-a-fourth-escalation-signal-and-a-null)) | `python scripts/generate_semantic_entropy_evidence.py` |
| Time-to-revocation drill | 1 wrong decision revoked a category, aggregate still 97.6%. Seeds its own synthetic category, because nothing in the committed history holds autonomy to revoke | `POST /api/drift/drill` |
| Realized regret | ₹0 across 8 real auto-resolved transactions | `GET /api/regret` |
| GSTR-2B match | 120 matched, 30 exceptions across 3 disjoint kinds | `GET /api/gstr2b` |
| Blind backtest, seeds 1-20 | median amount error 0.17%, coverage 56.5% (range 3%-100%) | `GET /api/forecast/blind-backtest` |
| Load test | 100% success to 32 concurrent, 2.157s to 4.750s mean | `python scripts/load_test.py` |
| Cross-run tool memory | 834 prior `netting_trap` resolutions recalled on a fresh run | `GET /api/audit?run_id=<id>` |

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                 # 543 tests
python scripts/generate_reading_evidence.py
python scripts/generate_three_source_evidence.py --n 120
python scripts/generate_forecast_evidence.py
python scripts/generate_qa_evidence.py
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
```
