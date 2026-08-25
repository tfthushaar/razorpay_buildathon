# Full setup

Requires Python 3.11+ and Node 20.19+ (or 22.12+) — Vite 8's own minimum, not just a suggestion. The
[README](../README.md) has a three-line quick start that gets a mock-provider run going in under a
minute; this is the full picture — real LLM providers, Docker, and how the live deployment is wired.

## Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate       # .venv\Scripts\activate on Windows cmd, source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

By default the narrator runs in **mock mode** — zero cost, deterministic, and it calls the exact same
real tool functions the live narrator does (only the final "turn tool results into a
category+confidence+reasoning" step is a fixed rule rather than an LLM call). Two real-provider
options exist. **Set either one by copying `backend/.env.example` to `backend/.env` and editing
it** (works identically on Windows/macOS/Linux — `main.py` calls `load_dotenv()` on startup, so this
is the recommended path regardless of shell):

### Recommended: `ollama` — a fully local model, zero cost, zero rate limit, zero external dependency

```bash
winget install Ollama.Ollama       # or download from ollama.com — free, no account needed
ollama pull qwen2.5:7b-instruct    # ~4.7GB, one-time download
```

then set `LLM_PROVIDER=ollama` in `backend/.env` (or, on macOS/Linux only, `export LLM_PROVIDER=ollama`
in your shell — `export` is not valid PowerShell/cmd syntax on Windows, where this project was
actually built, so `.env` is the path that works everywhere).

This runs entirely on your own machine (GPU-accelerated automatically if one is available, falls back
to CPU otherwise) — no API key, no account, no rate limit, works fully offline. It's the result of
evaluating every free-tier API alternative (Groq, Cerebras, Gemini, DeepSeek, GLM, SambaNova,
OpenRouter, GitHub Models, Mistral — see BUILD_LOG.md for the full comparison) and finding each one
hit either a hard per-minute ceiling, a one-time credit that expires, or a daily cap too small for a
real batch. Real, verified result on this project's own hardware: a full batch + stress run (160
transactions, 55 narrated) in **~150 seconds**, 94%+ narrator accuracy — versus 11-70 *minutes* for
the same workload against Groq's free tier.

A circuit breaker sits in front of both real providers (`app/narrator/circuit_breaker.py`): after 3
consecutive real API/connectivity failures it stops attempting calls for a cooldown window and fails
safe immediately, instead of every remaining transaction in the queue independently re-discovering
the same outage through a full retry-with-backoff cycle.

### Alternative: `groq` — a real tool-calling loop against a hosted API

Set `GROQ_API_KEY=your-key-here` (free tier at console.groq.com) and `LLM_PROVIDER=groq` in
`backend/.env`, the same way as above.

(or pass `"provider": "ollama"` / `"provider": "groq"` per-request in the `/api/run` body. Groq was
chosen over the hosted LLM API originally planned, for cost — see BUILD_LOG.md. Default model is
`openai/gpt-oss-20b`. Free-tier accounts have a real per-minute token limit; the narrator retries
rate limits with backoff automatically, but a full batch can still take many minutes. Kept as a
second option, not required.)

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the printed local URL (typically `http://localhost:5173`), click **Run batch**, then drag the
calibration threshold slider and try resolving an escalation to see the live dial and the
human-feedback loop in action.

## Deployment

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
table, and escalation queue all populated with real data, not a static page. This caught one real bug
before it shipped: the backend `CMD` hardcoded `--port 8000`, which would have silently broken on any
host (like Render) that injects its own `PORT` env var — fixed to read `${PORT:-8000}`.

This containerizes the current single-instance implementation as-is — it doesn't itself add
horizontal scaling, a message queue, or a real settlement-ledger webhook integration. Those are real,
identified next steps (worker-pool narration, Postgres instead of SQLite, an async job queue), not
built yet — see BUILD_LOG.md's Tier 2/3 architecture notes. The one integration point that already
exists today: `POST /api/transactions/evaluate` accepts an arbitrary transaction record and runs it
through the full pipeline — wiring a real settlement-ledger webhook to call that endpoint is the
remaining integration work, not a redesign.

**How far a single instance actually goes, from a real measured number, not a guess:** the real
Ollama run linked from the README processed 120 transactions end-to-end (matching + narration for the
18 that needed it) in 46.5 measured seconds — **2.58 transactions/second** sustained, entirely on
local, free inference. Extrapolated (not itself measured at this volume) at that same rate run
continuously: **~222,700 transactions/day** on one instance, no GPU rental, no LLM API cost. That
number would only improve in a realistic production batch, since this demo's own mix sends 15% of
transactions to the narrator (`18/120`) — the Tier 1 sparse-batch benchmark in BUILD_LOG.md shows a
realistic settlement batch is closer to 1-3% needing narration, meaning proportionally far fewer LLM
calls and a correspondingly higher sustained rate.

### Frontend on Netlify

`netlify.toml`, repo root: deploys `frontend/` as a static build — `base = "frontend"`,
`command = "npm run build"`, `publish = "dist"`. Set `VITE_API_BASE_URL` in Netlify's site
environment variables to point at wherever the backend actually runs (self-hosted, or deployed
separately — the backend itself doesn't fit Netlify: it's a stateful FastAPI service with SQLite
persistence and narrator calls that can run minutes against a real LLM provider, not a static site or
a request/response serverless function). On the backend host, set `ALLOWED_ORIGINS` to the deployed
Netlify URL (comma-separated if there's more than one, e.g. a preview + production domain) — CORS
only allows `localhost` by default, so this is required, not optional, for the deployed frontend to
actually reach the deployed backend. Written and reviewed; didn't work out during this project's own
deployment (not diagnosed further, moved to Vercel instead — see below), kept as a still-valid option
for anyone else running this.

### Frontend on Vercel

`frontend/vercel.json`, the equivalent setup for Vercel instead of Netlify: in the Vercel dashboard,
set the project's Root Directory to `frontend` — `vercel.json` (relative to that root) then supplies
`buildCommand`, `outputDirectory`, the `vite` framework preset, and an SPA rewrite. Same
`VITE_API_BASE_URL` / `ALLOWED_ORIGINS` wiring as the Netlify path above, just on Vercel's own
dashboard instead.

### The actual live path for this submission

Backend on Render at `razorpay-buildathon-a1p0.onrender.com`, frontend on Vercel at
[razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app), `ALLOWED_ORIGINS`
on Render pointed at the Vercel URL, and a free UptimeRobot monitor pinging the backend's
`/api/health` every 5 minutes so Render's free-tier 15-minute idle sleep never kicks in — a judge
opening the link gets an instant response instead of a 30-60s cold start, and calibration state
(audit log, calibration history) stays intact across visits instead of resetting on every restart.
Verified live end to end, not just health-checked: a real batch run was driven through the actual
public URLs via Playwright (not local dev servers), zero console/network errors, every dashboard
section — tiles, fee-leak analysis, ERP export, calibration table, escalation queue — populated with
real data. See BUILD_LOG.md for the full deployment trail, including a hardcoded backend port that
would have silently broken on Render and was caught and fixed before it shipped.
