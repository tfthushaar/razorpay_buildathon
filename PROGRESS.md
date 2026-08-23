# Build Progress — Settlement Reconciliation Copilot

Tracker for the build against [docs/track04-settlement-reconciliation-copilot.md](docs/track04-settlement-reconciliation-copilot.md).
Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked/needs input

## Core path (never-cut, per spec §8)

- [x] Repo scaffold (backend/frontend dirs, git, .gitignore)
- [x] Synthetic data generator + hidden ground truth + adversarial cases + 100%-adversarial stress batch (spec §6.1, §4) — 6/6 tests passing, see BUILD_LOG
- [x] Causal chain builder (spec §6.2) — 5-hop trace + ledger gap + SLA timing check
- [x] Matching engine — exact-match pass + deterministic structured diff pass (spec §6.3) — resolves 5 of 8 categories with zero LLM calls, 18/18 tests passing incl. 199-seed fuzz check
- [x] Naive baseline reconciler (spec §6.7) — pure amount equality, documented blind spots on timing_lag (false negative) and currency_rounding (false positive)
- [x] Calibration / auto-resolve layer — per-category accuracy + Wilson CI + threshold logic (spec §6.5) — applies only to narrator categories, genuine_error hard-never-auto-resolves, 7/7 tests passing
- [x] Calibration history accumulator (`app/calibration/history.py`) — persists scored decisions across runs + human confirmations; single-batch N is provably too small to clear threshold alone, see BUILD_LOG
- [x] Audit logger (spec §6.8) — SQLite, append-only, source-row links
- [x] Pipeline orchestrator (`app/pipeline.py`) — full generate→chain→match→narrate→calibrate→audit→baseline→stress flow, 7/7 tests passing

## Agentic layer (needs GROQ_API_KEY — free tier, see BUILD_LOG for provider-switch rationale)

- [x] Mock backend (zero-cost, deterministic, uses real tool functions) — 100% on narration queue across 99-seed fuzz, see BUILD_LOG for why that number needs an asterisk
- [x] Groq backend implemented (Llama 3.3, OpenAI-compatible, tool-calling loop) — **not yet run for real, needs GROQ_API_KEY from the user**
- [x] Agentic discrepancy narrator + 4 tools (spec §6.4) — lookup_fee_schedule, check_sla_window, check_batch_anomalies (duplicate+netting, consolidated), recall_similar_resolutions
- [x] `recall_similar_resolutions` retrieval over audit log (spec §7) — in-memory per-run, grows as batch is narrated

## Polish / differentiators (cut first if behind schedule)

- [x] Escalation triage ranking (spec §6.6) — ₹ amount x ambiguity
- [x] ₹-at-risk calculation — per category in CalibrationReport
- [x] Human-feedback loop (calibration updates live from resolved escalations) — `CalibrationHistory.confirm_human_resolution`, needs a FastAPI endpoint to expose it
- [x] Adversarial stress-test scorecard (spec §6.9) — `pipeline.StressScorecard`
- [x] Live threshold dial (backend support) — `calibrate()`/`CalibrationHistory.report()` are cheap re-aggregations, needs a FastAPI endpoint to expose it
- [ ] Merkle-tree divergence pre-filter (optional stretch, spec §3) — cut first if time runs out, per spec

## Backend API (spec §7)

- [x] FastAPI app: POST /api/run, GET /api/runs/latest, GET /api/calibration (live dial), POST /api/escalations/resolve (feedback loop), GET /api/audit, GET /api/health — 5/5 tests passing, 34/34 overall

## Frontend (spec §6.10)

- [x] React/TS scaffold (Vite)
- [x] Match rate + ₹ reconciled + exception queue view
- [x] Calibration chart w/ live threshold dial — verified live in a real browser, recomputes without re-running the batch
- [x] Baseline comparison chart
- [x] Adversarial stress-test scorecard tile
- [x] Escalation queue ↔ calibration dial coherence ("would auto-resolve at current dial" badges) — found and fixed during browser verification, see BUILD_LOG
- [x] Audit log view (collapsible)
- [x] Full stack verified end-to-end in a real headless browser (Playwright): run → tiles → baseline → stress → calibration dial → resolve escalation → audit log. Zero console/network errors.
- [x] Random-reshuffle path — "Randomize" button next to the seed field picks a fresh random seed, proving the demo isn't replaying 4 hardcoded cases
- [ ] Judge-submitted single-transaction upload — bigger lift, not yet built (would need a one-off evaluate endpoint + form); randomize covers the "not scripted" claim more cheaply for now

## Submission checklist (spec §10)

- [ ] Public repo, clean history, README
- [ ] 5-min pitch video
- [ ] Architecture doc (adapt docs/track04-*.md)
- [ ] "What broke / how fixed" narrative — pulled from BUILD_LOG.md, not invented
- [ ] Reproducible setup instructions
- [ ] Honest exception list surfaced in the UI

---
*This file is the resumption point if the session breaks mid-build — re-read it before starting again rather than re-deriving state.*
