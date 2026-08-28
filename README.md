# Settlement Reconciliation Copilot

**Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller**

**Live**: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)

Every settlement Razorpay sends a merchant is a black box — one bank credit standing in for hundreds
of transactions, net of fees, GST, and refund offsets. Turning that into books a finance team can
close on is normally a manual, multi-hour job every cycle. This system explodes the credit back into
its transactions, narrates *exactly which hop* broke in each one's causal chain, and only
auto-resolves what it's statistically earned trust on — escalating the rest with a stated reason
instead of guessing.

```bash
git clone https://github.com/tfthushaar/razorpay_buildathon.git && cd razorpay_buildathon/backend
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt   # Windows: .venv\Scripts\activate
python -m uvicorn app.main:app --reload --port 8000   # then: cd ../frontend && npm install && npm run dev
```

![Escalation queue with tool-call trace expanded](docs/screenshots/04-escalation-tool-trace.png)
*A real escalated case — the exact `check_batch_anomalies` / `check_sla_window` /
`recall_similar_resolutions` tool calls and results behind "needs a human," not a black-box verdict.*

## Meeting the bar

| The bar (Razorpay's own) | This system | Verify |
|---|---|---|
| 50+ record batch | 50,000 (mock provider) / 120 (real Ollama) | [50k run](docs/evidence/50k-batch-run-2026-08-25.json) |
| Match rate | 99.3% of settlement value reconciled (real Ollama run, 7 escalations of 120) — the default mock-provider run reconciles ~86% instead, because mock deliberately escalates all three LLM-judgment categories rather than auto-resolving on unearned trust; see [below](#the-result) | [below](#the-result) |
| Throughput | 5,508 tx/sec (mock, 50k scale) — 2.58 tx/sec (real LLM, measured, not extrapolated). The 2,000× gap is the deterministic/LLM split below, not two different systems | [docs/setup.md](docs/setup.md) |
| Measured accuracy | Wilson 95% CI *lower bound* per category, not a raw point estimate | [below](#the-result) |
| Honest exception list | Every escalation ships a reason + tool trace; full build gaps in [What this can't do](#what-this-cant-do-and-what-it-refuses-to-do) | ↓ |
| Real Razorpay data | Order + payment + fee + refund are real API objects on a live test account; settlement is structurally unavailable in test mode. The fee-rate discrepancy this uncovered is below | [raw API dump](docs/evidence/razorpay-sandbox-2026-08-25.json) |

> **The one thing here a Razorpay engineer doesn't already know**: the real sandbox payment's raw
> fee (`fee: 1180, tax: 180` on a `50000`-paise capture) implies a 2.0% rate — matching this
> project's own `card` fee constant, not `netbanking`, the rail the payment actually used — left in
> the README as found, not smoothed over.

## Track 04 listed four example directions. All four are built.

| Direction | Built as | Evidence |
|---|---|---|
| Multi-source reconciliation | The primary identity above — causal chain across 5 hops, calibrated auto-resolve | [Why it's not a flat matcher](#why-its-not-a-flat-matcher) |
| Tax-line matcher | GST-wrong-base detection, ITC separation, and a match against a simulated GSTR-2B | `GET /api/gstr2b` |
| Settlement Q&A agent | A second agentic loop — free-text questions, real tool calls, trace shown | `POST /api/qa/ask` |
| Forward cash forecaster | Predicts settlement date + net amount from the merchant's own fee/SLA schedule — 9.1% MAPE, 93.3% interval coverage at this platform's default batch size, [caveats below](#what-this-cant-do-and-what-it-refuses-to-do) | `GET /api/forecast/backtest` |

They share one substrate: once the causal chain and the real rate card exist, the forecaster is
that chain run forward, and the Q&A agent is the same tool loop pointed at a free-text question
instead of one transaction. Breadth here cost days, not weeks — depth on the primary direction
didn't suffer for it. Caveats on the forecaster: [below](#what-this-cant-do-and-what-it-refuses-to-do).

## The result

After 8 real Ollama batches, accumulated over time — not one lucky run — `netting_trap` earned
auto-resolve: 59 distinct real cases, 98.3% measured accuracy, a 95% Wilson confidence interval whose
*lower bound* (91.0%) cleared the 90% trust threshold. That's real, distinct money: **₹4,86,473.13
auto-resolved with zero human review**, not the same handful of transactions counted once per
re-scoring (`duplicate_refund` earned the same status separately: 37 cases, 100% accuracy). Getting
here took real setbacks — 100% accuracy at 29 distinct cases still didn't clear the bound.

Autonomy is watched, not just earned. A controlled experiment seeds a category into auto-resolve
with 40 clean decisions, then feeds it wrong ones one at a time (`POST /api/drift/drill` — the
result is category-independent by construction, a test of the drift mechanism in isolation, not a
per-category comparison): **1 wrong decision (₹500) revoked it, even with the all-time aggregate
still reading 97.6% correct.** That's a deliberate choice, not an accident — for autonomous action on
real money, over-revoking costs one human review; under-revoking costs real rupees. The control
limit is a tunable parameter, and this project ships it tuned toward the expensive-to-get-wrong side.
Replaying the real accumulated history chronologically (`GET /api/regret` — realized cost, not
`amount_at_risk`'s forward-looking estimate) shows **₹0 in realized regret across 8 real
auto-resolved transactions so far** — small, still early.

The counterweight is the actual point. `genuine_error` sat at 80.3% measured accuracy across the same
evidence and **stayed escalated anyway** — it's the one category that never auto-resolves regardless
of the numbers, because a misclassification there should cost a human a glance, never become a wrong
autonomous action. It doesn't just give up, either: one more real model call proposes a named,
evidence-grounded hypothesis for what the case might actually be, never auto-adopted, shown as
"unreviewed" for a human to confirm ([raw evidence](docs/evidence/discovery-ollama-run-2026-08-27.json)).
A system willing to *not* act is the only reason a finance team would ever let it act.
[Raw output](docs/evidence/verified-ollama-run-2026-08-25.json) — reproducible on a fresh clone, not
just re-verified against local state.

## Why it's not a flat matcher

1. **Causal chain, not row matching.** Every transaction is `order → payment → fee → refund(s) →
   settlement`; a mismatch is located at the specific hop that diverges, not flagged as "these two
   numbers disagree." A finance team gets "the fee deduction hasn't posted yet," not a two-hour
   investigation.
2. **Autonomy that's earned and revocable.** Per-category accuracy is tracked against a Wilson lower
   bound, not a raw percentage, and an EWMA drift check pulls a category back to escalating the
   moment its *recent* decisions regress — even while its all-time average still looks fine. Trust
   is never a one-time unlock.
3. **Audits the fee, not just the reconciliation.** A transaction can reconcile perfectly — ledger
   and settlement agree on every rupee — while still being charged a fee inconsistent with the
   merchant's own contract. That's invisible to every check above; only comparing against the actual
   contracted rate catches it — one of the two building blocks behind the tax-line matcher in the
   table above. Detail: [docs/track04-*.md §9](docs/track04-settlement-reconciliation-copilot.md#9-beyond-the-original-spec-fee-leak-detection-and-erp-posting-added-post-build).

**Where the LLM actually sits, stated plainly:** 85% of a batch resolves deterministically, zero LLM
calls — that's the design, not a shortfall. The model is reserved for the three exception categories
where real judgment is required (`duplicate_refund`, `netting_trap`, `genuine_error`), and every one
of its calls is tool-grounded — it looks up the fee schedule, checks the SLA window, cross-references
past resolutions — and audited, not a bare classification. Autonomy in bullet 2 above is earned by
*that* judgment, specifically, not by the deterministic 85%.

## What this can't do, and what it refuses to do

- **Not horizontally scaled** — one FastAPI instance, SQLite. Worker-pool narration and Postgres are
  identified next steps, deliberately deferred, not built.
- **No real settlement-ledger webhook** — `POST /api/transactions/evaluate` is the integration point
  a webhook would call; the consumer itself isn't wired.
- **`recall_similar_resolutions` is per-run only** — doesn't persist across runs. Disclosed, not
  hidden.
- **The Tally XML export is verified against Tally's own published sample docs, not a live
  TallyPrime install** (no license available). Structurally correct per spec, not confirmed to
  import cleanly.
- **The fee-leak detector ships two patterns** (blended-rate overcharge, GST-wrong-base), not five —
  the taxonomy extends without a different architecture, but only these two are built and tested.
- **Four of five causal-chain hops are real Razorpay API objects** (order, captured payment, fee/tax,
  refund); the fifth, settlement, is structurally excluded from test mode on any account, confirmed
  against Razorpay's own docs, not an account limitation or something more real data would fix. The
  synthetic generator covers the settlement leg alone, for exactly this reason — full trail:
  [BUILD_LOG.md](BUILD_LOG.md).
- **The forecaster is exact by construction on ~73% of transactions** — it reuses the merchant's own
  known fee/SLA schedule, not a learned model, so its MAPE/coverage come entirely from the ~27% of
  transactions with a refund, dispute, or timing anomaly it structurally can't see in advance. The
  reported figure also moves with batch size (n=30: 9.1% MAPE / 93.3% coverage; n=120: 8.6%/90.8%;
  n=160: 4.1%/90.6%) since the anomaly categories are a roughly fixed share, not a fixed count — n=30
  has the best coverage of the three and the worst MAPE, so no single size flatters both metrics at
  once; the headline uses this dashboard's own default batch size (30), not a cherry-picked one. The
  backend's own API default is 120 — a bare `curl` to the endpoint without a prior UI run reads
  8.6%/90.8% instead, which is a different default, not an inconsistency.
- **Category discovery proposes a hypothesis per case, it doesn't cluster.** Eight live proposals
  produced six distinct names, five of them singletons — a genuine taxonomy would recur across
  cases; this doesn't yet.

## Verify it yourself

Three commands, all working on a genuinely fresh clone:

```bash
cd backend && python -m pytest tests/ -v                                          # 196 tests
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db  # the netting_trap/duplicate_refund result above, recomputed live
python -c "from app.pipeline import run_batch; r = run_batch(seed=42, main_n=120, stress_n=40, provider='mock'); print(r.fee_leak_report.total_fee_recovery, r.total_itc_separated)"  # fee-leak + ITC figures
```

Most submissions can't be independently verified at all — a block that survives `git clone` and
actually runs is meant as a differentiator by itself, not a formality. Full reproduction notes,
including exactly why `backend/data/*.db` is gitignored and what to use instead: see the comments in
[`scripts/audit_calibration.py`](backend/scripts/audit_calibration.py).

## Further reading

- [Architecture & design rationale](docs/track04-settlement-reconciliation-copilot.md) — full data
  model, system design, and every build decision's "why."
- [Where this fits in Razorpay's own stack](docs/positioning.md) — why this doesn't duplicate Recon
  or Settlement Insights, plus the NPCI agentic-commerce pilot and the regulatory correction behind
  the fee-leak detector's design.
- [BUILD_LOG.md](BUILD_LOG.md) — ~39,000 words, every real bug found and how it was fixed, in
  chronological order. Long because it's a process record, not a pitch document.
- [Screenshot gallery](docs/screenshots.md) · [Full setup, Docker, deployment](docs/setup.md) ·
  [Raw evidence JSONs](docs/evidence/)

**What I'd build next, inside Razorpay:** wire `POST /api/transactions/evaluate` to Recon's own
escalation output near-term; per-merchant contract ingestion so the fee-leak detector calibrates to a
merchant's actual rate card, medium-term; calibration as a cross-merchant signal — patterns that
consistently auto-resolve becoming candidate rules upstream in Recon itself — longer-term. Detail in
[docs/positioning.md](docs/positioning.md).
