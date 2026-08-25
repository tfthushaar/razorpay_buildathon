# Settlement Reconciliation Copilot

**Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller**

**Live**: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)

Every settlement Razorpay sends a merchant is a black box — one bank credit standing in for hundreds
of transactions, net of fees, GST, and refund offsets. Turning that into books a finance team can
close on is normally a manual, multi-hour job every cycle. This system explores the credit back into
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

## Scoreboard

| The bar (Razorpay's own) | This system | Verify |
|---|---|---|
| 50+ record batch | 50,000 (mock provider) / 120 (real Ollama) | [50k run](docs/evidence/50k-batch-run-2026-08-25.json) |
| Match rate | 99.3% of settlement value reconciled (real Ollama run, 7 escalations of 120) | [below](#the-result) |
| Throughput | 5,508 tx/sec (mock, 50k scale) — 2.58 tx/sec (real LLM, measured, not extrapolated). The 2,000× gap is the deterministic/LLM split below, not two different systems | [docs/setup.md](docs/setup.md) |
| Measured accuracy | Wilson 95% CI *lower bound* per category, not a raw point estimate | [below](#the-result) |
| Honest exception list | Every escalation ships a reason + tool trace; full build gaps in [What this can't do](#what-this-cant-do-and-what-it-refuses-to-do) | ↓ |
| Real Razorpay data | Order + payment + fee + refund are real API objects on a live test account (raw `fee: 1180, tax: 180` on a `50000`-paise payment — pre-tax base 1180/1.18 = 1000, i.e. 2.0% of the payment, matching this project's own `card` rate constant, not `netbanking` — a real, disclosed discrepancy); settlement is structurally unavailable in test mode, verified not assumed | [raw API dump](docs/evidence/razorpay-sandbox-2026-08-25.json) |

## The result

After 8 real, honestly-accumulated Ollama batches — not one lucky run — `netting_trap` earned
auto-resolve: 59 distinct real cases, 98.3% measured accuracy, a 95% Wilson confidence interval whose
*lower bound* (91.0%) cleared the 90% trust threshold. That's real, distinct money: **₹4,86,473.13
auto-resolved with zero human review**, not the same handful of transactions counted once per
re-scoring (`duplicate_refund` earned the same status separately: 37 cases, 100% accuracy). Getting
here took real setbacks — 100% accuracy at 29 distinct cases still didn't clear the bound, and a
couple of genuine misclassifications happened before enough further evidence pulled it past 90% for
good.

The counterweight is the actual point. `genuine_error` sat at 80.3% measured accuracy across the same
evidence and **stayed escalated anyway**, because it's the one category that never auto-resolves
regardless of the numbers — a misclassification there costs a human a glance, never a wrong
autonomous action, which is exactly why it's excluded from auto-resolve by design rather than a
category this project tried and failed to improve. A system willing to *not* act is the only reason a
finance team would ever let it act. [Raw output](docs/evidence/verified-ollama-run-2026-08-25.json) —
reproducible on a fresh clone, not just re-verified against local state.

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
   contracted rate catches it. Detail: [docs/track04-*.md §12](docs/track04-settlement-reconciliation-copilot.md#12-beyond-the-original-spec-fee-leak-detection-and-erp-posting-added-post-build).

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

## Verify it yourself

Three commands, all working on a genuinely fresh clone — not read off a dashboard, not retyped by
hand:

```bash
cd backend && python -m pytest tests/ -v                                          # 145 tests
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
- [BUILD_LOG.md](BUILD_LOG.md) — ~30,000 words, every real bug found and how it was fixed, in order.
  Long because it's a process record, not a pitch document.
- [Where this fits in Razorpay's own stack](docs/positioning.md) — Recon, Settlement Insights, the
  NPCI agentic-commerce pilot, and the regulatory correction behind the fee-leak detector's design.
- [Screenshot gallery](docs/screenshots.md) · [Full setup, Docker, deployment](docs/setup.md) ·
  [Raw evidence JSONs](docs/evidence/)

**What I'd build next, inside Razorpay:** wire `POST /api/transactions/evaluate` to Recon's own
escalation output near-term; per-merchant contract ingestion so the fee-leak detector calibrates to a
merchant's actual rate card, medium-term; calibration as a cross-merchant signal — patterns that
consistently auto-resolve becoming candidate rules upstream in Recon itself — longer-term. Detail in
[docs/positioning.md](docs/positioning.md).
