# Results

Every number with the command that reproduces it. Strongest first. Superseded experiments:
[RESULTS_SUPERSEDED.md](RESULTS_SUPERSEDED.md).

Comparisons run over identical cases, so the tests are paired. Independent intervals ignore the
pairing and are conservative: the three-source intervals below overlap while the paired test on the
same cases is significant. See `app/calibration/significance.py`.

## Reading remittance advice: model vs. regex

The reader is scored on its own, upstream of everything that consumes it. Only the reading stage is
compared, since scoring a decomposition against assertions is bookkeeping a rule does perfectly. The
rule is written to win: fragment splitting, cause keywords, and a 29-entry negation-cue list
assembled with full sight of the generator's phrasing.

| Reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| best keyword rule | 95.2% [92.8, 96.9] | 61.7% [56.9, 66.2] | -33.6 |
| `qwen2.5:7b-instruct` | 79.8% [75.7, 83.3] | 72.6% [68.2, 76.7] | -7.1 |
| `qwen2.5:14b-instruct` | 86.9% [83.3, 89.8] | 81.7% [77.7, 85.1] | -5.2 |

60 cases x 7 charge types = 420 judgements per cell, 95% Wilson intervals. On held-out phrasing the
rule's interval does not overlap either model's.

Held-out phrasing keeps TDS, RSV, GST and MDR recognisable, changing only how applied-versus-not is
said: abeyance, rescinded, held over, zero-rated, struck off, stood down, lapsed, contra. A test
asserts the held-out bank contains none of the rule's cues.

The two failure modes carry different consequences.

| Reader | Condition | Reads a denial as a confirmation | Misses a mention |
|---|---|---|---|
| keyword rule | seen | 6 (1.4%) | 0 |
| keyword rule | held-out | 161 (**38.3%**) | 0 |
| `qwen2.5:7b` | held-out | 15 (3.6%) | 69 |
| `qwen2.5:14b` | held-out | 14 (3.3%) | 47 |

On unfamiliar phrasing the rule asserts a charge the text explicitly denies in 38.3% of judgements,
eleven times either model's rate. In a system that files recovery claims against an acquirer, that
is a false claim about money. The models miss the mention instead, which escalates the case.

Reproduce: `python scripts/generate_reading_evidence.py`. Raw:
[`advice-reading-2026-08-29.json`](evidence/advice-reading-2026-08-29.json).

## Three-source matching

A settlement report, a bank statement and an ERP ledger joined on nothing reliable
([ARCHITECTURE.md](ARCHITECTURE.md)). The hard case is two payouts to the same merchant, same amount,
same day: every structured field stops discriminating at once, the truncated UTRs share a tail, and
only the free-text cycle reference remains. Everything except the cycle reader is identical across
columns.

| Cycle reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| none | 91.3% [85.7, 94.9] | 88.0% [81.8, 92.3] | -3.3 |
| best regex parser | 98.7% [95.3, 99.6] | 88.0% [81.8, 92.3] | -10.7 |
| `qwen2.5:7b-instruct` | 98.0% [94.3, 99.3] | 94.0% [89.0, 96.8] | -4.0 |

150 settlements against 180 bank rows. The true row was reachable in 150/150 for every column, so
nothing is capped by filtering.

The regex wins seen phrasing by one match. On held-out it scores 88.0%, identical to not parsing the
cycle at all, because its patterns match zero descriptions. The model wins 13 paired cases and loses
4, exact McNemar **p = 0.049**. Conceding 2 cases to the regex takes that to p = 0.33, so the result
is significant but not robust to a couple of mis-scored cases.

Reproduce: `python scripts/generate_three_source_evidence.py`. Raw:
[`three-source-2026-08-29.json`](evidence/three-source-2026-08-29.json).

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

59 to 60 under-determined cases per condition, with the true answer inside the 40-option window in
every one. The keyword rule collapses to 8.3% against a 6.1% floor.

Handing the model the whole option list scores at chance: Layer 0 has already done the arithmetic,
and re-deriving a subset-sum over 30 candidates is what the model is worst at. Splitting the job so
it only reads takes 7b from 5.1% to 25.4% on identical data.

Parsimony scores 31.7% against the 14b reader's 26.7%. An earlier version of this file said parsimony
beat every reader; the paired test gives 7 discordant one way and 4 the other, p = 0.55. Parsimony is
at least as good, and the difference is not distinguishable at n=60. Reading did not help where it
competes with a structural prior.

Reproduce: `python scripts/generate_residual_evidence.py`. Raw:
[`residual-architecture-2026-08-29.json`](evidence/residual-architecture-2026-08-29.json),
[`residual-architecture-14b-2026-08-29.json`](evidence/residual-architecture-14b-2026-08-29.json).

## Where the ambiguity comes from

Tolerance-based matching could plausibly have manufactured the under-determination, so the row worst
for the architecture is zero rounding noise and zero tolerance, exact integer arithmetic.

| Noise | Tolerance | Resolved | Under-determined | Unmatched | Median k | True answer recovered |
|---|---|---|---|---|---|---|
| 0 | 0 | 9 | 51 | 0 | 4 | 60/60 |
| 0 | 10 | 0 | 60 | 0 | 28 | 60/60 |
| 3 | 0 | 5 | 48 | 7 | 3 | 10/60 |
| 3 | 10 | 1 | 59 | 0 | 22 | 60/60 |

At exact match with no tolerance, 51 of 60 compound cases are still under-determined.
Compositionality does that; tolerance amplifies it
(`test_compositionality_alone_makes_it_under_determined`).

The recovery column makes the rest meaningful. Without it, "the model chose wrong" and "the right
answer was never on the table" are indistinguishable. It found a real bug: percentage candidates
computed off the post-fee hop, giving 11/60.

## Fee leakage and GST on fees

A transaction can reconcile perfectly and still have been charged wrongly, because reconciliation
compares the settlement against the records and never the fee against the merchant's contract.
Neither Razorpay Recon nor Settlement Insights performs that check. Three patterns ship in
`app/feeleak/detector.py`.

| Pattern in `app/feeleak/detector.py` | What it catches | Found in a 20-transaction sample |
|---|---|---|
| `blended_rate_overcharge` | a flat card-grade rate applied to UPI or netbanking | 7 |
| `gst_wrong_base` | GST computed on the gross captured amount instead of the fee | 7 |
| `gst_wrong_rate` | a real GST slab applied instead of 18% | 6 |

On that sample: ₹1,497.40 recoverable in fees and ₹15,181.65 in miscalculated tax, against 0.58% of
sample value. The amounts are synthetic; the rates are real. `FEE_PCT` and `GST_RATE` are the contract the
detector checks against, and the same comparison runs unchanged against a merchant's own.

False positive rate: **0 across 51,000** ordinary transactions spanning every category the generator
produces, in 0.06s, which is what makes the check safe to run unattended. It is a pure
arithmetic pass with no per-transaction state, so 200 times more data costs nothing
(`test_zero_false_positives_against_every_existing_category`).

GST on the gateway fee is Input Tax Credit, routinely buried inside a single "gateway charges" ledger
line where no accountant will find it. `app/erp/journal.py` splits it onto its own ITC-eligible line:
₹2,139.72 per 120-transaction batch. That is money already lost on transactions that reconciled
correctly.

## Throughput, and why the model is only allowed on the residual

Scope is stated per row, because a previously published 5,508 tx/sec figure and a 20,953 tx/sec one
measured different things and the difference was scope. 50,000 transactions, median of 3 repeats.

| Density | Closed without a model | Reaching a model | chains + matching | full `run_batch` region |
|---|---|---|---|---|
| demo default, 60% clean | 85.0% | 15.0% | 17,424 tx/sec | 7,011 tx/sec |
| realistic, 97% clean | 98.9% | 1.1% | 20,513 tx/sec | 11,136 tx/sec |

`chains + matching` is `build_all_chains` plus Pass 1/2. The `run_batch` region adds batch
generation, tool-context construction and mock narration, and is the scope behind the 5,508 figure.
Component timings are in the evidence file and they sum.

Two corrections. The 5,508 figure was the wider scope at demo density, never comparable to a chains-
and-matching number. Figures of 20,953 and 27,531 in an earlier version came from single unrepeated
runs; the medians above are 17% and 25% lower. Measured on Windows 11, AMD Zen 3, 16 logical CPUs,
Python 3.12.10.

Measured on Windows 11, AMD Zen 3 (Family 25), 16 logical CPUs, Python 3.12.10, since throughput is a
hardware claim as much as a code claim.

A real model runs at 2.58 tx/sec, about 8,000 times slower than deterministic matching, and that gap
is the argument for the architecture. At 100,000 transactions a day and realistic density, 98,880
resolve deterministically in about 5 seconds while the 1,120 reaching a model take 7 minutes.
Running everything through the model would take 10.8 hours.

Reproduce: `python scripts/benchmark_throughput.py`. Raw:
[`throughput-2026-08-29.json`](evidence/throughput-2026-08-29.json).

## Forecasting

The track's bar is throughput, measured accuracy and an honest exception list. The forecaster met
one and a half of those, and two of the gaps turned out to be in the measurement.

### What it refuses to predict

`predict_settlement` computes net as `captured - fee - tax`, exact for an ordinary transaction and
wrong otherwise, so `app/forecast/forecastability.py` declines the rest. Every reason is decidable
from Order, Payment and Refund alone; none consults a Settlement, which does not exist yet.

| Scored population | n | exact | median err | mean err | p95 |
|---|---|---|---|---|---|
| what it forecasts | 1,795 | **80.8%** | 0.00% | 3.18% | 6.27% |
| what it refuses | 205 | 0.0% | 77.85% | 107.22% | 315.02% |
| everything | 2,000 | 72.5% | 0.00% | 13.87% | 84.14% |

Refusing 10.2% of the batch separates those rows. It does not improve date coverage, because every
reason currently firing is amount-related and a refund changes what settles, not when
(`test_refusing_actually_improves_amount_accuracy` fails the build if refusing stops helping).

Five numbers because the mean sits far from the middle here: 83% of it comes from five rows out of
1,795, and the median is zero.

### Which interval, and what it costs

The SLA tolerance window is a policy boundary stating no confidence level, so no coverage figure can
falsify it. The calibrated interval states one.

| Interval | Coverage | Mean width | Claims |
|---|---|---|---|
| SLA window | 87.2% | 1.14 d | nothing |
| calibrated | **93.8%** | 2.40 d | 90% |
| calibrated | 96.6% | 3.57 d | 95% |

Five held-out batches. The calibrated interval over-covers by 3.8 points and costs more than double
the width, which a treasury team planning against 2.40 days instead of 1.14 pays for. The SLA window
stays the default because it needs no settlement history.

### Whether the stated confidence is earned

Fitted on one batch and verified on twelve others, since fitting quantiles and scoring the same data
measures memorisation.

| Nominal | 50% | 60% | 70% | 80% | 90% | 95% | 99% |
|---|---|---|---|---|---|---|---|
| Empirical | 57.1% | 65.6% | 77.5% | 83.6% | 93.6% | 96.5% | 99.1% |
| Gap | +7.1 | +5.6 | +7.5 | +3.6 | +3.6 | +1.5 | +0.1 |

n=2,000 per batch, seeds 100-111. Coverage sits at or above nominal everywhere, so the stated
confidence is conservative.

### What a better forecaster could not fix

Attributed after the fact against the answer key the forecaster never sees: 125 date misses are
`timing_lag` and 100 amount misses are `genuine_error`, neither predictable from an Order and a
Payment. A business-day window was the obvious next build, but 29.8% of settlements land on a weekend
against 28.6% for uniform, so there is no weekday structure here to model.

Throughput: **455,955 predictions/sec**. Reproduce:
`python scripts/generate_forecast_evidence.py`, or `GET /api/forecast/reliability`. Raw:
[`forecast-2026-08-30.json`](evidence/forecast-2026-08-30.json).

## Settlement Q&A

Six questions per batch, each with an answer computed from the batch itself, scored on the number,
the ids cited, and any cited id that does not exist.

| Provider | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| keyword rule | 83.3% | **0.0%** | -83.3 pts |
| `qwen2.5:7b-instruct` | 63.3% | **50.0%** | -13.3 pts |

5 seeds, 30 numeric answers per cell. The held-out column asks the same questions in words the
rule's cue list was never built for, and a test asserts it contains none of that vocabulary.

The rule is the better instrument on questions phrased the way I wrote it, and answers none at all
once the wording changes: "what is the size of this run" fires no cue, so no tool runs. Citation
overlap moves the same way, 0.67 to 0.01 against the model's 0.90 to 0.58.

Fabricated transaction ids: 0 of 30 in all four conditions. An invented id is a reference an
operations person goes and looks for, so it is scored separately from being wrong.

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
| Auto-resolved with no human review | 59 distinct cases, ₹4,86,473.13 of synthetic value |
| Adversarial stress batch | 40/40 handled, 0 wrongly auto-resolved |

These rows are at the generator's demo density of 60% clean, denser than reality so every category
is exercised at n=120. The 98.9% figure above is the same pipeline at 97%-clean density. Different
denominators, both measured. Every rupee figure is generated; the mechanism is the claim.

After 8 accumulated Ollama batches, `netting_trap` and `duplicate_refund` cleared the 90% trust
threshold. `genuine_error` sat at 80.3% and stayed escalated, because no accuracy figure makes auto-
resolving an unexplained case correct. A 20-line rule with zero LLM calls scores 519/519 on those
three categories, which is why the model's value there is reliability under failure.

Reproduce: `python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db`.

## Everything else

| Result | Number | Reproduce |
|---|---|---|
| Time-to-revocation drill | 1 wrong decision revoked a category, aggregate still 97.6% | `POST /api/drift/drill` |
| Realized regret | ₹0 across 8 real auto-resolved transactions | `GET /api/regret` |
| GSTR-2B match | 120 matched, 30 exceptions across 3 disjoint kinds | `GET /api/gstr2b` |
| Blind backtest, seeds 1-20 | median amount error 0.17%, coverage 56.5% (range 3%-100%) | `GET /api/forecast/blind-backtest` |
| Load test | 100% success to 32 concurrent, 2.157s to 4.750s mean | `python scripts/load_test.py` |
| Cross-run tool memory | 834 prior `netting_trap` resolutions recalled on a fresh run | `GET /api/audit?run_id=<id>` |

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                 # 404 tests
python scripts/generate_reading_evidence.py
python scripts/generate_three_source_evidence.py
python scripts/generate_forecast_evidence.py
python scripts/generate_qa_evidence.py
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
```
