# What this can't do

Twelve limits, ordered by how much they constrain the claims here.

## Every reading result rests on one model family

`qwen2.5:7b-instruct` and `qwen2.5:14b-instruct`. Two sizes, one family. "A model reads better than a
rule" is really "qwen reads better than a rule" until a second family is measured on the same cases.

The harness exists and works. `app/resolver/cycle_reader.py` and
`scripts/generate_reading_evidence.py` both take `--groq-model`, and live calls against
`openai/gpt-oss-20b` return correct verdicts on held-out phrasing where the regex returns nothing. The
measurement is blocked on Groq's free-tier daily token quota, which this session had already consumed:
199,913 of 200,000. A first attempt also produced a column that scored identically to its baseline in
both conditions, because a missing key was swallowed as "no reading available" (see
[WHAT_BROKE.md](WHAT_BROKE.md)). That is fixed, and the run is a quota reset away. No claim rests on
the second family.

## The held-out phrasing is held out from the parser, not from me

The headline result measures the keyword rule dropping 33.6 points on phrasing its cue list never saw,
against 5 to 7 points for the models. Both phrase banks are mine. A test asserts the held-out bank
contains none of the rule's negation cues, so it does test the parser. It does not test either reader
against real bank text.

What the measurement supports is narrow: a rule tuned to phrasing its author has seen generalises
worse than a model does to phrasing neither has seen. No absolute accuracy on production data follows.
The same applies to the three-source corruption mix and to the Q&A question bank. Truncated UTRs,
house-style names, date slip and free-text cycle references are all real patterns, but the mix is mine.

## On familiar phrasing, the best rule I could write wins

Choosing among Layer 0's valid decompositions, the keyword baseline scores 42.4% against the local
model's 25.4% when the advice is phrased the way its cue list expects. The Q&A router does the same,
83.3% against 63.3%. The model's advantage is in generalisation and failure mode. Anyone hoping to
find "the LLM beat the rules" in this repo will not find it.

## On the compound residual, free parsimony is at least as good as any reader

"Always take the fewest-component explanation" scores 31.7% against the 14b reader's 26.7% on held-out
phrasing. An earlier version of this file said parsimony beat every reader. The paired test on those
same cases gives p = 0.55, so the difference is not distinguishable at n=60. What is clear is that
reading did not help there. Where free text is one signal among several competing with a structural
prior, it does not pay for itself at these model sizes.

## Cascade routing does not work

20.0% end to end, worse than free parsimony, for 2.4s per case. Both escalation gates were wrong. The
model tiers escalate on verification failure, which in choice mode can never happen, so the 14b tier
absorbed zero cases. Tier 0's gate measures whether the advice discriminated, not whether the reading
was correct. No signal I tried correlates with correctness.

## The residual numbers rest on n≈60, and no cause has earned autonomy

That is enough to separate a 38.3% dangerous-error rate from 3.4%. It is not enough to rank two models
against each other. A per-cause Wilson lower bound rarely clears the 90% gate at this sample size, so
`auto_attribute_causes` is usually empty. Per-cause autonomy is a mechanism with real numbers behind
it, and not yet a system that has earned autonomy on this task.

## Three-source matching sits beside the product, not inside it

`app/resolver/entity_resolution.py` and its generator run from their own evidence script and tests. No
batch run, dashboard panel or calibration gate consumes them. `compound_delta` and the residual stage
are off by default behind `enable_compound_delta`, so every evidence file measured before this
architecture stays valid. The cost is that the documented quickstart does not show the residual
pipeline unless you tick the box.

## Four of five causal-chain hops are real Razorpay API objects

Order, captured payment, fee/tax and refund are real. Settlement is structurally excluded from test
mode on any account, confirmed against Razorpay's own docs, so the synthetic generator covers that leg
alone.

`POST /api/webhooks/razorpay` does real HMAC-SHA256 verification and real `settlement.processed`
parsing. It cannot reconcile on its own, because the order, payment and ledger side of a transaction
lives in the merchant's own integration and never in a settlement-only payload. The Tally XML export
is verified against Tally's published sample documents. No TallyPrime licence was available.

## The forecaster's refusal layer is validated on one reason out of five

`partial_capture`, `not_captured`, `non_positive_net` and `sla_already_breached` are implemented and
unit-tested, and none of them fires on this generator's data. Only `refund_in_flight` is exercised
against real batches, so the measured effect of refusing rests entirely on one reason.

Refusing improves amount accuracy and not date coverage, because every reason currently firing is
amount-related. A refund changes what settles, not when. The forecaster still has no reason to decline
a prediction on timing grounds beyond an already-breached SLA, and I could not find an observable
signal for one: settlement lateness in this generator is assigned at random, and a business-day
window would model nothing, since 29.8% of settlements land on a weekend against 28.6% for uniform.

## The calibrated interval is conservative, and it is not the default

Empirical coverage sits above nominal at every level, by as much as 7.5 points at the 70% mark. That
is safe in the direction that matters and it is still miscalibration: a stated 70% that delivers 77.5%
is wider than a treasury team needs. The likely cause is that settlement lag is close to discrete at
day granularity, so empirical quantiles snap to the same value.

The shipped default remains the SLA tolerance window, which needs no settlement history and therefore
works on a merchant's first batch. It states no confidence level, so its 87.2% coverage is a hit rate.
A blind backtest against a hidden schedule drift keeps median amount error at 0.17% but swings
interval coverage from 3% to 100% seed to seed. The amount forecast survives schedule staleness. The
declared date window does not.

## The Q&A benchmark is six questions, and one is unanswerable on held-out phrasing

Six questions per batch across five seeds is 30 scored answers per condition, which separates 83.3%
from 0.0% and does not support finer comparisons. `busiest_date` has no tool that groups settlements
by date, so on held-out phrasing no provider can answer it. That is a missing capability rather than a
scoring artefact, and it is counted as a miss for everyone.

## Not horizontally scaled

One FastAPI instance, SQLite. A load test found 100% success and gracefully-degrading latency up to 32
concurrent requests, which bounds "this would fall over under load" within the range tested. Postgres
and worker-pool narration are deferred.

---

Limits of the architecture this project replaced are in
[RESULTS_SUPERSEDED.md](RESULTS_SUPERSEDED.md), alongside the experiments they constrain.
