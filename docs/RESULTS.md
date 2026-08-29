# Results

Every number with the command that reproduces it. Strongest first, not chronological. Experiments
measuring the architecture this project replaced: [RESULTS_SUPERSEDED.md](RESULTS_SUPERSEDED.md).

Comparisons run over identical cases, so paired tests are used rather than a comparison of two
independent intervals. Independent intervals ignore the pairing and are conservative: the three-source
intervals below overlap while the paired test on the same cases is significant.
See `app/calibration/significance.py`.

## Reading remittance advice: model vs. regex

Measured in isolation rather than inferred from end-to-end accuracy. The keyword baseline is two
stages: read the advice into assertions, then score every valid decomposition against them. Stage two
is bookkeeping a rule does perfectly. Only stage one is compared, against ground truth the generator
records itself.

The rule is written to win: fragment splitting, cause keywords, and a 29-entry negation-cue list
assembled with full sight of the generator's phrasing.

| Reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| best keyword rule | 95.2% [92.8, 96.9] | 61.7% [56.9, 66.2] | −33.6 |
| `qwen2.5:7b-instruct` | 79.8% [75.7, 83.3] | 72.6% [68.2, 76.7] | −7.1 |
| `qwen2.5:14b-instruct` | 86.9% [83.3, 89.8] | 81.7% [77.7, 85.1] | −5.2 |

60 cases × 7 charge types = 420 judgements per cell. 95% Wilson intervals. On held-out phrasing the
rule's interval does not overlap either model's.

Held-out phrasing keeps the cause vocabulary recognisable (TDS, RSV, GST, MDR all still appear) and
changes only how applied-versus-not-applied is expressed: abeyance, rescinded, held over, zero-rated,
struck off, stood down, lapsed, contra. A test asserts the held-out bank contains none of the rule's
own cues.

The two failure modes are not interchangeable:

| Reader | Condition | Reads a denial as a confirmation | Misses a mention |
|---|---|---|---|
| keyword rule | seen | 6 (1.4%) | 0 |
| keyword rule | held-out | 161 (**38.3%**) | 0 |
| `qwen2.5:7b` | held-out | 15 (3.6%) | 69 |
| `qwen2.5:14b` | held-out | 14 (3.3%) | 47 |

On unfamiliar phrasing the rule asserts a charge the text explicitly denies in 38.3% of judgements,
eleven times either model's rate. In a system that files recovery claims against an acquirer, that is
a false claim about money. The models miss the mention instead, which leaves the component unexplained
and escalates the case.

Reproduce: `python scripts/generate_reading_evidence.py`. Raw:
[`advice-reading-2026-08-29.json`](evidence/advice-reading-2026-08-29.json).

## Three-source matching

A settlement report, a bank statement and an ERP ledger that never agreed, joined on nothing reliable.
This checks the residual argument itself: if under-determination only appeared in compound arithmetic,
it would be fair to suspect the arithmetic was built to produce it.

The hard case is two payouts to the same merchant, same amount, same day. Merchant, amount and date
stop discriminating at once, the truncated UTRs share a tail, and only the free-text settlement cycle
remains. Everything in the matcher is held identical across these columns. Only the cycle reader
changes.

| Cycle reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| none | 91.3% [85.7, 94.9] | 88.0% [81.8, 92.3] | −3.3 |
| best regex parser | 98.7% [95.3, 99.6] | 88.0% [81.8, 92.3] | −10.7 |
| `qwen2.5:7b-instruct` | 98.0% [94.3, 99.3] | 94.0% [89.0, 96.8] | −4.0 |

150 settlements against 180 bank rows. The true row was reachable in 150/150 for every column, so
nothing is capped by filtering.

On seen phrasing the regex wins by one match. On held-out phrasing it scores 88.0%, identical to not
parsing the cycle at all, because the regexes match zero descriptions. The model wins 13 paired cases
and loses 4, exact McNemar **p = 0.049**. Conceding 2 cases to the regex takes that to p = 0.33, so the
result is significant but not robust to a couple of mis-scored cases.

Reproduce: `python scripts/generate_three_source_evidence.py`. Raw:
[`three-source-2026-08-29.json`](evidence/three-source-2026-08-29.json).

## End to end on the residual

Layer 0 enumerates every arithmetically valid decomposition. All columns choose from the identical
shuffled option list.

| Strategy | Seen phrasing | Held-out phrasing |
|---|---|---|
| chance, computed as 1/k | 6.3% | 6.1% |
| best keyword rule | 42.4% [30.5, 55.2] | 8.3% [3.6, 18.1] |
| model, whole option list (7b) | 5.1% [1.7, 14.0] | 5.0% [1.7, 13.7] |
| model reader (7b) | 25.4% [15.9, 38.1] | 20.0% [11.8, 31.8] |
| model reader (14b) | 35.6% [24.6, 48.3] | 26.7% [17.1, 39.0] |
| parsimony, ignores the advice | 25.4% [15.9, 38.1] | 31.7% [21.3, 44.2] |

59 to 60 under-determined cases per condition. The true answer was inside the 40-option window in
every one.

The keyword rule collapses to near-chance on held-out phrasing: 8.3% against a 6.1% floor.

Handing the model the whole option list scores at chance. Layer 0 has already done the arithmetic, so
asking the model to re-derive a subset-sum over 30 candidates is the one thing it is worst at.
Splitting the job so the model only reads takes 7b from 5.1% to 25.4% on identical data.

Parsimony scores 31.7% against the 14b reader's 26.7%. An earlier version of this file said parsimony
beat every reader. The paired test gives 7 discordant one way and 4 the other, p = 0.55. Parsimony is
at least as good, and the difference is not distinguishable at n=60. Reading did not help where it
competes with a structural prior.

Reproduce: `python scripts/generate_residual_evidence.py`. Raw:
[`residual-architecture-2026-08-29.json`](evidence/residual-architecture-2026-08-29.json),
[`residual-architecture-14b-2026-08-29.json`](evidence/residual-architecture-14b-2026-08-29.json).

## Why the ambiguity is not a tolerance knob

The objection is that tolerance-based matching manufactured the under-determination. The row worst for
the architecture is zero rounding noise and zero tolerance, exact integer arithmetic.

| Noise | Tolerance | Resolved | Under-determined | Unmatched | Median k | True answer recovered |
|---|---|---|---|---|---|---|
| 0 | 0 | 9 | 51 | 0 | 4 | 60/60 |
| 0 | 10 | 0 | 60 | 0 | 28 | 60/60 |
| 3 | 0 | 5 | 48 | 7 | 3 | 10/60 |
| 3 | 10 | 1 | 59 | 0 | 22 | 60/60 |

At exact match with no tolerance, 51 of 60 compound cases are still under-determined.
Compositionality does that; tolerance amplifies it. Standing test:
`test_compositionality_alone_makes_it_under_determined`.

The recovery column is what makes the rest meaningful. Without it, "the model chose wrong" and "the
right answer was never on the table" are indistinguishable. It found a real bug: percentage candidates
computed off the post-fee hop, giving 11/60.

## Cascade routing

Free rule → 7b → 14b → human, each tier handling only what the tier below could not.

| Tier | Absorbed | Correct | Accuracy | Sec/resolved |
|---|---|---|---|---|
| keyword rule | 6 | 0 | 0.0% | ~0 |
| `qwen2.5:7b-instruct` | 54 | 12 | 22.2% | 2.64 |
| `qwen2.5:14b-instruct` | 0 | n/a | n/a | n/a |
| human | 0 | n/a | n/a | n/a |

20.0% end to end at 2.38s per case, worse than free parsimony and equal to running 7b on everything.
Two design errors, both mine. The model tiers escalate on verification failure, which in choice mode
can never happen, so the 14b tier never fired. Tier 0's gate measures whether the advice discriminated,
not whether the reading was correct.

No signal I tried correlates with correctness: self-reported confidence is uninformative, verification
is trivially satisfied, tie count measures the wrong quantity.

Reproduce: `python scripts/generate_cascade_evidence.py`. Raw:
[`cascade-routing-2026-08-29.json`](evidence/cascade-routing-2026-08-29.json).

## Fee leakage and GST on fees

A transaction can reconcile perfectly and still have been charged wrongly. Reconciliation compares the
settlement against the records. It never compares the fee against the merchant's contract, so a fee
charged at the wrong rate reconciles cleanly forever. Neither Razorpay Recon nor Settlement Insights
performs that check.

Three patterns ship (`app/feeleak/detector.py`):

| Pattern | What it catches | Found in a 20-transaction sample |
|---|---|---|
| `blended_rate_overcharge` | a flat card-grade rate applied to UPI or netbanking | 7 |
| `gst_wrong_base` | GST computed on the gross captured amount instead of the fee | 7 |
| `gst_wrong_rate` | a real GST slab applied instead of 18% | 6 |

On that sample: ₹1,497.40 recoverable in fees and ₹15,181.65 in miscalculated tax, against 0.58% of
sample value. The amounts are synthetic. The rates are not: `FEE_PCT` and `GST_RATE` are the contract
the detector checks against, and the same comparison runs against a real merchant's real contracted
rates unchanged.

False positive rate: **0 across 51,000** ordinary transactions spanning every category the generator
produces, in 0.06s of detection time. That property is what makes the check safe to run unattended.
It is a pure arithmetic pass with no per-transaction state, so scanning 200 times more data costs
nothing (`test_zero_false_positives_against_every_existing_category`).

Separately, GST on the gateway fee is Input Tax Credit the merchant can claim, and it is routinely
buried inside a single "gateway charges" ledger line where no accountant will find it.
`app/erp/journal.py` splits it into its own ITC-eligible line on every transaction: ₹2,139.72 across a
120-transaction batch. That is not an exception to investigate. It is money already lost on
transactions that reconciled correctly.

## Throughput, and why the model is only allowed on the residual

Scope is stated per row, because a previously published 5,508 tx/sec figure and a 20,953 tx/sec one
measured different things and the difference was scope, not speed. Both scopes are timed here so any
two figures can be checked against each other. 50,000 transactions, median of 3 repeats.

| Density | Closed without a model | Reaching a model | chains + matching | full `run_batch` region |
|---|---|---|---|---|
| demo default, 60% clean | 85.0% | 15.0% | 17,424 tx/sec | 7,011 tx/sec |
| realistic, 97% clean | 98.9% | 1.1% | 20,513 tx/sec | 11,136 tx/sec |

`chains + matching` is `build_all_chains` plus Pass 1/2. The `run_batch` region additionally covers
batch generation, tool-context construction and mock narration, and is the scope behind the earlier
5,508 figure. Component timings are in the evidence file and they sum.

Two corrections. The earlier 5,508 tx/sec was the wider scope at demo density, not the narrower one,
so it was never comparable to a chains-and-matching number. And figures of 20,953 and 27,531 published
in an earlier version of this file came from single unrepeated runs; the medians above are 17% and 25%
lower.

Measured on Windows 11, AMD Zen 3 (Family 25), 16 logical CPUs, Python 3.12.10. Throughput is a
hardware claim as much as a code claim, so the baseline is recorded rather than left to inference.

A real model runs at 2.58 tx/sec, about 8,000 times slower than deterministic matching. That gap is
the argument for the architecture. For a merchant at 100,000 transactions a day at realistic density,
98,880 resolve deterministically in about 5 seconds and the 1,120 reaching a model take 7 minutes.
Running everything through the model would take 10.8 hours. At the demo's inflated exception rate the
same day costs 97 minutes of model time.

Reproduce: `python scripts/benchmark_throughput.py`. Raw:
[`throughput-2026-08-29.json`](evidence/throughput-2026-08-29.json).

## Forecasting, measured against the same bar

The track's bar is throughput, measured accuracy, and an honest exception list. The reconciler meets
all three. The forecaster met one and a half, and the gap was not obvious: it reported MAPE and
coverage, but it predicted every pending payment with identical confidence, and the coverage figure
quoted here for several passes was not a confidence at all. It was the hit rate of a fixed SLA
window, with no nominal level to check it against.

### What it now refuses to predict

`predict_settlement` computes net as `captured - fee(rail, captured) - tax`. That is exact when the
transaction is ordinary and simply wrong when it is not, so `app/forecast/forecastability.py`
declines those cases instead of issuing a number. Every reason is decidable from Order, Payment and
Refund alone; none consults a Settlement, which does not exist yet for a forward prediction.

| Scored population | n | MAPE | SLA-window coverage |
|---|---|---|---|
| what it forecasts | 1,795 | **4.32%** | 87.1% |
| what it refuses | 205 | 107.22% | 91.2% |
| everything, as before | 2,000 | 14.87% | 87.5% |

Refusing 10.2% of the batch cuts MAPE from 14.87% to 4.32%. It does not improve date coverage, and
that is reported rather than omitted: every refusal reason currently firing is amount-related
(`refund_in_flight`), and a refund changes what settles, not when. A refusal layer that improved
neither would be decoration, and `test_refusing_actually_improves_amount_accuracy` fails the build if
that ever becomes true.

Four of the five refusal reasons never fire on this generator's data. `partial_capture`,
`not_captured`, `non_positive_net` and `sla_already_breached` are implemented and unit-tested but
empirically unexercised, so only one is validated against real batches.

### Whether its stated confidence is earned

Intervals are fitted on one batch and verified on twelve entirely different ones. Fitting quantiles
and scoring the same data measures memorisation, so they never share a batch.

| Nominal | Empirical | Gap | Mean width |
|---|---|---|---|
| 50% | 57.1% | +7.1 | 0.42 d |
| 60% | 65.6% | +5.6 | 0.65 d |
| 70% | 77.5% | +7.5 | 0.72 d |
| 80% | 83.6% | +3.6 | 0.78 d |
| 90% | 93.6% | +3.6 | 2.40 d |
| 95% | 96.5% | +1.5 | 3.57 d |
| 99% | 99.1% | +0.1 | 4.49 d |

n=2,000 per batch, seeds 100–111. Empirical coverage is at or above nominal at every level, so the
stated confidence is conservative rather than overclaimed, and the largest deviation is +7.5 points
at the 70% level. A forecaster whose stated 90% really contained 60% would be the same failure as a
category auto-resolving without having earned it; this is the forecasting analogue of the Wilson
lower bound.

Throughput: **455,955 predictions/sec**, which the forecaster had never had measured.

Reproduce: `python scripts/generate_forecast_evidence.py`, or `GET /api/forecast/reliability`. Raw:
[`forecast-2026-08-30.json`](evidence/forecast-2026-08-30.json).

## Core reconciliation

| Claim | Number |
|---|---|
| Match rate, real provider, demo density | 99.3% of settlement value, 7 escalations of 120 |
| Match rate, mock provider, demo density | 86.0%, 18 escalations of 120 |
| Throughput | see the throughput section above |
| `netting_trap` | 59 distinct real cases, 98.3% [91.0, 99.7] |
| `duplicate_refund` | 37 distinct real cases, 100% [90.6, 100.0] |
| `genuine_error` | 66 distinct real cases, 80.3% [69.2, 88.1], never auto-resolves by design |
| Auto-resolved with no human review | 59 distinct cases, ₹4,86,473.13 of synthetic value |
| Adversarial stress batch | 40/40 handled, 0 wrongly auto-resolved |

Reproduce: `python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db`.

These rows are all at the generator's demo density, 60% clean, which is deliberately denser than
reality so every category is exercised at n=120. The 98.9% figure quoted elsewhere is the same
pipeline at a realistic 97%-clean density. Different denominators, both measured.

Every rupee figure here is generated. The mechanism is the claim; the amounts illustrate it.

After 8 accumulated Ollama batches, `netting_trap` and `duplicate_refund` each cleared the 90% trust
threshold. `genuine_error` sat at 80.3% and stayed escalated, because no accuracy figure makes
auto-resolving an admittedly-unexplained case correct. A 20-line rule with zero LLM calls scores
519/519 on those three categories, which is why the LLM's value there is reliability under failure.

## Everything else

| Result | Number | Reproduce |
|---|---|---|
| Time-to-revocation drill | 1 wrong decision revoked a category, aggregate still 97.6% | `curl -X POST localhost:8000/api/drift/drill -H 'Content-Type: application/json' -d '{}'` |
| Realized regret | ₹0 across 8 real auto-resolved transactions | `GET /api/regret` |
| GSTR-2B match | 120 matched, 30 exceptions across 3 disjoint kinds | `GET /api/gstr2b` |
| Forecaster, n=30 | 9.1% MAPE, 93.3% interval coverage | `GET /api/forecast/backtest` |
| Blind backtest, seeds 1–20 | MAPE 0.11%, coverage 56.5% (range 3%–100%) | `GET /api/forecast/blind-backtest` |
| Load test | 100% success to 32 concurrent, 2.157s to 4.750s mean | `python scripts/load_test.py` |
| Cross-run tool memory | 834 prior `netting_trap` resolutions recalled on a fresh run | `GET /api/audit?run_id=<id>` |

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                 # 350 tests
python scripts/generate_reading_evidence.py
python scripts/generate_three_source_evidence.py
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
```
