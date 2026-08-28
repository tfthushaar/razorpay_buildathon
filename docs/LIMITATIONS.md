# What this can't do, and what it refuses to do

The complete list. README keeps the five most load-bearing of these as one-line teasers; this is
the full picture, including the ones that didn't make the cut for space, not because they matter
less.

**Not horizontally scaled.** One FastAPI instance, SQLite. Worker-pool narration and a move to
Postgres are identified next steps, deliberately deferred rather than built speculatively — nothing
in the current design blocks either, but neither is exercised or measured yet.

**No real settlement-ledger webhook.** `POST /api/transactions/evaluate` is the integration point a
real webhook consumer would call — the endpoint and its full pipeline are real and tested, but the
webhook receiver itself isn't wired to any actual Razorpay event stream.

**`recall_similar_resolutions` is per-run only.** It doesn't persist across runs — a fresh batch run
starts with no memory of prior resolutions in this session. Disclosed, not hidden: the tool call
itself is real and tested, its scope is just narrower than "all history ever."

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

**The forecaster is exact by construction on roughly 73% of transactions.** It reuses the merchant's
own known fee/SLA schedule — real reference data, not a learned model — so its MAPE and interval
coverage come entirely from the ~27% of transactions with a refund, dispute, or timing anomaly it
structurally can't see in advance (verified: 88/120 exact matches to the paise at the API's default
batch size). The reported figure also moves with batch size — n=30 (the dashboard's own default):
9.1% MAPE / 93.3% coverage; n=120 (the API's own default): 8.6%/90.8%; n=160: 4.1%/90.6% — and no
single size flatters both metrics at once (n=30 has the best coverage of the three and the worst
MAPE). The headline figure uses the dashboard's actual default, not whichever size looked best.

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

**On the multi-way netting experiment** ([RESULTS.md](RESULTS.md)): even with a verification tool
available, the smaller local model (`qwen2.5:7b-instruct`) solved only 1 of 8 hand-constructed cases
— and 4 of those 8 never converged on any answer at all, so the real capability gap is narrower than
"1/8" alone suggests but still real: of the runs that produced an answer, most were wrong. "An LLM
helps here" depends heavily on which model — this project's own local-first default is not the
strongest option for genuinely hard compositional reasoning, only for the deterministic-oracle
classification task the shipped narrator actually performs day to day. One Ollama run also
hallucinated a transaction id, received a real tool error back, and narrated that error as a
confirmed finding rather than recognizing the lookup had failed — see RESULTS.md for the exact
case.
