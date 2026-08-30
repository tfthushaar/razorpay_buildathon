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
| `openai/gpt-oss-20b` | 92.1% [89.2, 94.4] | 96.2% [93.9, 97.6] | +4.0 |

60 cases x 7 charge types = 420 judgements per cell, 95% Wilson intervals. On held-out phrasing the
rule's interval does not overlap any model's.

`openai/gpt-oss-20b` is a second model family, run separately once the daily quota allowed it. Its
`keyword_rule` column reproduces the committed one exactly, 400/420 and 259/420 with the same 6 and
161 dangerous errors, so the two runs scored the identical case set. 7 of its 420 seen judgements came
back unparseable and are counted as wrong; its held-out column had none.

It is the only reader that does not lose ground on unfamiliar phrasing. I would not read much into a
4-point gain at 60 cases; what matters is that a second family clears the rule on held-out phrasing by
34.5 points, so the finding is about models rather than about qwen.

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
| `openai/gpt-oss-20b` | held-out | 1 (0.2%) | 14 |

On unfamiliar phrasing the rule asserts a charge the text explicitly denies in 38.3% of judgements,
against 3.6% for 7b, 3.3% for 14b and 0.2% for gpt-oss-20b. In a system that files recovery claims against an acquirer, that
is a false claim about money. The models miss the mention instead, which escalates the case.

Reproduce: `python scripts/generate_reading_evidence.py`, and for the second family
`python scripts/generate_reading_evidence.py --models "" --groq-model openai/gpt-oss-20b`. Raw:
[`advice-reading-2026-08-29.json`](evidence/advice-reading-2026-08-29.json),
[`advice-reading-gpt-oss-20b-2026-08-30.json`](evidence/advice-reading-gpt-oss-20b-2026-08-30.json).

## Three-source matching

A settlement report, a bank statement and an ERP ledger joined on nothing reliable
([ARCHITECTURE.md](ARCHITECTURE.md)). The hard case is two payouts to the same merchant, same amount,
same day: every structured field stops discriminating at once, the truncated UTRs share a tail, and
only the free-text cycle reference remains. Everything except the cycle reader is identical across
columns.

| Cycle reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| none | 91.3% [85.7, 94.9] | 88.0% [81.8, 92.3] | -3.3 |
| none, weights estimated not chosen | 90.0% [84.2, 93.8] | 91.3% [85.7, 94.9] | +1.3 |
| best regex parser | 98.7% [95.3, 99.6] | 88.0% [81.8, 92.3] | -10.7 |
| `qwen2.5:7b-instruct` | 98.0% [94.3, 99.3] | 94.0% [89.0, 96.8] | -4.0 |

150 settlements against 180 bank rows. The true row was reachable in 150/150 for every column, so
nothing is capped by filtering.

The regex wins seen phrasing by one match. On held-out it scores 88.0%, identical to not parsing the
cycle at all, because its patterns match zero descriptions. The model wins 13 paired cases and loses
4, exact McNemar **p = 0.049**. Conceding 2 cases to the regex takes that to p = 0.33, so the result
is significant but not robust to a couple of mis-scored cases.

The shipped matcher uses weights I chose, and scoring by hand-chosen constants is the standing
criticism of fuzzy matching. The second row answers it: the same comparators, the same candidate
filter, no cycle term, weighted instead by log-odds estimated from a calibration batch on a different
seed from the one it is scored on.

| Field | Hand-chosen | Estimated | m | u |
|---|---|---|---|---|
| exact UTR | 2.0 | **9.02** | 0.519 | 0.001 |
| exact amount | 1.0 | 0.52 | 0.999 | 0.695 |
| exact date | 0.5 | 0.46 | 0.674 | 0.492 |
| merchant name | up to 1.0 | **0.01** | 0.770 | 0.763 |

Merchant-name similarity agrees on 77% of true matches and 76% of false ones, so it separates almost
nothing, and the hand-tuned scorer was adding up to a full point of it to every candidate. An exact
UTR is worth nine, not two.

The effect is real and one-directional rather than a clean win. On held-out phrasing the estimated
weights take the structured-only baseline from 88.0% to 91.3%, beating the regex parser on 5 cases
and losing none, though at p = 0.0625 that is not significant at this n. On seen phrasing they are
worse, 90.0% against 91.3%: the hand-chosen weights were tuned against phrasing I had seen, which is
the same effect the reading experiment measures one layer up.

What it buys is the argument, not the number. The model's 94.0% on held-out phrasing is now measured
against a best structured baseline of 91.3% rather than 88.0%, so its margin is 2.7 points and not 6.
"Even with weights estimated from the data, the structured fields tie" is a claim about the problem;
"with my weights, they tie" was a claim about me.

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

Four of the five reasons had never fired outside a hand-built object, leaving the effect above resting
on `refund_in_flight`. On a pending batch carrying the missing shapes, `sla_already_breached` fires 129
times, `partial_capture` 80, `not_captured` 40 and `non_positive_net` 40. `non_positive_net` turns out
to be unreachable for any positive capture under a pure-percentage fee schedule, so it fires only on a
fully reversed authorisation, alongside `partial_capture`.

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

Fitting on one batch and applying to another is split conformal prediction, which over-covers by at
most 1/(n+1). Here that bound is +0.005 points against an observed +7.5, so the finite-sample term
explains almost none of the gap. Ties do: lag takes 35 to 53 distinct values per rail across hundreds
of observations, with one value carrying up to 9.8% of the mass, so a quantile steps across a whole
block at once. Randomising to break ties would close most of it and would make the same payment yield
a different window per call, which is the worse trade for a finance tool.

### What a better forecaster could not fix

Attributed after the fact against the answer key the forecaster never sees: 125 date misses are
`timing_lag` and 100 amount misses are `genuine_error`, neither predictable from an Order and a
Payment. A business-day window was the obvious next build, but 29.8% of settlements land on a weekend
against 28.6% for uniform, so there is no weekday structure here to model.

Throughput: **455,955 predictions/sec**. Reproduce:
`python scripts/generate_forecast_evidence.py`, or `GET /api/forecast/reliability`. Raw:
[`forecast-2026-08-30.json`](evidence/forecast-2026-08-30.json).

## Settlement Q&A

Nine questions per batch, each with an answer computed from the batch itself, scored on the number,
the ids cited, and any cited id that does not exist.

| Provider | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| keyword rule | 87.5% | **0.0%** | -87.5 pts |
| `qwen2.5:7b-instruct` | 65.0% | **62.5%** | -2.5 pts |

5 seeds, 40 numeric answers per cell. The held-out column asks the same questions in words the rule's
cue list was never built for, and a test asserts it contains none of that vocabulary.

An earlier version of this table ran six questions and put the model at 63.3% and 50.0%. The two are
not a before-and-after: the question set changed at the same time as the toolset. `settlements_by_date`
closed the one question no provider could answer, and three questions were added. What the new run
measures is nine questions against the current tools.

Per question, held-out, the model now takes `busiest_date` 5/5 where it previously scored 0/5, which
is the tool arriving. It still scores 0/5 on `needs_review_count`, `sla_breach_count` and
`discrepancy_count`. Those three have a tool that can answer them, so the miss is comprehension rather
than capability, and it is the honest ceiling on this column.

The rule is the better instrument on questions phrased the way I wrote it, and answers none at all
once the wording changes: "what is the size of this run" fires no cue, so no tool runs. Citation
overlap moves the same way, 0.67 to 0.01 against the model's 0.93 to 0.56.

Fabricated transaction ids: 0 of 45 in all four conditions. An invented id is a reference an operations
person goes and looks for, so it is scored separately from being wrong.

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
| Auto-resolved with no human review, under the superseded fixed-n gate | 59 distinct cases, ₹4,86,473.13 of synthetic value. Under the anytime-valid gate that replaced it, none of these would have been auto-resolved |
| Adversarial stress batch | 40/40 handled, 0 wrongly auto-resolved |

These rows are at the generator's demo density of 60% clean, denser than reality so every category
is exercised at n=120. The 98.9% figure above is the same pipeline at 97%-clean density. Different
denominators, both measured. Every rupee figure is generated; the mechanism is the claim.

Neither category currently clears the gate. `netting_trap` and `duplicate_refund` were reported as
having cleared 90% after 8 accumulated batches, measured with a Wilson bound recomputed after every
batch. Wilson holds at a fixed n, and re-checking then stopping at the first crossing is optional
stopping. Under a bound valid at every stopping time they are 88.4% and 85.6%, and both escalate.
`genuine_error` stays escalated at any bound, because no accuracy figure makes auto-resolving an
unexplained case correct.

A 20-line rule with zero LLM calls scores 519/519 on those three categories, which is why the model's
value there is reliability under failure.

### What the threshold buys

Escalating what a machine is unsure of is selective prediction, so the honest report is coverage
against risk rather than one pass/fail.

| Gate | Coverage | Selective risk | Automated |
|---|---|---|---|
| up to 0.85 | 59.3% | 1.0% | `duplicate_refund`, `netting_trap` |
| 0.86 to 0.88 | 36.4% | **1.7%** | `netting_trap` |
| 0.89 and above | 0.0% | n/a | none |

The middle row is what a single threshold hides: raising the gate raised risk. `duplicate_refund` is
perfect at 37 of 37 and still scores a lower bound than `netting_trap` at 58 of 59, because the bound
rewards evidence and not only accuracy, so a stricter bar drops the flawless category and keeps the
flawed one.

A 95% lower bound promises P(bound > true accuracy) at most 5%. Checked every 5 decisions to n=300
it delivered 9.72%, 10.12% and 8.77% at true accuracies of 88%, 90% and 92%: a uniform violation,
not one bad row. The gate opening at 90% is a separate number, 3.12% at a true 88% against 0.12%
for a single check. Forty perfect decisions are worth
91.2% under Wilson and 86.6% under a valid bound; 55 is the first n that qualifies.

Reproduce: `GET /api/risk-coverage`.

Reproduce: `python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db`.

## Everything else

| Result | Number | Reproduce |
|---|---|---|
| Time-to-revocation drill | 1 wrong decision revoked a category, aggregate still 97.6%. Seeds its own synthetic category to 60 clean decisions first, because nothing in the committed history holds autonomy to revoke | `POST /api/drift/drill` |
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
