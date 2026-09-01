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
| `openai/gpt-oss-20b` | not run | **97.3% [93.3, 98.9]** | n/a |

150 settlements against 180 bank rows, the true row reachable in 150/150 for every column.

The regex wins on seen phrasing. On held-out it scores 88.0%, identical to not parsing the cycle at
all, because its patterns match zero descriptions; the model wins 13 paired cases and loses 4, exact
McNemar **p = 0.049**, moving to p = 0.33 if two cases are conceded. Row two replaces my hand-chosen
match weights with log-odds estimated from data, lifting the structured-only baseline to 91.3%, so
the model's real margin is 2.7 points and not 6
([METHODS.md](METHODS.md#match-weights-estimated-instead-of-chosen)).

A second model family reaches 97.3%, beating the regex on 14 paired cases and losing none: exact
McNemar **p = 0.0001**, still p = 0.013 if two cases are conceded. That is a sharper result than
qwen's 13 wins against 4 losses at p = 0.049, which collapsed to p = 0.33 under the same concession.
It also leaves only 2 settlements under-determined against the regex's 10.

Held-out phrasing only, and that is a budget decision rather than a preference: Groq's free tier
allows 200,000 tokens a day and both conditions need about 250,000, so scoring both could not finish.
The run re-scores the deterministic columns first and refuses to proceed unless they reproduce their
published 132/150, so the model column is known to have scored the identical case set rather than
assumed to have.

The reading result now holds across two families and so does this one. The compound residual and the
Q&A agent are still qwen-only.

Reproduce: `python scripts/generate_three_source_evidence.py --n 120`, and for the second family
`python scripts/generate_three_source_second_family_evidence.py`. Raw:
[`three-source-2026-08-30.json`](evidence/three-source-2026-08-30.json),
[`three-source-second-family-2026-09-01.json`](evidence/three-source-second-family-2026-09-01.json).

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

## Shapes the matcher was never designed for

Every accuracy figure above is scored against categories the matching engine's rules were written
knowing, which measures implementation rather than generalisation: the same author wrote the exam and
the student. Four shapes absent from the taxonomy entirely, each breaking an assumption the engine
relies on. **The pass criterion is not accuracy.** Escalating all of them passes; what fails is one
wrong resolution, or a transaction in neither the resolved set nor the queue.

| Shape | n | Resolved wrongly | Escalated | Flagged by a control |
|---|---|---|---|---|
| controls (`clean_match`) | 12 | 0 | 0 | 0 |
| `bank_fee_deduction` | 8 | **0** | 8 | 0 |
| `post_dated_settlement` | 8 | **0** | 0 | 8 |
| `double_settlement` | 8 | **0** | 0 | 8 |
| `stale_utr_reuse` | 8 | **0** | 0 | 8 |

**It failed on its first run**, 24 of 32 wrong, and finding that is what the suite is for. Three of
those failures were real and all three are now closed.

`build_all_chains` keyed settlements by `payment_id` in a dict comprehension, so a payment settled
inside two batches lost one silently and the chain tied perfectly against the survivor: a transaction
paid twice, reported clean. Nothing read `settled_at`, so a settlement dated two days before its own
capture also tied. And two settlements could carry the same UTR with nothing noticing, which is a
payout reference claimed twice.

`app/chain/controls.py` catches all three. They are controls rather than matching rules deliberately:
each asserts an invariant that must hold of the data whatever the matcher concludes, and each flags
rather than resolves, because which of two payouts was the erroneous one is a question about the
gateway rather than about this arithmetic. Silent on ordinary batches across three seeds.

`stale_utr_reuse` was published as a declared blind spot in an earlier version of this table, on the
argument that a causal chain carries no UTR. That was right about the chain and wrong about the
batch: a settlement carries one, and "a payout reference identifies one payout" needs no matching
heuristic to check. The declaration was also propping up a badly built shape, which gave each of
those settlements a fresh random UTR and so contained no reuse to detect. There are no declared blind
spots now.

Reproduce: `python scripts/generate_generalization_evidence.py`. Raw:
[`generalization-2026-09-01.json`](evidence/generalization-2026-09-01.json).

## What each tier is worth

Cumulative, because that is how the pipeline works: Layer 0 only sees what the matching engine could
not close, and a model only sees what Layer 0 could not finish. 900 transactions, seeds 1-3.

| Tier | Resolved | Marginal | Wrongly resolved | tx/sec |
|---|---|---|---|---|
| 1 matching engine | 765 | 765 | **0** | 431,179 |
| 2 + Layer 0 residual | 765 | 0 | **0** | 587 |
| 3 + a model on what is left | 765 | 0 | **0** | 2.58 |

Layer 0 resolves nothing outright here and **bounds 116**: it turns "no explanation" into "one of k
enumerated, arithmetically valid explanations", which is what makes the 1/k chance baseline
computable rather than argued. Reporting it as zero marginal resolutions without that column would
make it look like dead weight.

The model is credited with no resolutions in this table. Whether it is right is measured in the
reading and residual experiments; asserting it in an ablation would be circular. What the table
prices is the cost: 135 transactions reach it, 52 seconds at a measured 2.58 tx/sec, against a
whole-batch deterministic pass in well under a second.

Reproduce: `python scripts/generate_ablation_evidence.py`. Raw:
[`ablation-2026-09-01.json`](evidence/ablation-2026-09-01.json).

## What the tuning actually buys

RESULTS says the system is built to escalate rather than guess. That is a claim about constants
nobody had been shown, so each is swept across its usable range, one at a time.

| Constant | Shipped | First wrong resolution | Margin |
|---|---|---|---|
| `ROUNDING_EPSILON` (matching engine) | 100 | **5,000** | 50x |
| `DEFAULT_TOLERANCE_PAISE` (Layer 0) | 10 | never, in range | trades resolutions for admitted ambiguity |

`ROUNDING_EPSILON` is the knob that governs correctness: the delta the engine will call FX noise and
close without further evidence. At the shipped 100 it posts no wrong resolution across 900
transactions; the first appears at 5,000, which is a fifty-fold margin. Below 3 it starts refusing
work it could safely have done, so the operating point is wide rather than lucky.

`DEFAULT_TOLERANCE_PAISE` does not trade correctness at all. It trades resolutions against
under-determination: at 0 Layer 0 closes 12 cases and admits 99, and past 5 it closes none and admits
116. Widening it does not buy wrong answers, it buys admitted ambiguity, which is the behaviour the
architecture is supposed to have.

**A first version of this sweep reported that `DEFAULT_TOLERANCE_PAISE` "does not govern
correctness".** That was true only because the sweep scored it against the matching engine, which
never calls the resolver: setting the constant to 99,999 leaves every matching-engine resolution
byte-identical. The number was real and measured nothing. It now scores against Layer 0's own output.

Reproduce: `python scripts/generate_sensitivity_evidence.py`. Raw:
[`sensitivity-2026-09-01.json`](evidence/sensitivity-2026-09-01.json).

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

## Scored once, on seeds nothing had touched

Every held-out figure above was re-measured across passes. The reading experiment, three-source and
the Q&A benchmark were each run many times while the system around them changed, and the numbers that
survived are the ones I kept. That is multiple testing on a held-out set: the intervals are narrower
than they should be and the point estimates are optimistic by an amount nobody had measured.

So a set was scored exactly once, on seeds no experiment in this repository has ever used, with
whatever came out being what ships.

| | Published | Single-shot | Delta |
|---|---|---|---|
| keyword rule, seen phrasing | 95.2% | 96.4% | +1.2 |
| keyword rule, held-out | 61.7% | 62.4% | +0.7 |
| its gap | -33.5 | **-34.1** | -0.6 |
| denial read as confirmation, held-out | 161 (38.3%) | **158 (37.6%)** | -3 |
| three-source, no cycle parsing | 88.0% | 88.7% | +0.7 |
| three-source, estimated weights | 91.3% | 90.0% | -1.3 |
| three-source, regex parser | 88.0% | 88.7% | +0.7 |

**Everything reproduced.** The rule still collapses on phrasing its author never saw, still reads a
denial as a confirmation in roughly 38% of judgements, and estimated weights still beat the regex on
held-out phrasing. Nothing moved by more than 1.3 points and the direction of every finding held.

That validates the published figures rather than replacing them, and it is one seed rather than a
new distribution, so it bounds the inflation from repeated measurement rather than eliminating the
concern. The mechanism is enforced in code: `app/final_holdout.py` refuses to overwrite a scored
holdout, so a second run raises instead of quietly producing a nicer number.

Reproduce: `python scripts/score_final_holdout.py`, which will refuse. Raw:
[`final/reading.json`](evidence/final/reading.json),
[`final/three_source.json`](evidence/final/three_source.json).

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

### Resolved is not the same question as handled correctly

The match rate above counts every escalation as a transaction the system failed to close. Six of the
eighteen escalations on seed 42 are `genuine_error`, a category the policy forbids closing at any
accuracy, so escalating them is the right answer and the strict rate scores the system down against
its own rule.

| | Strict resolution | Correct disposition | Wrongly resolved |
|---|---|---|---|
| mock, seed 42 | 85.0% | **90.0%** | **0** (0.0% of the batch) |

Three things stop this being a softer number to hide behind. The strict rate is printed beside it
always. Auto-resolving a forbidden category counts as **wrongly resolved**, never as a correct
disposition, so the figure cannot be reached by closing exactly what the policy exists to stop
(`test_disposition_cannot_be_gamed_by_resolving_everything`: ten forbidden cases resolved scores 0%,
the same ten escalated scores 100%). And wrongly-resolved is a share of the whole batch rather than
of the resolved subset, so resolving less while being wrong about more of it buys nothing.

Escalating something the system could safely have closed is still a miss, not a win. Refusing is
only correct where the policy forbids resolving.

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
