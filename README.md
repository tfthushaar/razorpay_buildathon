# Settlement Reconciliation Copilot

**Razorpay AI Buildathon 2026 — Track 04.** Reconciles merchant ledger data against Razorpay
settlement data, narrates which hop in a transaction's causal chain broke, and auto-resolves only
what it has measured itself accurate on.

**Live**: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)

![Escalation queue with tool-call trace expanded](docs/screenshots/04-escalation-tool-trace.png)
*A real escalated case — the `check_batch_anomalies` / `check_sla_window` /
`recall_similar_resolutions` tool calls and results behind it.*

## The result

| Metric | Result | Reproduce |
|---|---|---|
| Match rate | 99.3% of settlement value, real provider, 7 escalations of 120 | `cd backend && python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db` |
| Throughput | 2.58 tx/sec (real LLM, measured) — 5,508 tx/sec (mock, 50k scale) | [SETUP.md](docs/SETUP.md) |
| Per-category accuracy | `netting_trap` 98.3% (91.0% Wilson lower bound), `duplicate_refund` 100%, `genuine_error` 80.3% (never auto-resolves) | same command |
| Adversarial stress batch | 40/40 correctly handled, 0 wrongly auto-resolved | [raw output](docs/evidence/verified-ollama-run-2026-08-25.json) |
| Auto-resolved with zero human review | ₹4,86,473.13, 59 distinct real cases | same command |

Full numbers, every claim in this file: [RESULTS.md](docs/RESULTS.md).

## How it works

1. **Causal chain, not row matching.** Every transaction is `order → payment → fee → refund(s) →
   settlement`; a mismatch is located at the specific hop that diverges.
2. **Autonomy that's earned and revocable.** Per-category accuracy is tracked against a Wilson
   lower bound, and an EWMA drift check pulls a category back to escalating the moment its recent
   decisions regress, even while its all-time average still looks fine. A controlled drill found
   1 wrong decision was enough to revoke a category's trust, with the aggregate still at 97.6%.
3. **Audits the fee, not just the reconciliation.** A transaction can reconcile perfectly while
   still being charged a fee inconsistent with the merchant's contract — only comparing against the
   contracted rate catches it. Completed by a match against a simulated GSTR-2B.

A second agentic loop answers free-text questions over a batch; one more model call proposes a named
hypothesis instead of stopping at `genuine_error`; a forecaster predicts settlement date and net
amount before a payment settles. Detail: [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Where the LLM sits, including where it doesn't

85% of a batch resolves deterministically, zero LLM calls. The three categories the model is
reserved for aren't ambiguous to the tools that gather evidence for them: a 20-line rule with zero
LLM calls scores 100.0% across 519 real cases. The real narrator doesn't match that (98.3%/80.3%
above) — on this task, the rule is a strict upgrade over the LLM. The real-provider call earns
autonomy for reliability under conditions the rule never faces (API failures, malformed tool
arguments, a hallucinated id), not for resolving a case the rule genuinely couldn't.

For a case the rule provably can't resolve — a netting pattern across 3+ transactions, invisible to
a pairwise-only detector by construction — Groq solved 8/8 with a verification tool available; a
smaller local model solved 1/8. Full numbers and raw evidence: [RESULTS.md](docs/RESULTS.md).

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                 # 221 tests
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
python scripts/measure_mock_narrator_accuracy.py
```

All three work on a genuinely fresh clone. Full reproduction notes: [RESULTS.md](docs/RESULTS.md).

## What this can't do

- Not horizontally scaled — one FastAPI instance, SQLite.
- Settlement is structurally unavailable in Razorpay's test mode, on any account — 4 of 5
  causal-chain hops are real API objects; the fifth is synthetic for exactly this reason.
- The forecaster is exact by construction on ~73% of transactions (the merchant's own known fee/SLA
  schedule, not a learned model). A separate, genuinely-blind backtest against a hidden schedule
  drift shows why that matters: amount error stays under 0.2%, but date-window coverage swings
  3%–100% seed to seed.
- Category discovery clusters proposals within one run, not across separate runs.
- No real settlement-ledger webhook — `POST /api/transactions/evaluate` is the integration point one
  would call.

Full list: [LIMITATIONS.md](docs/LIMITATIONS.md).

## What broke

- A category's earned trust could be ridden by a mock-mode guess in the same category.
- The same unguarded-concurrent-write bug shape recurred five times, across the narrator and then
  the API's own run state.
- An external review caught a headline rupee figure inflated by re-scoring the same transactions
  across runs.
- The flagship multi-way netting experiment's own first result (Groq 8/8) turned out to have a
  leaked strategy and a trivially-satisfiable grader; corrected, Groq needed a verification tool to
  earn 8/8 again — honestly, this time.

Twelve incidents, fixed format: [WHAT_BROKE.md](docs/WHAT_BROKE.md).

## Get it running

```bash
git clone https://github.com/tfthushaar/razorpay_buildathon.git && cd razorpay_buildathon/backend
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt   # Windows: .venv\Scripts\activate
python -m uvicorn app.main:app --reload --port 8000   # then: cd ../frontend && npm install && npm run dev
```

Full setup, real LLM providers, Docker, deployment: [docs/SETUP.md](docs/SETUP.md).

## Further reading

[Architecture](docs/ARCHITECTURE.md) · [Results](docs/RESULTS.md) ·
[What broke](docs/WHAT_BROKE.md) · [Limitations](docs/LIMITATIONS.md) ·
[Where this fits in Razorpay's own stack](docs/positioning.md) ·
[Screenshots](docs/screenshots.md) · [Evidence](docs/evidence/)

[BUILD_LOG.md](BUILD_LOG.md) — raw chronological journal, ~41,600 words, kept as an appendix.
