# What this can't do, and what it refuses to do

The complete list. README keeps the five most load-bearing of these as one-line teasers; this is
the full picture, including the ones that didn't make the cut for space, not because they matter
less.

**The "held-out" advice phrasing is held out from the parser, not from me.** The generalisation
result this project now leads with — the keyword rule dropping 33.6 points on phrasing its cue list
never saw, while both models drop 5–7 — is measured against a second phrase bank that I also wrote.
That makes it a genuine test of the *parser* (a test asserts the held-out bank contains none of the
rule's own negation cues, so the rule really is meeting unfamiliar constructions) and **not** a test
against real bank text. Someone else's settlement advice, or a real remittance file, could break both
readers in ways neither bank anticipates. What the measurement supports is narrow and worth stating
exactly: a rule tuned to phrasing its author has seen generalises far worse than a model does to
phrasing neither has seen. It does not establish either reader's absolute accuracy on production
data, and no claim to that effect is made anywhere in these docs.

**On held-out phrasing, the best end-to-end strategy is a free heuristic that ignores the advice
entirely — and it beats every model reader.** "Always take the fewest-component explanation" scores
31.7%, against 26.7% for the 14b reader, 20.0% for 7b, 8.3% for the collapsed keyword rule and a 6.1%
chance floor ([RESULTS.md](RESULTS.md)). Reading this text at 73–82% accuracy is comfortably better
than a broken rule and still not accurate enough to beat a trivial structural prior. This is the
sharpest limit on the argument this project makes, so it is stated here and in the README rather than
left for a reader to find: what the evidence supports is that a model reads settlement advice better
than a rule does and fails far more safely, **not** that adding the model improves the end-to-end
result at these model sizes. Whether a stronger reader crosses that line is an open question this
project has not answered — 14b closes about half the gap to parsimony that 7b leaves, which is
suggestive and nothing more.

**Cascade routing is built and measured and does not work.** `app/resolver/cascade.py` scores 20.0%
end to end, worse than free parsimony, having added ~2.4s per case. Its escalation gates were designed
before the numbers came in and both turned out to be structurally wrong: the model tiers escalate on
verification failure, which in choice mode can never happen (a chosen option is arithmetically valid
by construction), so the 14b tier absorbed zero cases; and tier 0's gate measures whether the advice
*discriminated* rather than whether the reading was *correct*, so it absorbed six cases and got none
of them right. The honest summary is that I could not construct a useful escalation signal for this
task — self-reported confidence is uninformative, verification is trivially satisfied, and tie count
measures the wrong quantity. It ships as measured rather than tuned or withdrawn.

**On the residual, the best rule I could write still beats the model on familiar phrasing.** Choosing
among Layer 0's valid decompositions, the keyword baseline lands well ahead of the local model when
the advice is phrased the way its cue list expects. The model column is not dropped for losing, and
the honest summary is that the model's advantage here is specifically in *generalisation and failure
mode*, not in raw accuracy on the phrasing distribution the rule was tuned for. Anyone reading this
project for "the LLM beat the rule" will not find that claim; what is claimed is narrower and, I
think, more useful.

**Layer 0's decomposition resolver closes 0% of the exceptions it sees, by construction.** The
architecture's headline is that the deterministic layer takes everything it can first — and on the
whole batch that is true and large (85%, almost entirely the matching engine's own work). But of the
exceptions that actually reach the *decomposition* resolver, it fully resolves essentially none,
because a delta produced by several compounding causes is under-determined almost by definition. Its
job there is to establish which cases arithmetic cannot answer and exactly how unanswerable each one
is (the k that gives the 1/k floor), not to close them. Quoting "Layer 0 resolves N%" without that
distinction would misdescribe what the module does.

**The residual numbers rest on n≈59 cases per condition.** Enough to separate a 38.3% dangerous-error
rate from a 3.4% one, not enough to rank the two models against each other with confidence, and small
enough that a per-cause Wilson lower bound rarely clears the 90% autonomy gate yet — which is why
`auto_attribute_causes` is usually empty on a single run. That is the calibration layer working as
designed rather than a result being suppressed, but it does mean the per-cause autonomy story is
currently a mechanism with real numbers behind it, not a system that has actually earned autonomy on
this task.

**`compound_delta` and the residual stage are off by default.** They run only with
`enable_compound_delta`, deliberately, so every evidence file and number measured before this
architecture existed stays valid rather than being silently invalidated by a default change. The cost
is that a judge running the plain documented quickstart does not see the residual pipeline unless
they tick the box in the dashboard or pass the flag.

**Not horizontally scaled, though a real load test found no acute problem within the range tested.**
One FastAPI instance, SQLite. A real load test against a genuinely running server (not the in-process
`TestClient` tests use) measured 100% success and gracefully-degrading latency at 1 through 32
concurrent `POST /api/run` requests (2.157s mean at concurrency=1, 4.750s at concurrency=32 — see
[RESULTS.md](RESULTS.md)) — this doesn't mean SQLite scales indefinitely, it means "this would fall
over under real concurrent load" isn't supported by what was actually measured. Worker-pool narration
and a move to Postgres remain real next steps for load well beyond this range, deliberately deferred
as unmeasured-as-necessary rather than built speculatively against a problem not shown to exist yet.

**The Razorpay webhook receiver verifies and parses; it does not reconcile on its own.**
`POST /api/webhooks/razorpay` (`app/webhooks/razorpay.py`) does real HMAC-SHA256 signature
verification and real `settlement.processed` event parsing, both checked against Razorpay's own
current docs, not guessed — the actual gap this limitation used to name. What it structurally can't
do: reconstruct a full causal chain from a settlement-only webhook, since the order/payment/ledger
side of a real transaction lives in the merchant's own separate integration (order creation, payment
capture callbacks), never in a settlement event alone. A real merchant integration would take the
parsed, verified settlement here and feed it, alongside its own already-known order/payment/ledger
data, into the existing `/api/transactions/evaluate` pipeline — that hand-off is the integration's own
job, not something a settlement-only payload can supply by itself.

**`recall_similar_resolutions`'s persisted history doesn't separate mock from real-provider
confidence.** Now that it persists across runs (see [ARCHITECTURE.md](ARCHITECTURE.md)), the
`avg_confidence` it reports blends every provider's logged decisions together — mock's fixed
heuristic confidence values alongside genuine Ollama/Groq ones. Unlike the calibration layer, which
explicitly excludes mock decisions from every trust gate, this tool has no such filter: it's
informational context for the model to reason with, not something that gates auto-resolve, so a
blended number is disclosed rather than silently biased toward whichever provider ran more often
locally. It also only reaches back as far as whatever `backend/data/audit_log.db` (gitignored) has
locally accumulated — a fresh clone starts with the same clean slate as before this fix.

**The Tally XML export is verified against Tally's own published sample documents, not a live
TallyPrime install** — no license was available to test against. Structurally correct per the
published spec, but not confirmed to actually import cleanly into a real Tally instance.

**The fee-leak detector ships three patterns** (blended-rate overcharge, GST-computed-on-the-wrong-base,
GST-computed-at-the-wrong-rate), not an exhaustive taxonomy of every way a fee could be miscomputed.
The third pattern (a real GST slab, e.g. 0%, mistakenly applied instead of 18%) is restricted to
`card`-rail transactions — verified directly that UPI's and netbanking's much smaller contracted fee
rates can produce a delta too small to clear the same rounding-noise threshold the other two patterns
use, at this generator's smaller transaction amounts. The architecture extends to more patterns
without a redesign — only these three are actually built, tested, and measured (₹1,497.40 recoverable
fees, ₹15,181.65 miscalculated tax in a real review batch; see [RESULTS.md](RESULTS.md)).

**Four of five causal-chain hops are real Razorpay API objects** (order, captured payment, fee/tax,
refund); the fifth, settlement, is structurally excluded from test mode on *any* Razorpay account —
confirmed against Razorpay's own documentation, not an account-specific limitation or something more
real test data would eventually unlock. The synthetic generator covers the settlement leg alone, for
exactly this reason. Full trail of what was actually tried against the real sandbox (a real payment,
a real partial refund, real non-null fee/tax fields): [`BUILD_LOG.md`](../BUILD_LOG.md).

**The forecaster is exact by construction on roughly 73% of transactions, against its OWN schedule.**
It reuses the merchant's own known fee/SLA schedule — real reference data, not a learned model — so
its MAPE and interval coverage come entirely from the ~27% of transactions with a refund, dispute, or
timing anomaly it structurally can't see in advance (verified: 88/120 exact matches to the paise at
the API's default batch size). The reported figure also moves with batch size — n=30 (the dashboard's
own default): 9.1% MAPE / 93.3% coverage; n=120 (the API's own default): 8.6%/90.8%; n=160: 4.1%/90.6%
— and no single size flatters both metrics at once (n=30 has the best coverage of the three and the
worst MAPE). The headline figure uses the dashboard's actual default, not whichever size looked best.

That number alone can't say what happens when the schedule itself is stale, since the predictor and
the batch that scores it share the exact same reference constants — a real gap, not a hypothetical
one: a merchant's actual contracted rate or real settlement timing can drift from what a platform's
schedule still assumes. A separate, genuinely-blind backtest ([RESULTS.md](RESULTS.md),
`app/forecast/blind_backtest.py`) scores the same predictor against a self-contained batch whose real
settlements were computed with a hidden, per-rail fee-rate/SLA-day drift the predictor never sees.
Measured over seeds 1–20: mean MAPE stays under 0.2% (fee-rate drift alone is a small fraction of
settled value), but interval coverage swings from 3% to 100% seed to seed, since a few days of SLA
drift can push the real settlement date entirely outside the predictor's own narrow tolerance window.
The amount forecast is robust to schedule staleness; the declared date-window confidence is not.

**Category discovery clusters within a run now, but only within a run.** Every proposal made so far
in the same batch is threaded into the next one, and a live Ollama run confirms it actually reuses a
name when the evidence genuinely matches (e.g. 5 of 7 `genuine_error` cases converging on the same
`post_refunds_to_settlement_mismatch`, correctly leaving 2 different cases unnamed) rather than
minting a fresh label each time, the behavior an earlier version measured (8 proposals, 6 distinct
names, 5 singletons). It still starts over on the next run — nothing persists across batches, so the
same real pattern seen in two separate runs gets no guarantee of the same name. The feature remains
an "unreviewed hypothesis" a human confirms, never a self-organizing category system. One build note
worth disclosing: the first attempt at this fix quietly regressed the local model's naming rate to
near zero (mentioning a prior-proposals section at all, even as an empty placeholder, pushed
`qwen2.5:7b-instruct` toward proposing nothing) — fixed by omitting that section from the prompt
entirely until a real named proposal exists to show. See [WHAT_BROKE.md](WHAT_BROKE.md).

**The Q&A agent's mock provider only routes on a few keywords.** A date pattern or a word like
"duplicate"/"anomaly" routes to a real, specific lookup; anything else — including an entirely
reasonable question like "how many transactions were escalated?" — falls back to detail on the
first 3 transactions in the batch, with an honest label pointing at `ollama`/`groq` for a real
answer. Disclosed here rather than left to surprise a judge on a default-provider run.

**The strongest deterministic netting solver breaks down at ordinary batch sizes once the group is
genuinely multi-way.** An earlier version of this file, and of [RESULTS.md](RESULTS.md), reported the
solver as reliable up to `n_total=1000`. That figure was measured with `group_size=3`, which — because
that parameter counts the target transaction itself — meant only two other transactions had to cancel,
a plain 2-sum. Re-run across group sizes 3/4/5 so all three k-sum paths actually execute, the real
frontier is much closer in: at a genuine four-member group the solver finds the true group on 19/30
seeds at n=100 and **1/30 at n=200**, because a coincidental smaller group cancels first (30/30 of the
failures at n=5,000). Compute time was never the limit — it stays under 2ms at every size tested. The
limit is disambiguation, and it arrives at batch sizes a real merchant sees every day.

**On the original, hand-built multi-way netting experiment** ([RESULTS.md](RESULTS.md)): even with a
verification tool available, the smaller local model (`qwen2.5:7b-instruct`) solved only 1 of 8
hand-constructed cases — and 4 of those 8 never converged on any answer at all, so the real capability
gap is narrower than "1/8" alone suggests but still real: of the runs that produced an answer, most
were wrong. One Ollama run also hallucinated a transaction id, received a real tool error back, and
narrated that error as a confirmed finding rather than recognizing the lookup had failed — see
RESULTS.md for the exact case.

**`multiway_netting_trap`, now a real shipped category, still can't use the recommended zero-cost
default.** `narrate_mock` fails structurally by construction (0/42, measured, not assumed — it never
calls the tools this category needs). Real providers do solve it on real generated batches (Ollama
5/7, Groq 6/7 — see [RESULTS.md](RESULTS.md)), so this category will very plausibly never clear
calibration's own auto-resolve bar under the mock-default demo path, and may take real accumulated
evidence to clear it even under Ollama. That's the honest, disclosed shape of the tradeoff, not
something worked around: a category the rule genuinely can't touch necessarily depends on the
real-provider path this project's whole "calibrated autonomy" story is built to require anyway.

**At real settlement-batch scale (hundreds of transactions in one batch), neither real provider holds
up cleanly, for two different reasons.** Ollama fails at every scale tested, 20 through 760
transactions (0/36) — not from context overflow, but a reasoning-strategy limit: it accumulates an
ever-growing candidate list across tool-call rounds instead of searching small subsets
systematically. Groq does solve the smallest case (n=20) but degrades quickly, and by n≥200 in this
project's own committed sweep every call returned a real `429` — though reading the actual error
message shows this was the account's free-tier **daily token quota** (200,000 tokens/day), exhausted
by cumulative Groq usage across this whole session's own earlier phases, confounded with (not a clean
substitute for) the genuine per-request context-size wall confirmed separately in an isolated check.
A magnitude-based pre-filter, tried as a fix, doesn't cleanly rescue either failure mode: loose enough
to rarely discard the real answer, it barely narrows a large request; tight enough to actually shrink
one, it discards the real answer over 40% of the time. See [RESULTS.md](RESULTS.md) for the full,
disclosed sweep.

**A bigger local model is not automatically a better one on a tool-budget-constrained task.**
`qwen2.5:14b-instruct` scores *worse* than `qwen2.5:7b-instruct` on `multiway_netting_trap` (1/7 vs
4/7) — it explores more per case (redundant tool calls, checking irrelevant ones) and more often runs
out of the same fixed round budget before converging. On `narration_explained`, a pure reading task
with no such budget tension, the larger model does score slightly better (5/5 vs 4/5). "Bigger model"
is not a substitute for measuring the actual task; see [RESULTS.md](RESULTS.md) for both real
comparisons. `gpt-oss-120b` was never included in any of this project's own comparisons — no verified
hosted or local path was confirmed available in this environment, and no claim is made that it was
tested.

**The held-out near-miss patterns show a real tool-design tension, not just a hard task.** Perturbed
`duplicate_refund`/`netting_trap` cases the exact-match rule can never confirm (built specifically to
break the "same author wrote the rule and the injector" problem) are also not solved by Ollama (0/21).
Reading the raw traces shows why: the model's own `verify_group_sum` tool is a strict exact-zero check
— correct and necessary for `multiway_netting_trap` — and it correctly reports a near-miss candidate
as NOT cancelling, so a model following its own instruction to never assert an unverified explanation
appropriately declines rather than guesses. The discipline this project credits elsewhere (a cautious
"I don't know" over a confident wrong guess) actively works against success on this specific task — a
real, disclosed limitation of the current tool design, not a smoothed-over negative result. See
[RESULTS.md](RESULTS.md) and [WHAT_BROKE.md](WHAT_BROKE.md).
