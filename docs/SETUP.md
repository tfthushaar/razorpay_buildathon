# Full setup

Requires Python 3.11+ and Node 20.19+ (or 22.12+) — Vite 8's own minimum, not just a suggestion. The
[README](../README.md) has a three-line quick start for a mock-provider run; this is the full
picture — real LLM providers, Docker, and how the live deployment is wired.

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

By default the narrator runs in **mock mode** — zero cost, deterministic, calling the exact same real
tool functions the live narrator does (only the final synthesis step is a fixed rule). Set a real
provider by copying `backend/.env.example` to `backend/.env` and editing it (`main.py` calls
`load_dotenv()` on startup, so this works the same on every shell).

**Recommended: `ollama`** — fully local, zero cost, zero rate limit, works offline.

```bash
winget install Ollama.Ollama       # or download from ollama.com
ollama pull qwen2.5:7b-instruct    # ~4.7GB, one-time
```

Set `LLM_PROVIDER=ollama` in `backend/.env`. GPU-accelerated automatically where available. Chosen
after evaluating every free-tier hosted API (Groq, Cerebras, Gemini, DeepSeek, GLM, SambaNova,
OpenRouter, GitHub Models, Mistral — see BUILD_LOG.md) and finding each one rate- or credit-capped
too tightly for a real batch. A circuit breaker (`app/narrator/circuit_breaker.py`) sits in front of
both real providers: 3 consecutive failures trips it, so the rest of a queue fails safe immediately
instead of each transaction re-discovering the same outage.

**Alternative: `groq`** — a real hosted tool-calling API. Set `GROQ_API_KEY` (free tier at
console.groq.com) and `LLM_PROVIDER=groq`, same file. Default model `openai/gpt-oss-20b`. Free-tier
accounts hit real per-minute token limits; the narrator retries with backoff automatically, but a
full batch can still take minutes. Either provider can also be set per-request via `"provider"` in
the `/api/run` body.

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the printed local URL, click **Run batch**, drag the calibration threshold slider, and try
resolving an escalation to see the human-feedback loop.

## Docker

```bash
docker compose up --build
```

Runs the backend on `:8000` (`LLM_PROVIDER=mock` by default — see `docker-compose.yml` for
`groq`/`ollama`; Ollama on the host needs `OLLAMA_HOST=http://host.docker.internal:11434` set
explicitly, since `localhost` isn't reachable from inside the container) and the frontend on `:5173`.
SQLite state persists in a named volume. Actually built and run against a real Docker install, driven
live through a browser with zero console errors — see BUILD_LOG.md.

This containerizes the current single-instance implementation as-is — no horizontal scaling, message
queue, or real settlement-ledger webhook (identified next steps, not built). The integration point
that already exists: `POST /api/transactions/evaluate` accepts an arbitrary transaction record and
runs it through the full pipeline; wiring a real webhook to call it is the remaining work, not a
redesign.

**Measured, not guessed:** a real Ollama run processed 120 transactions end-to-end in 46.5 seconds —
**2.58 tx/sec** sustained, on local, free inference. Extrapolated at that rate, continuously:
~222,700 tx/day on one instance. This demo's own mix sends 15% of transactions to the narrator; a
realistic settlement batch (1-3% needing narration, per BUILD_LOG.md's sparse-batch benchmark) would
run proportionally faster.

## Frontend hosting (Netlify / Vercel)

Both are static-build configs for `frontend/` only — the backend is a stateful FastAPI service with
SQLite persistence and narrator calls that can run minutes against a real LLM, not a fit for either
platform's serverless model. Set `VITE_API_BASE_URL` (frontend) to wherever the backend runs, and
`ALLOWED_ORIGINS` (backend) to the deployed frontend URL — CORS only allows `localhost` by default.

- **Netlify**: `netlify.toml` at repo root (`base = "frontend"`, `command = "npm run build"`,
  `publish = "dist"`). Written and reviewed; didn't work out during this project's own deployment
  (not diagnosed further, moved to Vercel), kept as a valid option for anyone else running this.
- **Vercel**: `frontend/vercel.json`; set the project's Root Directory to `frontend` in the Vercel
  dashboard.

## The actual live path for this submission

Backend on Render (`razorpay-buildathon-a1p0.onrender.com`), frontend on
[Vercel](https://razorpay-buildathon-five.vercel.app), `ALLOWED_ORIGINS` pointed at the Vercel URL,
and a free UptimeRobot monitor pinging `/api/health` every 5 minutes so Render's free-tier idle sleep
never kicks in — a judge opening the link gets an instant response, not a cold start, and state
persists across visits. Verified live end to end via Playwright against the actual public URLs, zero
console/network errors. Full deployment trail, including a hardcoded port that would have silently
broken on Render and was caught before it shipped: BUILD_LOG.md.
