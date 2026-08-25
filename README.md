# Settlement Reconciliation Copilot

**Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller**

Every settlement Razorpay sends a merchant is a black box — one bank credit standing in for hundreds
of transactions, net of fees, GST, and refund offsets. Turning that into books a finance team can
close on is normally a manual, multi-hour job every cycle. This system explodes the credit back into
its transactions, narrates *exactly which hop* broke in each one's causal chain, and only
auto-resolves what it's statistically earned trust on — escalating the rest with a stated reason
instead of guessing.

**Live**: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)

```bash
git clone https://github.com/tfthushaar/razorpay_buildathon.git && cd razorpay_buildathon/backend
python -m venv .venv && .venv/Scripts/activate && pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000   # then: cd ../frontend && npm install && npm run dev
```

![Escalation queue with tool-call trace expanded](docs/screenshots/04-escalation-tool-trace.png)
*A real escalated case — the exact `check_batch_anomalies` / `check_sla_window` /
`recall_similar_resolutions` tool calls and results behind "needs a human," not a black-box verdict.*

## Scoreboard

| The bar (Razorpay's own) | This system | Verify |
|---|---|---|
| 50+ record batch | 50,000 (mock provider) / 120 (real Ollama) | [50k run](docs/evidence/50k-batch-run-2026-08-25.json) |
| Throughput | 5,508 tx/sec (mock, 50k scale) — 2.58 tx/sec (real LLM, measured, not extrapolated) | [docs/setup.md](docs/setup.md) |
| Measured accuracy | Wilson 95% CI *lower bound* per category, not a raw point estimate | [below](#the-result) |
| Honest exception list | Every escalation ships a reason + tool trace; full build gaps in [What this can't do](#what-this-cant-do-and-what-it-refuses-to-do) | ↓ |

## The result

After 8 real, honestly-accumulated Ollama batches — not one lucky run — `netting_trap` earned
auto-resolve: 59 distinct real cases, 98.3% measured accuracy, a 95% Wilson confidence interval whose
*lower bound* (91.0%) cleared the 90% trust threshold. That's real, distinct money: **₹4,86,473.13
auto-resolved with zero human review**, not the same handful of transactions counted once per
re-scoring (`duplicate_refund` earned the same status separately: 37 cases, 100% accuracy). Getting
here took real setbacks — 100% accuracy at 29 distinct cases still didn't clear the bound, and a
couple of genuine misclassifications happened before enough further evidence pulled it past 90% for
good. The counterweight is the actual point: `genuine_error` sat at 80.3% measured accuracy across
the same evidence and **stayed escalated anyway**, because it's the one category that never
auto-resolves regardless of the numbers. A system willing to *not* act is the only reason a finance
team would ever let it act. [Raw output](docs/evidence/verified-ollama-run-2026-08-25.json) —
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
- **The Razorpay Test Mode connector makes real API calls but has no captured payment to
  reconcile** — this specific test account's Checkout profile rejects every documented domestic test
  card and doesn't offer UPI; a real account-level finding, not a connector bug. Full trail:
  [BUILD_LOG.md](BUILD_LOG.md).

## Verify it yourself

Three commands, all working on a genuinely fresh clone — not read off a dashboard, not retyped by
hand:

```bash
cd backend && python -m pytest tests/ -v                                          # 140 tests
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
