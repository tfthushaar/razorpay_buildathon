# What this can't do

Sixteen limits, ordered by how much they constrain the claims here.

## Two results hold across model families; the rest are still qwen alone

`openai/gpt-oss-20b` now has a reading column: 92.1% on seen phrasing and 96.2% on held-out, against
the rule's 61.7%. Its `keyword_rule` column reproduces the committed one exactly, so both runs scored
the identical case set, and 7 of its 420 seen judgements came back unparseable and count as wrong.

Three-source matching now carries four model columns across both families: qwen at 7b and 14b, and
`gpt-oss` at 20b and 120b, the latter two at 99.3% on held-out phrasing against the regex's 85.3%,
beating it on 21 paired cases and losing none. So the two strongest results in the project each hold
across two families, and the stronger of them across four models.

Scale is not what carries it, in either direction. The 120b matches the 20b case for case despite six
times the parameters, and qwen's 14b reads held-out phrasing worse than its 7b and falls below
significance doing it. Anyone reading this as "a bigger model reads better" is reading something the
evidence does not say.

The compound residual, cascade routing and the Q&A agent are still measured on qwen alone. Every
hosted column is one run at one seed rather than a repeated measurement, and covers held-out phrasing
only, because both conditions need about 250,000 tokens against a 200,000 daily cap.

The client was the reason the reading column took three attempts: it fired as fast as it could,
saturated the token ceiling, and fell back on exponential backoff, which is a collision detector
rather than a rate limiter. `app/narrator/pacing.py` now holds calls to 13.6 a minute against limits
read from Groq's own headers, and both hosted three-source runs above completed 247 calls each with no
rate-limit failure at all.

It is not applied to `generate_reading_evidence.py`. Pacing changes which calls come back
unparseable, and that script's committed column reports 7 unparseable judgements of 420; applying it
without re-running would leave a published number describing a client that no longer exists.

## Every published held-out figure was measured more than once

The reading experiment, three-source and the Q&A benchmark were each re-run across many passes while
the system around them changed, and the figures that survived are the ones I kept. Scoring a holdout
repeatedly and publishing the run you kept is multiple testing: those intervals are narrower than
they should be.

An untouched set has since been scored exactly once. Everything reproduced within 1.3 points and
every finding held its direction, which bounds the inflation rather than removing the concern -- one
seed is not a new distribution. `app/final_holdout.py` refuses to overwrite a scored holdout so the
rule survives my memory of it.

## Most single-seed p-values here would not have replicated

The three-source headline was published as exact McNemar p = 0.049 from one batch at one seed. Run
across five independent draws, the direction holds every time — no losses, no ties — but **only one of
the five is significant on its own**, and the published seed was that one. Pooled over 750 settlements
it is 56 wins to 23 at p = 0.0003, so the effect is real; the original evidence for it was a lucky
draw from a distribution I had not looked at.

This concern is not confined to the result that was checked. Every other model column here is also one
batch at one seed, and none has been swept. The hosted columns cannot be, at least not today: five
seeds is roughly 617,000 tokens against a free tier capped at 200,000 a day per model. Their p-values
should be read as carrying the same fragility, because nothing here shows they do not.

There is a smaller source of movement underneath it. Model calls are not deterministic, and the same
seed scored `qwen2.5:7b-instruct` at 141/150 in one run and 140/150 in another. One case, and worth
knowing before treating any single figure here as exact.

## The narrations are mine, and one of them is now Razorpay's

Razorpay's settlements documentation gives the real narration a merchant sees:

    NEFT CR: [bank name] [UTR] RAZORPAY SETTLEMENT

That is now the first of six house styles the generator produces, drawn with a real Indian bank
identifier. The other five are plausible formats I wrote, and the corruption mix applied to all of
them — truncated UTRs, house-style names, date slip — is also mine.

I implemented it, reverted it, and put it back. The revert was wrong. Adding a template changes the
generated data, and re-running two model families costs quota, so I protected a published number
instead of improving the input. The distinction I had collapsed is that the changes worth refusing are
silent ones; this one is disclosed and re-run in full, which is the legitimate way to move a published
figure. Everything downstream was re-run against it.

Measured across ten seeds rather than asserted, the documented format makes matching **harder**:
-1.4 of 150 on average, worse on 6 of 10 seeds. The baseline it produces is less flattering than the
one it replaced.

## What a real corpus could and could not check

The one public source of genuine bank descriptions I could obtain is a Mendeley corpus of 6,567
anonymised UK retail transaction descriptions (CC BY 4.0, DOI 10.17632/dnxtg6n4rv.1). It is real text
written by banks rather than by me, and it settles less than I hoped.

It cannot validate settlement matching at all. Only 62 of 6,567 descriptions carry any reference
number, none carries a UTR, and transfer entries are bare payee names. There are no settlement
narrations in it to mine, so the house styles here could not be derived from real ones. Publishing a
format claiming that provenance would have been worse than having none.

What it does check is name similarity, which is the one component that consumes text a bank actually
wrote. Against real renderings — `SAINSBURYS S/MKTS`, `LIDL GB  NOTTINGHA`, `CAPEWELL WINDOW CL` —
`_name_similarity` matched 8 of 8 at the shipped threshold with 0 false positives against 200
unrelated real descriptions.

It also nearly made this generator worse. Real descriptions truncate hard at 18 characters, with 2,297
rows sitting exactly there against ours averaging 40. Matching that ceiling is the obvious move and it
would have been wrong: Razorpay's own documented settlement narration is about 45 characters, so 18 is
that bank's retail feed and not a universal constraint. Two real reference classes disagree, and this
generator follows the settlement one. A test asserts the gap exists rather than quietly closing it
against the wrong reference.

The gap that remains is the one a format string never closes: every narration here is still generated.
What would close it is a settlement report and a bank statement from an actual Razorpay account,
joined on their real UTRs. A merchant can export both. It is not in this repository.

## The held-out phrasing is held out from the parser, not from me

The headline result measures the keyword rule dropping 33.6 points on phrasing its cue list never saw,
against 5 to 7 points for the models. Both phrase banks are mine. A test asserts the held-out bank
contains none of the rule's negation cues, so it does test the parser, but neither reader is tested
against real bank text.

What follows is narrow: a rule tuned to phrasing its author has seen generalises worse than a model
does to phrasing neither has seen. No absolute accuracy on production data follows. The same applies
to the three-source corruption mix and the Q&A question bank. Truncated UTRs, house-style names, date
slip and free-text cycle references are real patterns, but the mix is mine.

It applies hardest to the generalisation suite, because that is the table that most looks like
independent validation. Its four shapes are held out from the matching engine and not from me, and a
defect shape I never thought of is precisely the one I could not have put in it. Zero wrong
resolutions there means the engine stayed safe on assumptions it was not built against. It does not
mean the engine is safe on defects nobody in this repository imagined.

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
coverage holds at a fixed n. Tested against what the bound promises, P(bound > true accuracy) at most 5%, it came out at 9.72%,
10.12% and 8.77% across true accuracies of 88%, 90% and 92%, about twice its stated level. The gate now uses a confidence sequence valid at every
stopping time, and under it no category in the committed history auto-resolves: 88.4% for
`netting_trap` and 85.6% for `duplicate_refund` against a 90% bar.

Per-cause autonomy is a mechanism with real numbers behind it, and not a system that has earned
autonomy here.

## Three-source matching is reachable but not part of the batch loop

`POST /api/three-source/evaluate` runs it and the dashboard's evidence page renders it, so the result
is checkable without a checkout. It is still not part of the batch loop: no run, no calibration gate
and no escalation consumes a three-source match, and the shipped matcher uses my hand-chosen weights
rather than the estimated ones the fourth column measures.

The model column is left to the evidence script rather than the route, because it costs one model call
per candidate pair and a synchronous request is the wrong place for two hundred of them.

`enable_compound_delta` defaults to False in the API and True in the dashboard. The API default is
what every committed evidence file and reproduce command replays, so flipping it would change
published numbers; the dashboard default is what a reader sees on the first click, and the residual
architecture is the centre of the argument.

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
