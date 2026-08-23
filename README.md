# Settlement Reconciliation Copilot

**Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller**

An agent that reconciles merchant ledger data against settlement data across UPI/card/netbanking,
narrates *exactly which hop* in the transaction's causal chain broke, and only auto-resolves the
exception categories it has statistically earned trust on — everything else escalates with a
stated reason. Full design rationale: [docs/track04-settlement-reconciliation-copilot.md](docs/track04-settlement-reconciliation-copilot.md).
Build history, including every bug found and how it was fixed: [BUILD_LOG.md](BUILD_LOG.md).

## What makes this different from a flat matcher

1. **Causal chain matching, not row matching** — every transaction is modeled as
   `order → payment → fee → refund(s) → settlement`, and a mismatch is located at the specific hop
   where the number diverges, not just flagged as "doesn't match."
2. **Calibrated autonomy** — the system tracks its own historical accuracy *per exception category*
   (with a Wilson confidence interval, not a raw percentage) against a hidden ground-truth key.
   Only categories that have earned trust above a threshold auto-resolve; everything else escalates.
   `genuine_error` never auto-resolves regardless of measured accuracy — escalation is the correct
   outcome for an admittedly-unexplained case, not a fallback for low confidence.
3. **Agentic narrator** — unresolved transactions go through a tool-calling loop (fee schedule
   lookup, SLA window check, batch-anomaly cross-referencing, recall of similar past resolutions),
   not a one-shot classification.

## Repo layout

```
backend/     Python + FastAPI. Synthetic data gen, causal chain builder, matching engine,
             agentic narrator, calibration layer, audit logger, pipeline orchestrator, API.
frontend/    React 18 + TypeScript (Vite). Dashboard: run controls, match rate, baseline
             comparison, live calibration threshold dial, escalation queue, audit log.
docs/        Full architecture/design doc.
PROGRESS.md  What's built vs. outstanding, updated as the build progresses.
BUILD_LOG.md Chronological engineering journal — every bug found, root cause, fix, verification.
```

## Setup

Requires Python 3.11+ and Node 18+.

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
category+confidence+reasoning" step is a fixed rule rather than an LLM call). To use the real
agentic narrator:

```bash
export GROQ_API_KEY=your-key-here   # free tier at console.groq.com
export LLM_PROVIDER=groq
```

(or pass `"provider": "groq"` per-request in the `/api/run` body — see BUILD_LOG.md for why Groq
was chosen over the originally-planned Claude API: it's free-tier, OpenAI-tool-call compatible,
and this build is optimized to minimize running cost. Default model is `openai/gpt-oss-20b` —
verified tool-calling support and the cheapest of the candidates tested. Free-tier accounts have a
real per-minute token limit; the narrator retries rate limits with backoff automatically.)

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the printed local URL (typically `http://localhost:5173`), click **Run batch**, then drag the
calibration threshold slider and try resolving an escalation to see the live dial and the
human-feedback loop in action.

### Tests

```bash
cd backend
python -m pytest tests/ -v
```

50 tests covering the data generator's arithmetic invariants, the matching engine's deterministic
resolution paths, the narrator's tool-based detection and retry/failure handling, the calibration
layer's statistical behavior (including that mock-mode decisions can never earn auto-resolve),
the Merkle-tree divergence pre-filter, the full pipeline, and the API layer.

## What's real vs. mock

Everything runs end-to-end with `LLM_PROVIDER=mock` (the default) — synthetic data generation,
causal chain matching, deterministic exception resolution, calibration, audit logging, and the
full dashboard are all live with zero external dependencies, and mock mode calls the exact same
real tool functions the live narrator does (only the final synthesis step is a fixed rule).

The agentic Groq-backed narrator has been run against the live API twice, on two different random
batches, with results genuinely persisted into the same `CalibrationHistory`/audit log the live
dashboard reads from — not just a side file. **Run 1** (n=120 + full 100%-adversarial stress batch,
n=40): 100% narrator accuracy across all three categories (17/18 via genuine tool-informed
reasoning, 1/18 via a safe "did not converge" fallback that happened to match ground truth), 37/37
correctly handled on the stress batch, 0 wrongly auto-resolved. **Run 2** (different seed): 4/4 and
7/7 on two categories, 6/7 on the third — the one miss was a real API hiccup (an empty response,
correctly caught and routed through the same fail-safe path) that happened to guess wrong this
time; the fail-safe's *design* held regardless, since it always defaults to the one category that
can never auto-resolve, so a real narrator failure produced a wrong classification but never a
wrong autonomous action. Stress batch: 34/34 handled, 0 wrongly auto-resolved. Two runs with one
honest miss are a more credible artifact than either run alone would be. Raw output:
[run 1](docs/evidence/real-groq-run-2026-08-24.json),
[run 2](docs/evidence/real-groq-run-2026-08-24b-persisted.json); full narrative in BUILD_LOG.md,
including the real rate-limit hits and how they're handled (retry with backoff, honoring the API's
own `retry-after` header, failing safe rather than crashing).

## Honest exceptions

The dashboard's escalation queue is not a "coming soon" placeholder — it's the point. Categories
that haven't earned statistical trust yet, and any transaction the system genuinely cannot
explain, show up there with the reasoning behind why it wasn't auto-resolved, ranked by ₹ amount
× ambiguity so the highest-value, least-certain cases surface first.
