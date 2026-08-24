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
frontend/    React 19 + TypeScript (Vite). Dashboard: run controls, match rate, baseline
             comparison, live calibration threshold dial, escalation queue, audit log.
docs/        Full architecture/design doc.
PROGRESS.md  What's built vs. outstanding, updated as the build progresses.
BUILD_LOG.md Chronological engineering journal — every bug found, root cause, fix, verification.
```

## Setup

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
options exist:

**Recommended: `ollama` — a fully local model, zero cost, zero rate limit, zero external
dependency.**

```bash
winget install Ollama.Ollama       # or download from ollama.com — free, no account needed
ollama pull qwen2.5:7b-instruct    # ~4.7GB, one-time download
export LLM_PROVIDER=ollama
```

This runs entirely on your own machine (GPU-accelerated automatically if one is available, falls
back to CPU otherwise) — no API key, no account, no rate limit, works fully offline. It's the
result of evaluating every free-tier API alternative (Groq, Cerebras, Gemini, DeepSeek, GLM,
SambaNova, OpenRouter, GitHub Models, Mistral — see BUILD_LOG.md for the full comparison) and
finding each one hit either a hard per-minute ceiling, a one-time credit that expires, or a daily
cap too small for a real batch. Real, verified result on this project's own hardware: a full batch
+ stress run (160 transactions, 55 narrated) in **~150 seconds**, 94%+ narrator accuracy — versus
11-70 *minutes* for the same workload against Groq's free tier.

**Alternative: `groq` — a real tool-calling loop against a hosted API.**

```bash
export GROQ_API_KEY=your-key-here   # free tier at console.groq.com
export LLM_PROVIDER=groq
```

(or pass `"provider": "ollama"` / `"provider": "groq"` per-request in the `/api/run` body. Groq was
chosen over the originally-planned Claude API for cost — see BUILD_LOG.md. Default model is
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

### Tests

```bash
cd backend
python -m pytest tests/ -v
```

76 tests covering the data generator's arithmetic invariants, the matching engine's deterministic
resolution paths, the narrator's tool-based detection, response-schema validation (an out-of-set
category, a malformed/wrongly-shaped final answer, out-of-range confidence, an unusable tool call,
plus an orchestration-level backstop for whatever the next unforeseen failure shape turns out to be
— see BUILD_LOG.md), a real, finite request timeout on both real providers (verified directly that
Ollama's own client silently defaults to *no* timeout at all, unlike a bare `httpx.Client()`), and
retry/failure handling (Groq-specific and provider-agnostic), the calibration layer's statistical
behavior (including that mock-mode decisions can never earn auto-resolve, and that a category's
earned trust can't be spent by a different decision that never itself earned it), the Merkle-tree
divergence pre-filter, the full pipeline, and the API layer (including that both live-input
endpoints reject malformed input cleanly instead of crashing, an out-of-range threshold can't force
the calibration gate open, 8 genuinely concurrent batch runs against the shared SQLite-backed state
all succeed, 5 concurrent resolves of the same escalation count exactly once instead of racing, and
— with an amplified thread-switch interval, the technique used to actually find this — 16 concurrent
batch runs never desync a run's escalations from its own ground truth — see BUILD_LOG.md).

## What's real vs. mock

Everything runs end-to-end with `LLM_PROVIDER=mock` (the default) — synthetic data generation,
causal chain matching, deterministic exception resolution, calibration, audit logging, and the
full dashboard are all live with zero external dependencies, and mock mode calls the exact same
real tool functions the live narrator does (only the final synthesis step is a fixed rule).

**Local (Ollama, qwen2.5:7b-instruct)** — the recommended real provider, run against the live
server and persisted into the same `CalibrationHistory`/audit log the dashboard reads from, same as
both Groq runs below. Raw output: [real run](docs/evidence/real-ollama-run-2026-08-24.json).
**94.4% narrator accuracy** (17/18 correct on the main narration queue, cross-checked directly
against ground truth, not just read off the dashboard), 50.75s for that queue (~2.8s/txn), 37/37
correctly handled on the stress batch, 0 wrongly auto-resolved. The one miss carried confidence
0.0 — a genuine safe fallback (predicted `genuine_error`, true label `netting_trap`).

An earlier Ollama run had a real, more interesting failure an external audit caught live: the model
returned `timing_lag` — a category outside the 3 the narrator is allowed to output — at **confidence
0.9**, and nothing downstream of the JSON parse checked the category against the valid set before
letting it through. **Fixed**: both `narrate_groq` and `narrate_ollama` now validate the category
and route an out-of-schema result through the same fail-safe as malformed JSON. The exact
transaction that hallucinated `timing_lag` before now resolves correctly (`genuine_error`,
confidence 1.0) in the linked evidence run — not proof the new check fired rather than the model
just answering right this time, but a clean demonstration the case is healthy either way, with a
code-level backstop now in place regardless. Full incident, fix, and the DB cleanup that followed
in BUILD_LOG.md.

A genuine concurrent-dispatch attempt (running narration calls in parallel) was also tried and
measured: it delivered **no speedup** (Ollama serializes on its single GPU-resident model
regardless of client-side concurrency) and introduced a real, if modest, accuracy cost from making
the `recall_similar_resolutions` tool's "prior resolutions so far" answer order-dependent —
reverted after measuring both effects rather than kept on the assumption that concurrency must
help. Full narrative, including the provider-comparison research that led here, in BUILD_LOG.md.

**Groq (openai/gpt-oss-20b)** — a second real option, run against the live API twice on two
different random batches, with results genuinely persisted into the same
`CalibrationHistory`/audit log the live dashboard reads from — not just a side file. **Run 1**
(n=120 + full 100%-adversarial stress batch, n=40): 100% narrator accuracy across all three
categories (17/18 via genuine tool-informed reasoning, 1/18 via a safe "did not converge" fallback
that happened to match ground truth), 37/37 correctly handled on the stress batch, 0 wrongly
auto-resolved. **Run 2** (different seed): 4/4 and 7/7 on two categories, 6/7 on the third — the
one miss was a real API hiccup (an empty response, correctly caught and routed through the same
fail-safe path) that happened to guess wrong this time; the fail-safe's *design* held regardless,
since it always defaults to the one category that can never auto-resolve, so a real narrator
failure produced a wrong classification but never a wrong autonomous action. Stress batch: 34/34
handled, 0 wrongly auto-resolved. But both real Groq runs took 11-70 minutes of wall-clock time,
almost entirely rate-limit backoff, not model inference — the reason Ollama is now the recommended
default. Raw output: [run 1](docs/evidence/real-groq-run-2026-08-24.json),
[run 2](docs/evidence/real-groq-run-2026-08-24b-persisted.json); full narrative in BUILD_LOG.md,
including the real rate-limit hits and how they're handled (retry with backoff, honoring the API's
own `retry-after` header, failing safe rather than crashing).

## Honest exceptions

The dashboard's escalation queue is not a "coming soon" placeholder — it's the point. Categories
that haven't earned statistical trust yet, and any transaction the system genuinely cannot
explain, show up there with the reasoning behind why it wasn't auto-resolved, ranked by ₹ amount
× ambiguity so the highest-value, least-certain cases surface first.
