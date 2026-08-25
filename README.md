# Settlement Reconciliation Copilot

**Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller**

Every settlement Razorpay sends a merchant is a black box — one bank credit standing in for
hundreds of transactions, net of fees, GST, refund offsets, and timing variance — and turning that
into books a finance team can close on is normally a manual, error-prone, multi-hour job every
settlement cycle. This system explodes that credit back into its transactions and narrates *exactly
which hop* broke in each one's causal chain, and — unlike a flat matcher — audits every fee against
the merchant's own contract, posts what it can prove straight into ERP-ready journal entries, and
only auto-resolves what it's statistically earned trust on, escalating the rest with a stated
reason instead of guessing. Full design rationale:
[docs/track04-settlement-reconciliation-copilot.md](docs/track04-settlement-reconciliation-copilot.md).
Build history, including every bug found and how it was fixed: [BUILD_LOG.md](BUILD_LOG.md).

**Live demo**: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)
(frontend on Vercel, backend on Render — deploys from this repo's `main` branch). Runs the `mock`
narrator provider by default (zero cost, instant); the provider dropdown also offers real Groq
inference for anyone who wants to see live LLM narration, at a smaller default batch size than this
project's own internal dev-testing size specifically so that choice doesn't come with a long wait.

## The money story, in one real run

The system safely auto-resolved **₹59,97,863.76 in netting-trap exceptions with zero human
review** — earned only after proving itself right on 15 distinct real cases, with a statistical
lower bound (90.4%) that actually cleared the trust threshold, not a guess — and automatically
reconciled **₹12,47,615.92 of a ₹13,02,997.38 batch** end-to-end, putting every remaining rupee it
couldn't explain in front of a human instead of guessing, with the exact reasoning and tool calls
behind each decision, not a black-box verdict. This is a real run against a live local model, not
mock — [raw output](docs/evidence/real-ollama-run-2026-08-24.json), independently re-verified live
against the committed database in this repo (see [Reproducing the results](#reproducing-the-results)
below).

Separately — this is a genuinely different axis of analysis, not a subset of the reconciliation
numbers above — a real fee-leak review of 20 transactions that all reconciled *perfectly cleanly*
(ledger and settlement agreed on every rupee) still found **₹2,634.50 in fee overcharges and
₹23,158.96 in wrongly-computed GST**, invisible to reconciliation because both sides of the
reconciliation check simply reflected whatever was actually charged, correct or not — only a check
against the merchant's own contracted rate catches it. And across a full 120-transaction batch's
journal export, **₹2,198.42 of GST-on-fee was automatically separated into its own ITC-eligible
ledger line**, ready for GSTR-2B filing, in 102 finalized, balanced double-entry journal entries
(the remaining 18 correctly held pending human review, not silently posted). See
[Fee leak detection](#fee-leak-detection-catching-what-reconciliation-cant-see) and
[ERP posting & ITC reclaim](#erp-posting--itc-reclaim) below for the real numbers behind each figure.

**At scale**: 50,000 transactions, ₹54,81,13,443.15 of total value, processed end-to-end —
matching, fee-leak review, and journal generation together — in **9.08 seconds** (5,508 tx/sec,
mock provider; raw output: [50k-batch-run-2026-08-25.json](docs/evidence/50k-batch-run-2026-08-25.json)).
85.0% resolved deterministically or via calibrated auto-resolve with zero LLM calls; the remaining
15% (7,500 transactions) went to the narrator and — since this specific run used the mock provider,
which this project's own calibration gate never lets auto-resolve regardless of accumulated
history — escalated honestly rather than silently riding on trust it hadn't itself earned. As
AI-agent-driven commerce increases transaction volume per merchant, this is the property that
matters: throughput that holds at scale without loosening what "earned trust" means.

## Where this fits in Razorpay's own stack

[Razorpay Recon](https://razorpay.com/newsroom/razorpay-pos-launches-industry-first-ai-powered-razorpay-recon-to-automate-reconciliation-for-businesses-boosting-financial-operations-efficiency-by-80/)
(launched December 2024) is real, AI-powered, rule-based batch matching across 200M+ transactions/month
— built for offline POS reconciliation at volume, not for narrating *why* one specific transaction
broke or auditing fee correctness per instrument.
[Settlement Insights](https://razorpay.com/blog/agent-studio-ai-agents-by-razorpay/) (launched as
part of Agent Studio, March 12, 2026) sends a daily WhatsApp settlement summary — genuinely useful,
and genuinely a different job: a summary of what happened, not a causal explanation of *why* a
specific transaction diverges or a fee correctness audit. Neither product classifies an exception's
root cause with a tool-call trace, tracks its own per-category accuracy before trusting itself to
auto-resolve, or separates GST-on-fee into an ITC-ready journal line. This system starts where
those stop — at the moment a settlement needs to become an audited, ERP-ready set of books, not
just a matched or summarized one. `POST /api/transactions/evaluate` is a plausible integration
point for output either product already produces.

One regulatory note, corrected here rather than glossed over: an earlier draft of this system's
fee-leak framing treated "any MDR on UPI/RuPay debit" as unconditionally illegal, citing the
zero-MDR mandate under [Section 10A of the Payment and Settlement Systems Act](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2114335&reg=48&lang=2)
(in force since January 2020). That mandate was **amended by Parliament on 4 August 2026** —
three weeks before this was written — replacing the blanket prohibition with a
government-notification framework under which specific modes can be selectively exempted. A
blanket legal claim would have gone stale the week this feature shipped. So the fee-leak detector
checks the actual fee against **this merchant's own contracted rate** instead (see below) — correct
regardless of how the notification framework evolves, which a hardcoded legal assumption never
could be.

One trajectory worth naming: Razorpay and NPCI already [launched agentic payments on Claude](https://razorpay.com/blog/agentic-payments-and-npci/)
in February 2026 — Zomato, Swiggy, and Zepto are live in pilot, letting an AI agent complete a food
or grocery order inside a conversation, no app switch, no manual payment entry. As that surface
grows, the reconciliation problem doesn't shrink, it multiplies: a human merchant generates one
settlement per cycle, an agent fleet generates however many the conversation volume demands, with
no person in the loop to notice an anomaly. The same calibrated-autonomy design — earn trust per
exception category before acting on it, escalate everything else with a stated reason — is the
right shape for that world regardless of who (or what) is on the other end of the transaction. This
system doesn't need to be rebuilt for agentic commerce; it needs to keep doing exactly what it
already does, at whatever volume shows up.

## Screenshots

Captured live in a real browser (Playwright, headless Chromium), against a mock-provider run —
zero console/network errors, nothing staged.

**Before a run — nothing hidden behind a login or a loading spinner:**
![Empty state](docs/screenshots/01-empty-state.png)

**After running a batch — match rate, ₹ auto-reconciled, and the naive-baseline lift, all real
numbers from that run:**
![Summary tiles and baseline comparison](docs/screenshots/02-summary-baseline.png)

**The calibration dial — drag it and every category's auto-resolve/escalate decision and ₹-at-risk
recompute instantly, no re-run:**
![Calibration dial](docs/screenshots/03-calibration-dial.png)

**An escalated case with its tool-call trace expanded — the actual `check_batch_anomalies` /
`check_sla_window` / `recall_similar_resolutions` calls and results that led to "needs a human,"
not a black-box verdict:**
![Escalation queue with tool-call trace](docs/screenshots/04-escalation-tool-trace.png)

**The guided tour, walking through escalate → resolve → recalibrate:**
![Guided tour](docs/screenshots/05-guided-tour.png)

**Fee leak analysis — ranked by ₹ impact, each finding expandable to a ready-to-send dispute
template:**
![Fee leak analysis](docs/screenshots/06-fee-leak-analysis.png)

**ERP journal export — real balanced double-entry lines, Tally/Zoho/generic formats, a live
preview before download:**
![ERP journal export](docs/screenshots/07-erp-export.png)

## Quick start

```bash
git clone https://github.com/tfthushaar/razorpay_buildathon.git && cd razorpay_buildathon

cd backend
python -m venv .venv && .venv/Scripts/activate   # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000 &

cd ../frontend
npm install && npm run dev
```

Open the printed local URL (typically `http://localhost:5173`), click **Run batch** — it finishes
instantly, zero cost, in the default mock mode (calls the exact same real tool functions the live
narrator does; only the final synthesis step is a fixed rule). Drag the calibration threshold slider
and resolve an escalation to see the live dial and the guided tour in action. Full setup (real LLM
providers, Docker, environment variables) is further down: [Full setup](#full-setup).

## Three real transactions this project has actually caught

Not invented examples — pulled directly from a real generated batch (`seed=42`), with the real
amounts and real reasoning:

- **A ₹49,823.00 UPI settlement landed on day 4, not the nominal day-1 SLA (or the 2-day tolerance
  line).** The ledger and settlement amounts matched exactly — nothing was actually missing — so
  the causal chain builder confirmed `ledger_gap = 0` and the deterministic Pass 2 auto-resolved it
  as `timing_lag` at 0.9 confidence, zero LLM calls needed. A naive matcher checking amount + date
  together would have flagged this as unreconciled the moment the date didn't line up, even though
  no money was ever actually missing.
- **Two unrelated transactions in the same settlement batch, one short by ₹150.00 and one over by
  exactly ₹150.00.** Summed together, the batch balances to the rupee — the classic "nets out at
  the aggregate level" trap a batch-total check would wave straight through. The causal chain model
  checks each transaction's own `ledger_gap` individually rather than trusting the aggregate, so
  both sides of the trap get caught, not just the batch-level number that happens to look clean.
- **A ₹153.74 refund was legitimately issued once — the refund registry shows exactly one
  `refund_id` — but the settlement feed deducted it from the payout twice.** The merchant's ledger
  (which only knows about the one real refund) and the actual settlement (net of two) differ by
  precisely one refund amount. `check_batch_anomalies` cross-references the refund registry itself
  rather than trusting the settlement total, and flags the double-deduction instead of quietly
  treating it as "a bigger refund than expected."

## Fee leak detection: catching what reconciliation can't see

A transaction can reconcile *perfectly* — ledger and settlement agree on every rupee — while still
being charged a fee inconsistent with the merchant's own contract. Standard reconciliation has no
way to see this: both sides of the check just reflect whatever was actually deducted, correctly or
not. Without an instrument-level audit against the rate card, a finance team has no signal at all
that anything is wrong. That's the real, documented blind spot `app/feeleak/detector.py` closes —
a genuinely separate axis of analysis from reconciliation, not a subset of it, run against its own
dedicated batch of otherwise-clean transactions.

Two patterns, both with real synthetic examples and real detection tests (`test_fee_leak.py`), not
just described:

| Pattern | What's actually wrong | How it's caught |
|---|---|---|
| **Blended-rate overcharge** | A flat/blended rate (e.g. the card rate) applied instead of the instrument's own contracted rate — most visible on UPI, whose contracted rate is furthest from a blended card rate | Actual fee compared against `amount × contracted_rate[instrument]`; any excess beyond a rounding epsilon is flagged, ranked by ₹ impact |
| **GST computed on the wrong base** | GST (18%) computed on the gross transaction amount instead of the gateway fee — the fee is what's actually taxed | Actual GST compared against `18% × actual_fee_charged`, isolated from the fee-amount check so the two error types are never conflated |

Real result from a 20-transaction review batch (reproducible: see
[Reproducing the results](#reproducing-the-results) below for the exact one-line command, or read
it straight off any `POST /api/run`'s `fee_leak_report` field): **10 blended-rate overcharges
totaling ₹2,634.50 in recoverable fees, and 10 GST-wrong-base findings totaling ₹23,158.96 in
miscalculated tax** — every finding also carries a ready-to-send dispute template naming the
transaction, the instrument, and the exact variance. Verified with zero false positives against
260 ordinary transactions from the main/stress batches (`test_zero_false_positives_against_every_existing_category`)
— a detector that flags correctly-charged transactions would be worse than useless.

This detector's pattern taxonomy is designed to extend to more leak types (refund-MDR retention,
chargeback-fee inflation, subscription-addon splitting, instrument reclassification) without a
different architecture — the same "compare actual against a known reference" check generalizes —
but only the two patterns above have real synthetic examples and tests behind them today. Framed
honestly as what's built, not what's designed-for; see [What it doesn't do yet](#what-it-doesnt-do-yet--honest-scope).

## ERP posting & ITC reclaim

A resolved transaction isn't useful to a finance team until it's a journal entry. `app/erp/journal.py`
turns every transaction's causal chain into double-entry lines — Revenue (credit, gross captured
amount), Bank Account (debit, actual settled amount), Payment Gateway Charges, Input Tax Credit
Receivable (GST-on-fee, always a *separate* line from the fee itself — the entire point, since
merging it makes ITC reclaim from GSTR-2B impossible to automate), Refunds, and a Reconciliation
Suspense line that absorbs whatever's genuinely unexplained.

That suspense line is the honest part: it's derived algebraically to be exactly zero — and omitted
entirely — for any transaction the pipeline has fully explained, and a real, visible, correctly-sized
number for anything it hasn't. **Every journal entry balances by construction, proven across all 8
transaction categories in `test_journal.py`, not just clean ones** — the failure mode this module
exists to avoid is a real accountant importing an entry that doesn't balance. A transaction still
sitting in the escalation queue posts with `finalized: false` and a "pending human review" note
instead of a silently-forced entry.

Real result from a full 120-transaction batch: **120 journal entries, all balanced, 102 finalized
and 18 correctly held pending human review** (matching the batch's own escalation count exactly),
with **₹2,198.42 of GST-on-fee automatically separated into the ITC Receivable ledger** across the
whole batch — a different, larger-scope number than the fee-leak review's own GST-correction figure
above, since this one covers every transaction's *correctly-computed* GST, not just the wrongly-computed
ones. Export in three real formats, all tested (`test_journal.py`):

- **Tally XML** — structure verified directly against [Tally's own published sample XML](https://help.tallysolutions.com/sample-xml/)
  before writing the exporter, including the (unusual, but confirmed correct) sign convention where
  a debit line carries a *negative* `AMOUNT` and a credit line a positive one.
- **Zoho Books CSV** and a **generic double-entry CSV** — a standard, defensible column shape, not
  independently verified against Zoho's current live import template the way Tally's structure was
  — disclosed honestly rather than presented with the same confidence.

TDS under [Section 393(1) of the Income-tax Act 2025](https://www.terra-insight.com/insights/section-393-tds-new-income-tax-act-reconciliation/)
(the recodified Section 194O, 0.1% on gross, effective 1 April 2026) is deliberately **not** applied
by default — it taxes an e-commerce *operator's* payouts to marketplace *participants*, not a direct
merchant's own gateway settlement, which is this project's actual scenario. `tds_note()` exists as
an opt-in, clearly-labeled informational helper for a merchant who is themselves an e-commerce
operator, never posted as a journal line automatically.

## Calibrated autonomy actually paying off — not just structurally possible in theory

This is the headline result, not a footnote: in the same real Ollama run linked above, `netting_trap`
became the **first category in this project's history to genuinely earn auto-resolve with real
evidence** — 36 real decisions across 15 distinct transactions, 100% accuracy, a 95% Wilson
confidence interval whose *lower bound* (90.4%) cleared the threshold. 8 of those decisions in the
same run show `decision: "auto_resolved_calibrated"` in the raw output, not just escalated — the
calibrated-autonomy pitch paying off end-to-end with a real model, not a hypothetical.

Meanwhile `genuine_error` sat at 82.9% measured accuracy in that same run and **stayed escalated
anyway** — by design, since it's the one category that never auto-resolves regardless of the
numbers, because "I genuinely can't explain this" is supposed to always reach a person, not be
smoothed over by a good aggregate score.

**Concrete evidence the reasoning behind this is genuine, not decorative tool-calling around a fixed
answer:** transaction `order_671da51349f1` has been narrated across both mock and real runs recorded
in this project's own audit log. Mock's answer is always confidence `0.3` for `genuine_error` (a
fixed constant in `narrate_mock`, which calls `recall_similar_resolutions` but never reads its
result) — so mock "matching" that tool's average confidence would be a tautology, not evidence of
anything. The real Ollama runs are different: across four independent real runs (confidence `0.533`,
`0.25`, `0.62`, and `0.427` — four different values, not a repeated constant), each one's final
confidence exactly equals what `recall_similar_resolutions` had just told it (`avg_confidence` from
that run's own prior resolutions) — checked directly against the live audit log, not asserted.
That's the model using one tool's numeric output to set a different, later decision — the actual
thing "agentic tool use" is supposed to mean, not just calling functions on the way to an answer it
would have given anyway.

A category earning trust once isn't treated as permanent, either: calibration also runs an
EWMA-based drift check (statistical process control — the same technique manufacturing lines use to
catch a process quietly drifting out of spec) on top of the Wilson interval, so a category whose
*recent* decisions start regressing gets pulled back to escalating even while its all-time aggregate
still looks fine. See [app/calibration/drift.py](backend/app/calibration/drift.py) and BUILD_LOG.md.

The escalation queue itself is not a "coming soon" placeholder — it's built to keep a human in the
loop, not replace one. Every escalated case shows the category, the confidence, the one-line
reasoning, and the full tool-call trace that led there — not a black-box verdict a finance team has
to take on faith. Categories that haven't earned statistical trust yet, and any transaction the
system genuinely cannot explain, land in the queue ranked by ₹ amount × ambiguity, so the
highest-value, least-certain case surfaces first — the one a person should actually look at before
the ₹200 rounding question nobody cares about.

## What makes this different from a flat matcher

1. **Causal chain matching, not row matching** — every transaction is modeled as
   `order → payment → fee → refund(s) → settlement`, and a mismatch is located at the specific hop
   where the number diverges, not just flagged as "doesn't match." *(A finance team gets "the fee
   deduction hasn't hit the ledger yet," not "these two numbers disagree, good luck" — the
   difference between a two-minute fix and a two-hour investigation.)*
2. **Calibrated autonomy** — the system tracks its own historical accuracy *per exception category*
   (with a Wilson confidence interval, not a raw percentage) against a hidden ground-truth key.
   Only categories that have earned trust above a threshold auto-resolve; everything else escalates.
   `genuine_error` never auto-resolves regardless of measured accuracy. *(This is what makes
   autonomy something a risk team can actually sign off on — trust is earned per category and
   revocable, not a blanket "the AI decides everything now.")*
3. **Agentic narrator** — unresolved transactions go through a tool-calling loop (fee schedule
   lookup, SLA window check, batch-anomaly cross-referencing, recall of similar past resolutions),
   not a one-shot classification. *(A judge — or an auditor — can see exactly what was checked
   before a verdict was reached, not just trust a confidence score.)*
4. **Trust that can be revoked, not just earned** — an EWMA drift check (statistical process
   control, borrowed from Six Sigma manufacturing lines) runs alongside the Wilson interval, so a
   category whose *recent* decisions start regressing gets pulled back to escalating even while its
   all-time aggregate still looks fine. *(A category doesn't quietly keep auto-resolving on
   yesterday's good record after it starts getting today's decisions wrong.)*
5. **Fails fast, not slow** — a circuit breaker (the same reliability pattern Netflix's Hystrix and
   the AWS SDK use) sits in front of both real LLM providers: after 3 consecutive real API/
   connectivity failures it stops attempting calls for a cooldown window instead of every remaining
   transaction in the queue independently re-discovering the same outage through a full
   retry-with-backoff cycle. *(A rate-limit storm or a downed provider degrades a batch's wall-clock
   time in seconds, not minutes — the difference between a demo that recovers gracefully and one
   that visibly hangs.)*
6. **Audits the fee, not just the reconciliation** — a fee-leak detector checks every fee actually
   charged against the merchant's own contracted rate, catching overcharges that reconcile
   perfectly cleanly and are invisible to every other check in this list. *(This is the difference
   between "your books match your bank statement" and "your bank statement is itself correct" —
   most reconciliation tools only ever answer the first question.)*
7. **Produces books, not just a verdict** — every resolved transaction becomes a balanced,
   ERP-ready journal entry with GST separated into its own ITC-eligible line, exportable to Tally,
   Zoho Books, or a generic CSV. *(The output isn't "here's what we found," it's "here's what's
   already in your books" — the actual deliverable a finance team needs, not an intermediate
   report they still have to act on by hand.)*

## How it's built

```
backend/     Python + FastAPI. Synthetic data gen, causal chain builder, matching engine,
             agentic narrator, calibration layer, audit logger, pipeline orchestrator, API.
frontend/    React 19 + TypeScript (Vite). Dashboard: run controls, match rate, baseline
             comparison, live calibration threshold dial, escalation queue, audit log.
docs/        Full architecture/design doc.
PROGRESS.md  What's built vs. outstanding, updated as the build progresses.
BUILD_LOG.md Chronological engineering journal — every bug found, root cause, fix, verification.
```

Full architecture rationale, data model, and the "why" behind every design choice:
[docs/track04-settlement-reconciliation-copilot.md](docs/track04-settlement-reconciliation-copilot.md).

## Full setup

Requires Python 3.11+ and Node 20.19+ (or 22.12+) — Vite 8's own minimum, not just a suggestion.

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate       # .venv\Scripts\activate on Windows cmd, source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

By default the narrator runs in **mock mode** — zero cost, deterministic, and it calls the exact
same real tool functions the live narrator does (only the final "turn tool results into a
category+confidence+reasoning" step is a fixed rule rather than an LLM call). Two real-provider
options exist. **Set either one by copying `backend/.env.example` to `backend/.env` and editing
it** (works identically on Windows/macOS/Linux — `main.py` calls `load_dotenv()` on startup, so
this is the recommended path regardless of shell):

**Recommended: `ollama` — a fully local model, zero cost, zero rate limit, zero external
dependency.**

```bash
winget install Ollama.Ollama       # or download from ollama.com — free, no account needed
ollama pull qwen2.5:7b-instruct    # ~4.7GB, one-time download
```

then set `LLM_PROVIDER=ollama` in `backend/.env` (or, on macOS/Linux only, `export LLM_PROVIDER=ollama`
in your shell — `export` is not valid PowerShell/cmd syntax on Windows, where this project was
actually built, so `.env` is the path that works everywhere).

This runs entirely on your own machine (GPU-accelerated automatically if one is available, falls
back to CPU otherwise) — no API key, no account, no rate limit, works fully offline. It's the
result of evaluating every free-tier API alternative (Groq, Cerebras, Gemini, DeepSeek, GLM,
SambaNova, OpenRouter, GitHub Models, Mistral — see BUILD_LOG.md for the full comparison) and
finding each one hit either a hard per-minute ceiling, a one-time credit that expires, or a daily
cap too small for a real batch. Real, verified result on this project's own hardware: a full batch
+ stress run (160 transactions, 55 narrated) in **~150 seconds**, 94%+ narrator accuracy — versus
11-70 *minutes* for the same workload against Groq's free tier.

A circuit breaker sits in front of both real providers (`app/narrator/circuit_breaker.py`): after 3
consecutive real API/connectivity failures it stops attempting calls for a cooldown window and
fails safe immediately, instead of every remaining transaction in the queue independently
re-discovering the same outage through a full retry-with-backoff cycle.

**Alternative: `groq` — a real tool-calling loop against a hosted API.**

Set `GROQ_API_KEY=your-key-here` (free tier at console.groq.com) and `LLM_PROVIDER=groq` in
`backend/.env`, the same way as above.

(or pass `"provider": "ollama"` / `"provider": "groq"` per-request in the `/api/run` body. I chose
Groq over the hosted LLM API I'd originally planned to use, for cost — see BUILD_LOG.md. Default model is
`openai/gpt-oss-20b`. Free-tier accounts have a real per-minute token limit; the narrator retries
rate limits with backoff automatically, but a full batch can still take many minutes. Kept as a
second option, not required.)

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the printed local URL (typically `http://localhost:5173`), click **Run batch**, then drag the
calibration threshold slider and try resolving an escalation to see the live dial and the
human-feedback loop in action.

### Deployment

```bash
docker compose up --build
```

Builds and runs both services: the backend on `:8000` (FastAPI/uvicorn, `LLM_PROVIDER=mock` by
default — see `docker-compose.yml` for switching to `groq`/`ollama`, including the one real gotcha:
Ollama running on the host machine isn't reachable from inside a container as `localhost`, so that
path needs `OLLAMA_HOST=http://host.docker.internal:11434` set explicitly) and the frontend on
`:5173`. SQLite state (audit log, calibration history) persists in a named volume across restarts.
**Actually built and run against a real Docker install (2026-08-25)**, not just reviewed: `docker
compose build` succeeds for both services, `docker compose up` starts them, `/api/health` responds,
and a real batch run through the Dockerized frontend against the Dockerized backend was driven live
in a browser (Playwright) with zero console errors — matching tiles, fee-leak analysis, calibration
table, and escalation queue all populated with real data, not a static page. This caught one real
bug before it shipped: the backend `CMD` hardcoded `--port 8000`, which would have silently broken
on any host (like Render) that injects its own `PORT` env var — fixed to read `${PORT:-8000}`.

This containerizes the current single-instance implementation as-is — it doesn't itself add
horizontal scaling, a message queue, or a real settlement-ledger webhook integration. Those are
real, identified next steps (worker-pool narration, Postgres instead of SQLite, an async job queue),
not built yet — see BUILD_LOG.md's Tier 2/3 architecture notes. The one integration point that
already exists today: `POST /api/transactions/evaluate` accepts an arbitrary transaction record and
runs it through the full pipeline — wiring a real settlement-ledger webhook to call that endpoint is
the remaining integration work, not a redesign.

**How far a single instance actually goes, from a real measured number, not a guess:** the real
Ollama run linked above processed 120 transactions end-to-end (matching + narration for the 18 that
needed it) in 55.8 measured seconds — **2.15 transactions/second** sustained, entirely on local,
free inference. Extrapolated (not itself measured at this volume) at that same rate run
continuously: **~185,000 transactions/day** on one instance, no GPU rental, no LLM API cost. That
number would only improve in a realistic production batch, since this demo's own mix sends 15% of
transactions to the narrator (`18/120`) — the Tier 1 sparse-batch benchmark in BUILD_LOG.md shows a
realistic settlement batch is closer to 1-3% needing narration, meaning proportionally far fewer
LLM calls and a correspondingly higher sustained rate.

**Frontend on Netlify** (`netlify.toml`, repo root): deploys `frontend/` as a static build —
`base = "frontend"`, `command = "npm run build"`, `publish = "dist"`. Set `VITE_API_BASE_URL` in
Netlify's site environment variables to point at wherever the backend actually runs (self-hosted,
or deployed separately — see the paragraph above for why the backend itself doesn't fit Netlify:
it's a stateful FastAPI service with SQLite persistence and narrator calls that can run minutes
against a real LLM provider, not a static site or a request/response serverless function). On the
backend host, set `ALLOWED_ORIGINS` to the deployed Netlify URL (comma-separated if there's more
than one, e.g. a preview + production domain) — CORS only allows `localhost` by default, so this is
required, not optional, for the deployed frontend to actually reach the deployed backend. Written
and reviewed, not yet deployed to a live Netlify site in this session.

**Frontend on Vercel** (`frontend/vercel.json`), the equivalent setup for Vercel instead of Netlify:
in the Vercel dashboard, set the project's Root Directory to `frontend` — `vercel.json` (relative to
that root) then supplies `buildCommand`, `outputDirectory`, the `vite` framework preset, and an
SPA rewrite. Same `VITE_API_BASE_URL` / `ALLOWED_ORIGINS` wiring as the Netlify path above, just on
Vercel's own dashboard instead.

**This is the actual live path for this submission**, not just a written-and-reviewed config: backend
on Render at `razorpay-buildathon-a1p0.onrender.com`, frontend on Vercel at
[razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app), `ALLOWED_ORIGINS`
on Render pointed at the Vercel URL, and a free UptimeRobot monitor pinging the backend's
`/api/health` every 5 minutes so Render's free-tier 15-minute idle sleep never kicks in — a judge
opening the link gets an instant response instead of a 30-60s cold start, and calibration state
(audit log, calibration history) stays intact across visits instead of resetting on every restart.
Verified live end to end, not just health-checked: a real batch run was driven through the actual
public URLs via Playwright (not local dev servers), zero console/network errors, every dashboard
section — tiles, fee-leak analysis, ERP export, calibration table, escalation queue — populated with
real data. Netlify itself didn't work out during setup (not diagnosed further, moved to Vercel
instead) — its config is kept in the repo as a still-valid alternative for anyone else running this.
See BUILD_LOG.md for the full deployment trail, including a hardcoded backend port that would have
silently broken on Render and was caught and fixed before it shipped.

## Tests & evidence

### Tests

```bash
cd backend
python -m pytest tests/ -v
```

137 tests covering the fee-leak detector (both patterns caught with hand-verified rupee amounts,
zero false positives against 260 ordinary transactions from the main/stress batches), the ERP
journal generator (every entry balances by construction across all 8 transaction categories, not
just clean ones, plus a well-formed-XML check on the Tally export and column checks on the CSV
exports), the data generator's arithmetic invariants (including that every requested batch
size 0-150 produces exactly that many transactions at both the default and non-default clean ratios,
not off-by-one on a rounding edge case, and that a large-scale/realistically-sparse batch — e.g.
50,000 records at 97% clean — produces exactly the requested proportions), the matching engine's
deterministic resolution paths, the narrator's tool-based detection, response-schema validation (an
out-of-set category, a malformed/wrongly-shaped final answer, out-of-range confidence, an unusable
tool call, plus an orchestration-level backstop for whatever the next unforeseen failure shape turns
out to be — see BUILD_LOG.md), a circuit breaker that trips after repeated real provider failures
and skips the call entirely while open (verified it never trips on a mere malformed answer, only on
a real API/connectivity failure), a real, finite request timeout on both real providers (verified
directly that Ollama's own client silently defaults to *no* timeout at all, unlike a bare
`httpx.Client()`), retry/failure handling (Groq-specific and provider-agnostic), the calibration
layer's statistical behavior (including that mock-mode decisions can never earn auto-resolve, that a
category's earned trust can't be spent by a different decision that never itself earned it, that a
concurrent history reset can never make a request's own just-added decisions vanish from its own
report, that repeatedly re-scoring the same small set of deterministic cases can never satisfy the
auto-resolve gate on its own, and that an EWMA drift check catches a category's recent decisions
regressing even while its all-time aggregate still clears the bar — see BUILD_LOG.md), the
Merkle-tree divergence pre-filter — including a live-pipeline integration test proving it produces
byte-identical results to the unfiltered path across 4 seeds and 3 clean ratios, and an honest
50,000-record benchmark that found it's a net wall-clock regression in this project's own in-memory
implementation, so it's kept as a tested, documented capability rather than wired into the default
path — see BUILD_LOG.md), the full pipeline, and the API layer (including that both live-input
endpoints reject malformed input cleanly instead of crashing, an out-of-range threshold can't force
the calibration gate open, 8 genuinely concurrent batch runs against the shared SQLite-backed state
all succeed, 5 concurrent resolves of the same escalation count exactly once instead of racing, and
— with an amplified thread-switch interval, the technique used to actually find this — 16 concurrent
batch runs never desync a run's escalations from its own ground truth — see BUILD_LOG.md), and the
Razorpay Test Mode connector (real response shapes captured by hand against the live account first,
including a real bug — the API returns `notes` as `[]`, not `{}`, when none are set — caught by
actually running it, not by guessing from docs).

### Reproducing the results

Every number in this README is real and independently checkable — nothing external, nothing that
only exists as a claim:

- **Calibration history**: `backend/data/calibration_history.db` (SQLite — `sqlite3 backend/data/calibration_history.db "select * from scored_decisions limit 5;"`
  reads it directly). Every scored decision: transaction id, predicted category, true label,
  amount, and which provider produced it (mock decisions are recorded for transparency but never
  count toward auto-resolve).
- **Audit log**: `backend/data/audit_log.db` — every decision made by a real run, with the full
  tool-call trace and reasoning behind it, linked back to the source order/payment/settlement/
  ledger rows.
- **Raw run output**: [docs/evidence/](docs/evidence/) has the complete JSON dumps this README
  quotes from — [real-ollama-run-2026-08-24.json](docs/evidence/real-ollama-run-2026-08-24.json)
  (the netting-trap auto-resolve and the ₹ figures above), plus two independent Groq runs
  ([run 1](docs/evidence/real-groq-run-2026-08-24.json),
  [run 2](docs/evidence/real-groq-run-2026-08-24b-persisted.json)).
- **To verify the calibration numbers yourself, recomputed live from the committed database** —
  not read off the dashboard, not retyped by hand:
  ```bash
  cd backend
  python scripts/audit_calibration.py
  ```
  This calls the exact same `app.calibration.calibrator.calibrate()` function the live app calls,
  over the real rows in `data/calibration_history.db`, and prints accuracy/95%-CI/EWMA/decision per
  category plus the ₹-at-risk total — the same netting-trap auto-resolve and genuine_error
  escalation described above, reproduced independently, not asserted.
- **To verify the fee-leak and ITC figures yourself**, from the real generator and detector, not
  retyped from a screenshot:
  ```bash
  cd backend
  python -c "from app.pipeline import run_batch; r = run_batch(seed=42, main_n=120, stress_n=40, provider='mock'); print(f'{len(r.fee_leak_report.findings)} findings, Rs.{r.fee_leak_report.total_fee_recovery/100:,.2f} fee recovery, Rs.{r.fee_leak_report.total_gst_correction/100:,.2f} GST correction, Rs.{r.total_itc_separated/100:,.2f} ITC separated')"
  ```
  Or via the API directly: `POST /api/run`, then inspect the `fee_leak_report` and
  `total_itc_separated` fields of the response, or `GET /api/journal/export?format=generic` for the
  full balanced journal.

**Groq real-run detail** (a second real provider, run against the live API twice on two different
random batches, with results genuinely persisted into the same `CalibrationHistory`/audit log the
live dashboard reads from — not just a side file). Both raw files predate the distinct-transaction-
count gate added later the same day, so their `calibration.categories[].reason` strings won't
include the "across N distinct transactions" phrasing current code always adds — the underlying
accuracy numbers are unaffected; re-running against a hosted API costs real quota, so these weren't
regenerated just for the string format (see BUILD_LOG.md). **Run 1** (n=120 + full
100%-adversarial stress batch, n=40): 100% narrator accuracy across all three categories (17/18 via
genuine tool-informed reasoning, 1/18 via a safe "did not converge" fallback that happened to match
ground truth), 37/37 correctly handled on the stress batch, 0 wrongly auto-resolved. **Run 2**
(different seed): 4/4 and 7/7 on two categories, 6/7 on the third — the one miss was a real API
hiccup (an empty response, correctly caught and routed through the same fail-safe path) that
happened to guess wrong this time; the fail-safe's *design* held regardless, since it always
defaults to the one category that can never auto-resolve, so a real narrator failure produced a
wrong classification but never a wrong autonomous action. Stress batch: 34/34 handled, 0 wrongly
auto-resolved. Both real Groq runs took 11-70 minutes of wall-clock time, almost entirely
rate-limit backoff, not model inference — the reason Ollama is now the recommended default.

An earlier Ollama run had a real, more interesting failure my audit loop caught live: the model
returned `timing_lag` — a category outside the 3 the narrator is allowed to output — at confidence
0.9, and nothing downstream of the JSON parse checked the category against the valid set before
letting it through. Fixed: both `narrate_groq` and `narrate_ollama` now validate the category and
route an out-of-schema result through the same fail-safe as malformed JSON. Full incident, fix, and
the DB cleanup that followed in BUILD_LOG.md.

A genuine concurrent-dispatch attempt (running narration calls in parallel) was also tried and
measured: it delivered no speedup (Ollama serializes on its single GPU-resident model regardless of
client-side concurrency) and introduced a real, if modest, accuracy cost from making the
`recall_similar_resolutions` tool's "prior resolutions so far" answer order-dependent — reverted
after measuring both effects rather than kept on the assumption that concurrency must help. Full
narrative, including the provider-comparison research that led here, in BUILD_LOG.md.

### Stress-test: what "100%-adversarial" actually means

Beyond the main batch, every run also generates a second batch that is nothing but traps — no
clean transactions at all — so the headline stress-test stat can't be cherry-picked from a mixed
batch. It's built only from the categories designed to fool a naive amount-check
(`duplicate_refund`, `netting_trap`, `fee_deduction`, `genuine_error`). Real result on this
project's own real (non-mock) run: **37/37 correctly handled, 0 wrongly auto-resolved**
([raw output](docs/evidence/real-ollama-run-2026-08-24.json)).

| Case | What's actually wrong | What a naive amount+date matcher does | What this system does |
|---|---|---|---|
| **Timing lag** | Amounts match exactly; settlement just arrived late (e.g. day 4 against a 1-day nominal, 2-day tolerance for UPI) | Silently calls it clean — no SLA awareness at all (proven in `test_naive_baseline_silently_misses_timing_lag`) | Causal chain confirms `ledger_gap = 0`, checks the SLA window, auto-resolves as `timing_lag` — money was never actually missing |
| **Currency rounding** | A few paise of harmless FX rounding drift, no real gap | Flags it as a mismatch requiring manual review — zero tolerance (proven in `test_naive_baseline_false_positives_on_rounding_noise`) | Recognizes the delta is within a rounding epsilon, resolves it deterministically, never escalates a non-problem |
| **Netting trap** | Two unrelated transactions in the same batch, one short and one over by the exact same amount | A batch-total check nets them to zero and calls the whole batch clean | Checks each transaction's own `ledger_gap` individually — both sides of the trap get caught |
| **Duplicate refund** | A refund legitimately issued once, deducted from the settlement twice | Sees a bigger-than-expected gap and either guesses "a refund happened" or escalates with no explanation | `check_batch_anomalies` cross-references the refund registry itself, confirms exactly one `refund_id`, flags the double-deduction specifically |
| **Genuine error** | An unexplained gap that doesn't fit any known pattern — by construction, deliberately ambiguous | Either guesses or escalates everything without distinguishing this from the cases above | Escalates — and this is the one category that **never** auto-resolves regardless of measured accuracy, because "I can't explain this" should always reach a person |

## What it doesn't do yet — honest scope

- **Not horizontally scaled** — a single FastAPI instance, SQLite for state. Real, identified next
  steps (worker-pool narration, Postgres, an async job queue) are deliberately deferred to a
  post-submission production decision, not built — see BUILD_LOG.md's Tier 2/3 notes.
- **No real settlement-ledger webhook integration** — `POST /api/transactions/evaluate` is the
  integration point a real webhook would call; wiring an actual webhook consumer isn't built. The
  Docker setup itself is written and reviewed but not run against a real Docker install in this dev
  environment, and not verified against a real cluster/orchestrator either.
- **`recall_similar_resolutions` is per-run only** — it doesn't persist across runs (a lightweight,
  disclosed limitation, not a hidden one; see docs/track04-*.md).
- **The Merkle-tree pre-filter exists and is tested, but isn't wired into the default pipeline** —
  measured honestly (not assumed) to be a net wall-clock regression at this project's own scale;
  kept as a documented capability for the case it actually helps (ledger and settlement data living
  in separate services), not shipped as a false performance win. See BUILD_LOG.md.
- **No committed Playwright test spec** for the frontend (manual + scripted live-browser
  verification exists throughout BUILD_LOG.md, but not as a checked-in, re-runnable suite).
- **The Tally XML export's structure was verified against Tally's own published sample docs, not a
  real Tally install** (no license available in this dev environment) — well-formed and structurally
  correct per the documented format, but not confirmed to import cleanly into live TallyPrime. The
  Zoho Books CSV column shape is a defensible standard, not independently verified against Zoho's
  current live import template.
- **The fee-leak detector ships two patterns with real synthetic examples and tests** (blended-rate
  overcharge, GST-wrong-base) — the pattern taxonomy is designed to extend to others (refund-MDR
  retention, chargeback-fee inflation, subscription-addon splitting, instrument reclassification)
  without a different architecture, but only these two are actually built and tested today.
- **The pitch video isn't recorded yet** — everything above is real and reproducible today; the
  5-minute walkthrough itself is the one remaining submission artifact.
- **The Razorpay Test Mode connector (`app/connectors/razorpay_sandbox.py`) makes real, live API
  calls with real test credentials** — `POST /v1/orders`, `GET /v1/payments`, `GET /v1/settlements`
  all confirmed working against the actual account (`GET /api/sandbox/status` proves it end to end).
  What it does *not* have is a captured payment to reconcile: there is no Razorpay API that manufactures
  one directly in test mode, and this account's Checkout activation profile rejects both documented
  domestic test cards (Visa `4111 1111 1111 1111`, Mastercard `5104 0155 5555 5558`) as
  `international_transaction_not_allowed` and doesn't offer UPI as a payment method at all — so the
  connector's `fetch_payments`/`fetch_settlements` currently return real, empty (or failed-only)
  collections rather than fabricated data. This is a genuine finding about the account, not a bug in
  the connector; the rest of the project runs on the synthetic data generator (`app/data_gen/`) for
  exactly this reason — reconciliation needs volume and labeled edge cases a fresh, unverified test
  account can't provide. One more disclosed gap in the same spirit: real `/v1/settlements` items
  carry no `payment_id` or rail/method field at all (verified against Razorpay's own docs) — real
  payment-level linkage needs the recon endpoint instead
  (`GET /v1/settlements/recon/combined?year=&month=&day=`), not built here, so `fetch_settlements`
  sets those two required-but-unavailable fields to an explicit placeholder rather than guessing —
  an earlier draft guessed `"upi"` from a field that doesn't exist in the real response, caught by
  a self-review audit round and fixed before push, not shipped.

## What I'd build next, inside Razorpay

A rough sense of where this goes past a Buildathon submission, not a commitment:

**Near-term** — wire `POST /api/transactions/evaluate` to Razorpay Recon's own escalation output,
so merchants already using Recon get causal-chain narration and fee-leak review automatically for
whatever Recon itself can't resolve, without a new merchant-facing surface to build or adopt.

**Medium-term** — per-merchant contract ingestion: a merchant uploads their actual negotiated rate
card once, and the fee-leak detector calibrates to it instead of a generic default, generating
dispute language that cites the merchant's own contract clause rather than a general pattern.

**Longer-term** — calibration as a shared signal, not just a per-merchant one: exception patterns
that consistently auto-resolve across many merchants (a netting-trap shape that recurs across a
whole industry vertical, say) are a real candidate for becoming new deterministic rules upstream,
in Recon itself — the same "earn trust with evidence before acting on it" discipline this project
already applies per-category, applied one level up.
