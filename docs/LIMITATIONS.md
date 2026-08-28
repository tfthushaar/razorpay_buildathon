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

**Category discovery proposes a hypothesis per case, it doesn't cluster.** Across 8 real proposals
from a live Ollama run, 6 distinct names came back, 5 of them singletons — a genuine taxonomy would
recur across similar cases; independently-prompted proposals, with no memory of prior proposals in
the same run, don't. The feature is accurately described as an "unreviewed hypothesis" a human
confirms, never as a self-organizing category system.

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
