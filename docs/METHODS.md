# Methods

The statistical machinery behind the numbers in [RESULTS.md](RESULTS.md), and the derivations that
make them checkable. Nothing here is needed to read the results; everything here is needed to argue
with them.

Sources and licences for every borrowed method: [CREDITS.md](CREDITS.md).

## The autonomy gate, and why Wilson was the wrong bound

`CalibrationHistory.add_and_report()` recomputes a lower bound on a category's accuracy after every
batch and grants autonomy the first time it clears 90%. Wilson's coverage guarantee holds at a
**fixed** n. Re-checking and stopping at the first crossing is optional stopping, and the guarantee
does not survive it.

The right way to show that is to test what the bound promises, which is P(bound > true accuracy) at
most 5%. Simulating the real gate, checked every 5 decisions to n=300:

| True accuracy | P(bound > truth) | P(gate opens at 90%) | Same, at a fixed check |
|---|---|---|---|
| 88% | **9.72%** | 3.12% | 0.12% |
| 90% | **10.12%** | 10.12% | 1.55% |
| 92% | **8.77%** | 35.70% | 17.18% |

The promise is broken at every accuracy tried, at about twice its stated level, in the direction that
hands out autonomy nothing earned.

An earlier version of this compared the 3.12% to the 5% figure and called it a 25× inflation. Those
are different quantities: 3.12% is how often the gate opens, and the 5% is what the bound claims
about itself. The comparison flattered the bug, because the real violation is uniform rather than
confined to one row.

### The replacement

`app/calibration/confidence_sequence.py` implements the betting construction of Waudby-Smith and
Ramdas. To test whether the true accuracy could be as low as m, bet repeatedly against that
hypothesis and watch the wealth:

    K_t(m) = product over i of (1 + lambda_i * (X_i - m))

X_i is 1 for a correct decision and 0 for a wrong one. If the true accuracy exceeds m the bets win on
average and the wealth compounds; wealth reaching 1/alpha rules m out, uniformly over time. The bound
is the smallest m the wealth has not ruled out.

`lambda_i` must be **predictable**, chosen from decisions 1..i-1 only. Sizing the bet on X_i using X_i
is the same cheat as fitting a quantile and scoring it on the same data, and it is the condition the
whole construction rests on.

Validated rather than asserted. Across true accuracies 0.85 to 0.98 under continuous checking, the
bound exceeded the truth in 0% to 2% of sequences, inside the 5% it claims. At a true 88% it reaches
a 90% gate 0.75% of the time against Wilson's 2.50%.

### What it costs

| n perfect decisions | Wilson | Anytime-valid | Clears 90%? |
|---|---|---|---|
| 40 | 91.2% | 86.6% | Wilson only |
| 50 | 92.9% | 89.1% | Wilson only |
| **55** | 93.5% | **90.0%** | both |

55 is the first n that qualifies, where the old gate qualified at 40. The revocation drill's seeding
count moved from 40 to 60 for the same reason.

### It is order-sensitive, and the counts-only path assumes the worst

The bound depends on **when** the failures happened, because the bets are sized from history. At 57
correct of 60:

| Ordering | Bound |
|---|---|
| failures first | **84.3%** |
| spread evenly | 84.3% |
| failures last | 90.3% |

A first draft of `accuracy_lower_bound` replayed the correct decisions first, which is the ordering
that flatters the bound most, and would have reported 90.3% for a cause whose worst case is 84.3%.
Where the true order is recorded the calibrator uses it; the counts-only path now assumes the worst.

## Selective prediction: what the threshold buys

Escalating what a machine is unsure of is selective prediction, reported as coverage against
selective risk. Coverage is counted per category, because that is how the gate works, so the curve is
a step function rather than a smooth dial.

| Gate | Coverage | Selective risk | Automated |
|---|---|---|---|
| up to 0.85 | 59.3% | 1.0% | `duplicate_refund`, `netting_trap` |
| 0.86 to 0.88 | 36.4% | **1.7%** | `netting_trap` |
| 0.89 and above | 0.0% | n/a | none |

The middle row is what a single threshold hides: **raising the gate raised risk**. `duplicate_refund`
is perfect at 37 of 37 and still scores a lower bound than `netting_trap` at 58 of 59, because the
bound rewards evidence and not only accuracy. A stricter bar drops the flawless category and keeps
the flawed one.

Reproduce: `GET /api/risk-coverage`.

## Match weights estimated instead of chosen

The three-source matcher scores candidates with constants I picked: 2.0 for an exact UTR, 1.0 for an
exact amount, 0.5 for an exact date, and the raw name similarity. Scoring by hand-chosen constants is
the standing criticism of fuzzy matching, so the evidence carries a column that does not.

Fellegi-Sunter estimates two probabilities per field, m = P(agrees | true match) and u = P(agrees |
non-match), weighting agreement by log2(m/u). Fitted on a calibration batch at seed+100 and scored on
the evaluation seed, because estimating m and u from the pairs you then score is memorisation.

| Field | Hand-chosen | Estimated | m | u |
|---|---|---|---|---|
| exact UTR | 2.0 | **9.02** | 0.520 | 0.001 |
| exact amount | 1.0 | 0.55 | 0.999 | 0.684 |
| exact date | 0.5 | 0.34 | 0.653 | 0.516 |
| merchant name | up to 1.0 | **0.19** | 0.687 | 0.600 |

Merchant-name similarity agrees on 69% of true matches and 60% of false ones, so it separates very
little, and the hand-tuned scorer was adding up to a full point of it to every candidate. An exact UTR
is worth nine, not two.

The effect is one-directional rather than a clean win. On held-out phrasing the estimated weights take
the structured-only baseline from 85.3% to 92.0%, beating the regex parser on 14 cases and losing 4,
exact McNemar p = 0.0309 — though that falls to p = 0.24 if two cases are conceded, so it is
significant and not robust. On seen phrasing they buy nothing at all, 91.3% against an identical
91.3%, because the hand-chosen weights were tuned against phrasing I had seen. That is the same effect
the reading experiment measures one layer up.

What it buys is the argument, not the number. "Even with weights estimated from the data, the
structured fields tie" is a claim about the problem; "with my weights, they tie" was a claim about me.

The shipped matcher still uses the hand-chosen weights. This column exists to bound how much of the
result depends on them.

## Semantic entropy: a fourth escalation signal, and a null

The cascade escalates on three signals and none works. Self-reported confidence is uninformative, the
verifier cannot fail in choice mode, and the tie count describes the advice rather than the reading.
The literature's answer is to stop asking the model how sure it is and instead resample it, measuring
how much it disagrees with itself.

The usual hard part, deciding when two free-text answers mean the same thing, costs nothing here:
Layer 0 has already enumerated the valid decompositions, so agreement is an equality check.

**The first measurement was at the wrong level.** Entropy over the final decomposition was exactly
zero on every case. On 41 of 59 cases the deterministic scorer mapped every resampled reading onto
the same choice, absorbing the model's variation before it reached an answer. That is a good property
of the architecture and a useless signal for a gate.

Measured over the raw readings instead, on 59 under-determined cases at 5 samples each:

| | Value |
|---|---|
| mean entropy, correct readings | 0.227 (n=15) |
| mean entropy, wrong readings | 0.426 (n=44) |
| AUROC | 0.633 |
| permutation test, 20,000 label shuffles | **p = 0.0505** |

So it points the right way and is **not distinguishable from chance at this n**, landing just the
wrong side of the line. 0.633 is short of what a gate needs regardless. Suggestive, not established.

The permutation test is part of the script rather than something run once, because an AUROC above 0.5
on 59 cases is easy to get by luck and the difference between a signal and a hopeful number is
whether that was checked.

Reproduce: `python scripts/generate_semantic_entropy_evidence.py`. Raw:
[`semantic-entropy-2026-08-30.json`](evidence/semantic-entropy-2026-08-30.json).

## Why the forecast interval over-covers

Fitting quantiles on one batch and applying them to another is **split conformal prediction**, which
is guaranteed to over-cover rather than under-cover, by at most 1/(n+1). At the n measured here that
bound is +0.005 points against an observed +7.5, so the finite-sample term explains almost none of
the gap.

Ties do. Settlement lag is close to discrete at day granularity:

| Rail | n | Distinct values | Heaviest tie |
|---|---|---|---|
| card | 698 | 35 | 9.6% |
| netbanking | 284 | 53 | 4.6% |
| upi | 1,018 | 41 | 9.8% |

Thirty-five distinct values across 698 observations, one holding 9.6% of the mass, so a quantile
cannot move a little: it steps across a whole block.

Vovk's smoothed conformal predictors break ties with a uniform draw and would close most of the gap.
Deliberately not adopted: it would make the same payment yield a different window on each call, and a
finance tool that cannot reproduce its own answer trades a real property for a cosmetic one.

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
Compositionality does that; tolerance amplifies it.

The recovery column makes the rest meaningful: without it, "the model chose wrong" and "the right
answer was never on the table" are indistinguishable. It found a real bug, percentage candidates
computed off the post-fee hop, giving 11/60.

## Paired tests, not overlapping intervals

Every comparison in RESULTS runs over identical cases, so the tests are paired. Independent
confidence intervals ignore that pairing and are badly conservative: the three-source intervals
overlap while the exact McNemar test on the same cases is decisive.

Each paired result also reports what happens if two cases were mis-scored. `qwen2.5:7b-instruct` on
three-source is p = 0.019 and moves to p = 0.12 under that concession: significant, and not robust to
a couple of judgement calls. The same test on `gpt-oss-20b` is p < 0.000001 and still p = 0.0002 after
conceding two, which is what a result that does not depend on judgement calls looks like. Reporting
only the first number in either case would overstate it.

See `app/calibration/significance.py`.

## One seed is not a measurement

Every model column in this project was scored on one batch at one seed, and the three-source headline
was an exact McNemar p = 0.049 — significant, and one discordant case away from not being. Sweeping
the deterministic columns across ten seeds showed the case set moving between 128 and 138 correct out
of 150. A margin that thin resting on a draw that wide had not been shown to exist.

Re-run across five independent draws, held-out phrasing, `qwen2.5:7b-instruct`:

| Seed | Regex | Model | Wins | Losses | p |
|---|---|---|---|---|---|
| 42 | 128/150 | 140/150 | 19 | 7 | 0.0290 |
| 1 | 133/150 | 140/150 | 11 | 4 | 0.1185 |
| 7 | 136/150 | 139/150 | 9 | 6 | 0.6072 |
| 100 | 138/150 | 143/150 | 8 | 3 | 0.2266 |
| 202 | 134/150 | 140/150 | 9 | 3 | 0.1460 |

The direction holds at every seed, with no losses and no ties, and **only one of the five is
significant on its own**. Pooled over 750 settlements the result is 56 wins to 23, p = 0.0003,
surviving the two-case concession at p = 0.0015.

Both halves matter. Four of five draws, published alone, would have read as no result; the one that
was published read as a result at p = 0.049. The effect is real, and the evidence originally offered
for it was a lucky draw from a distribution nobody had looked at.

Pooling is legitimate here only because the seeds are independent draws from one generator and the
readers are fixed across them, so the discordant pairs are exchangeable. It would not be legitimate
to pool after choosing which seeds to include, which is why the seed list is a script default rather
than an argument I tuned.

The hosted columns stay single-seed, and that is a budget limit rather than a judgement: five seeds is
roughly 617,000 tokens against a free tier capped at 200,000 a day per model. Their p-values carry the
same fragility this section measures, and no pooled figure is claimed for them.

Reproduce: `python scripts/generate_three_source_seed_stability_evidence.py`. Raw:
[`three-source-seed-stability-2026-09-01.json`](evidence/three-source-seed-stability-2026-09-01.json).

## Data-integrity controls

Three defects tie arithmetically and would reconcile clean, because the matching engine reconciles
amounts and reads neither `settled_at` nor a UTR. Each is an invariant rather than a heuristic.

| Control | Invariant | Found by |
|---|---|---|
| `duplicate_settlement` | a payment settles at most once | the generalisation suite, first run |
| `impossible_timing` | money cannot arrive before the capture it settles | the generalisation suite, first run |
| `recycled_reference` | a payout reference identifies one payout | the generalisation suite, after its own shape was fixed |

All three flag rather than resolve: which of two payouts was erroneous is a question about the
gateway's behaviour, not about the statement's arithmetic. All three are silent on ordinary batches
across seeds 1, 42 and 100, because a control that fires on clean data trains the reader to ignore it.

They are deliberately not matching rules. Tuning the matcher against shapes the generalisation suite
invented would stop it measuring generalisation and start it measuring how fast a special case can be
written, which is the one thing that suite exists to prevent.

