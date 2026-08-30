# What this can't do

Twelve limits, ordered by how much they constrain the claims here.

## The second model family is one run, and the rest are still qwen

`openai/gpt-oss-20b` now has a reading column: 92.1% on seen phrasing and 96.2% on held-out, against
the rule's 61.7%. Its `keyword_rule` column reproduces the committed one exactly, so both runs scored
the identical case set, and 7 of its 420 seen judgements came back unparseable and count as wrong.

That closes the reading headline as a claim about models. It does not close the rest. Three-source
matching, the compound residual, cascade routing and the Q&A agent are all still measured on qwen
alone, and the second family has one run behind it at 60 cases rather than a repeated measurement.

The client is also the reason this took three attempts. `groq_read` fires as fast as it can, saturates
a 7,500 tokens-per-minute ceiling, and falls back on exponential backoff, so a run can consume a day's
budget without finishing. Pacing the calls would fix it and would change which judgements come back
unparseable, so it needs its own re-run rather than a quiet edit to the script that produced these
numbers.

## The held-out phrasing is held out from the parser, not from me

The headline result measures the keyword rule dropping 33.6 points on phrasing its cue list never saw,
against 5 to 7 points for the models. Both phrase banks are mine. A test asserts the held-out bank
contains none of the rule's negation cues, so it does test the parser, but neither reader is tested
against real bank text.

What follows is narrow: a rule tuned to phrasing its author has seen generalises worse than a model
does to phrasing neither has seen. No absolute accuracy on production data follows. The same applies
to the three-source corruption mix and the Q&A question bank. Truncated UTRs, house-style names, date
slip and free-text cycle references are real patterns, but the mix is mine.

## On familiar phrasing, the best rule I could write wins

Choosing among Layer 0's valid decompositions, the keyword baseline scores 42.4% against the local
model's 25.4% when the advice is phrased the way its cue list expects, and the Q&A router does the
same. The model's advantage is in generalisation and failure mode. Anyone hoping to find "the LLM beat
the rules" in this repo will not find it.

## On the compound residual, free parsimony is at least as good as any reader

"Always take the fewest-component explanation" scores 31.7% against the 14b reader's 26.7% on held-out
phrasing. An earlier version of this file said parsimony beat every reader; the paired test gives
p = 0.55, so the difference is not distinguishable at n=60. Reading did not help there. Where free text
is one signal among several competing with a structural prior, it does not pay for itself at these
model sizes.

## Cascade routing does not work

20.0% end to end, worse than free parsimony, for 2.4s per case. Both escalation gates were wrong. The
model tiers escalate on verification failure, which in choice mode never happens, so the 14b tier
absorbed zero cases. Tier 0's gate measures whether the advice discriminated, not whether the reading
was correct.

A fourth signal has since been measured rather than guessed at. Semantic entropy resamples the reader
five times and scores how much it disagrees with itself. On 59 under-determined cases mean entropy is
0.227 on correct readings against 0.426 on wrong ones, AUROC 0.633, and a permutation test over 20,000
label shuffles gives p = 0.0505. At this n it is not distinguishable from chance, and 0.633 is short of
what a gate needs regardless. Suggestive, not established.

That run also found something about the architecture rather than the signal: on 41 of 59 cases the
deterministic scorer mapped every resampled reading onto the same decomposition. The scorer absorbs
the model's variation before it reaches an answer, which is why entropy over the final choice is
exactly zero and had to be measured over the readings.

## The autonomy gate was anti-conservative, and no cause has earned autonomy

n=60 is enough to separate a 38.3% dangerous-error rate from 3.4%, not enough to rank two models
against each other.

The gate itself was also wrong, in the dangerous direction. It recomputed a Wilson lower bound after
every batch and granted autonomy on the first crossing, which is optional stopping, and Wilson's
coverage holds at a fixed n. Simulated at a 90% threshold, a cause genuinely at 88% crossed 25 times
more often than a 5% guarantee implies. The gate now uses a confidence sequence valid at every
stopping time, and under it no category in the committed history auto-resolves: 88.4% for
`netting_trap` and 85.6% for `duplicate_refund` against a 90% bar.

Per-cause autonomy is a mechanism with real numbers behind it, and not a system that has earned
autonomy here.

## Three-source matching sits beside the product, not inside it

`app/resolver/entity_resolution.py` and its generator run from their own evidence script and tests. No
batch run, dashboard panel or calibration gate consumes them. `compound_delta` and the residual stage
are off behind `enable_compound_delta`, so every evidence file measured before this architecture stays
valid. The cost is that the quickstart does not show the residual pipeline unless you tick the box.

## Four of five causal-chain hops are real Razorpay API objects

Order, captured payment, fee/tax and refund are real. Settlement is structurally excluded from test
mode on any account, confirmed against Razorpay's own docs, so the generator covers that leg alone.

`POST /api/webhooks/razorpay` does real HMAC-SHA256 verification and `settlement.processed` parsing.
It cannot reconcile alone, because the order, payment and ledger side lives in the merchant's own
integration and never in a settlement-only payload. The Tally XML export is verified against Tally's
published sample documents. No TallyPrime licence was available.

## One refusal reason cannot fire under this fee schedule

Four reasons had never fired outside a hand-built object, leaving the measured effect of refusing
resting on `refund_in_flight` alone. `generate_pending_batch(edge_case_ratio=...)` produces the
missing shapes, and on 400 pending payments `sla_already_breached` fires 129 times,
`partial_capture` 80, `not_captured` 40 and `non_positive_net` 40. The flag defaults to 0.0, so no
committed forecast figure moves.

`non_positive_net` is the one that did not survive contact. This fee schedule is a pure percentage
with no flat floor, so fee plus tax is about 1.2% of any capture and never exceeds it. The only
capture driving net to zero is zero itself, a fully reversed authorisation, where `partial_capture`
fires too. It stays implemented because a flat per-transaction component makes it reachable for small
tickets, but under this contract it cannot fire alone.

Refusing improves amount accuracy and not date coverage, because every reason firing on the settled
batch is amount-related. The forecaster has no reason to decline on timing grounds beyond a breached
SLA, and there is no observable signal for one: lateness is assigned at random, and a business-day
window would model nothing, since 29.8% of settlements land on a weekend against 28.6% for uniform.

## The calibrated interval is conservative, and it is not the default

Empirical coverage sits above nominal at every level, by as much as 7.5 points at the 70% mark. Safe
in the direction that matters, and still miscalibration: a stated 70% delivering 77.5% is wider than a
treasury team needs.

Fitting quantiles on one batch and applying them to another is split conformal prediction, which is
guaranteed to over-cover rather than under-cover, by at most 1/(n+1). At the n measured here that
bound is +0.005 points against an observed +7.5, so the finite-sample term explains almost none of it.
The cause is ties: lag takes 35 to 53 distinct values per rail across hundreds of observations, with
single values carrying up to 9.8% of the mass, so a quantile cannot move a little without stepping
across a whole block. Smoothed conformal predictors break ties with a uniform draw and would close
most of the gap, at the cost of the same payment yielding a different window on each call. A finance
tool that cannot reproduce its own answer is a worse trade than a conservative interval, so this stays
as measured.

The shipped default is the SLA tolerance window, which needs no settlement history and works on a
merchant's first batch. It states no confidence level, so its 87.2% coverage is a hit rate. A blind
backtest against a hidden schedule drift keeps median amount error at 0.17% but swings interval
coverage from 3% to 100% seed to seed. The amount forecast survives schedule staleness; the declared
date window does not.

## The Q&A benchmark is nine questions, and every phrasing in it is mine

`settlements_by_date` closed the one question no provider could answer: nothing grouped settlements by
day, so "which day was busiest" had no tool behind it on held-out phrasing. Three questions were added
alongside it, taking the benchmark from 30 to 45 scored answers per condition.

45 supports the headline gap and nothing finer. Ranking two models against each other, or reading a
per-question number as anything but a direction, needs more. Both phrase banks are still mine, with
the limits that carries described above.

## Not horizontally scaled

One FastAPI instance, SQLite. A load test found 100% success and gracefully-degrading latency to 32
concurrent requests, which bounds "this would fall over under load" within the range tested. Postgres
and worker-pool narration are deferred.

---

Limits of the architecture this project replaced are in
[RESULTS_SUPERSEDED.md](RESULTS_SUPERSEDED.md), alongside the experiments they constrain.
