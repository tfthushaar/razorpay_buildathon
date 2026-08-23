# Build Progress — Settlement Reconciliation Copilot

Tracker for the build against [docs/track04-settlement-reconciliation-copilot.md](docs/track04-settlement-reconciliation-copilot.md).
Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked/needs input

## Core path (never-cut, per spec §8)

- [x] Repo scaffold (backend/frontend dirs, git, .gitignore)
- [x] Synthetic data generator + hidden ground truth + adversarial cases + 100%-adversarial stress batch (spec §6.1, §4) — 6/6 tests passing, see BUILD_LOG
- [x] Causal chain builder (spec §6.2) — 5-hop trace + ledger gap + SLA timing check
- [x] Matching engine — exact-match pass + deterministic structured diff pass (spec §6.3) — resolves 5 of 8 categories with zero LLM calls, 18/18 tests passing incl. 199-seed fuzz check
- [x] Naive baseline reconciler (spec §6.7) — pure amount equality, documented blind spots on timing_lag (false negative) and currency_rounding (false positive)
- [x] Calibration / auto-resolve layer — per-category accuracy + Wilson CI + threshold logic (spec §6.5) — applies only to narrator categories, genuine_error hard-never-auto-resolves; **provider-aware since 2026-08-24: only real-LLM decisions count toward the gate, mock decisions tracked separately (`mock_n`) but never earn auto-resolve** — closes a critical gap an external audit found (see BUILD_LOG)
- [x] Calibration history accumulator (`app/calibration/history.py`) — persists scored decisions across runs + human confirmations; single-batch N is provably too small to clear threshold alone, see BUILD_LOG
- [x] Audit logger (spec §6.8) — SQLite, append-only, source-row links
- [x] Pipeline orchestrator (`app/pipeline.py`) — full generate→chain→match→narrate→calibrate→audit→baseline→stress flow, 7/7 tests passing

## Agentic layer (needs GROQ_API_KEY — free tier, see BUILD_LOG for provider-switch rationale)

- [x] Mock backend (zero-cost, deterministic, uses real tool functions) — 100% on narration queue across 99-seed fuzz, see BUILD_LOG for why that number needs an asterisk
- [x] Groq backend (openai/gpt-oss-20b, OpenAI-compatible, tool-calling loop) — **run for real 2026-08-24: 100% accuracy on main batch (n=4/6/8 across 3 categories), 37/37 correct on stress batch, 0 wrongly auto-resolved.** Retry-with-backoff added after hitting a real rate limit. See docs/evidence/real-groq-run-2026-08-24.json
- [x] Agentic discrepancy narrator + 4 tools (spec §6.4) — lookup_fee_schedule, check_sla_window, check_batch_anomalies (duplicate+netting, consolidated), recall_similar_resolutions
- [x] `recall_similar_resolutions` retrieval over audit log (spec §7) — in-memory per-run, grows as batch is narrated

## Polish / differentiators (cut first if behind schedule)

- [x] Escalation triage ranking (spec §6.6) — ₹ amount x ambiguity
- [x] ₹-at-risk calculation — per category in CalibrationReport
- [x] Human-feedback loop (calibration updates live from resolved escalations) — `CalibrationHistory.confirm_human_resolution`, needs a FastAPI endpoint to expose it
- [x] Adversarial stress-test scorecard (spec §6.9) — `pipeline.StressScorecard`
- [x] Live threshold dial (backend support) — `calibrate()`/`CalibrationHistory.report()` are cheap re-aggregations, needs a FastAPI endpoint to expose it
- [x] Merkle-tree divergence pre-filter (optional stretch, spec §3) — `app/matching/merkle.py`, real measured number: 3,010 comparisons vs 50,000 brute-force (94% fewer) at 0.2% divergence; honestly documented that it provides no saving at this project's own ~33%-divergence demo batch density. 5/5 tests passing.

## Backend API (spec §7)

- [x] FastAPI app: POST /api/run, GET /api/runs/latest, GET /api/calibration (live dial), POST /api/escalations/resolve (feedback loop), GET /api/audit, POST /api/transactions/evaluate (live "break it" scenario eval), GET /api/health — 50/50 tests passing overall

## External audit (2026-08-24) — judge-agent review, round 1

Spawned an independent agent to audit the whole project as a Razorpay judge would, scoring against
the spec's own criteria and verifying claims against actual code/tests rather than trusting docs.
**Scores: AI Judgment 7/10, Failure Recovery 8/10, Measured Accuracy 8/10, Throughput 5/10, Bounded
& Gated 5/10, Real Problem 8/10, Submission Readiness 8/10 — Overall 71/100.** Full findings and
fixes in BUILD_LOG.md. Fixed this round:

- [x] **Provider-aware calibration gate** (critical) — mock-mode decisions could accumulate toward auto-resolve with zero real LLM involvement; empirically verified as fixed (mock accumulation now always escalates; real-provider accumulation still works)
- [x] **Test isolation** (high) — `test_api.py` was clearing the live demo's SQLite databases on every `pytest` run; now uses `conftest.py`'s `isolated_app_state` fixture with temp-file-backed instances
- [x] **Throughput instrumentation** (medium) — `elapsed_seconds`/`narrated_count`/`transactions_per_second` now measured and attached to every `BatchRunResult`, surfaced as a dashboard tile
- [x] Stale UI copy (trivial) — "Llama 3.3" → "gpt-oss-20b" in RunControls.tsx
- [x] Precision-of-claim fix (medium) — the "100% accuracy" headline now correctly distinguishes 17/18 genuine-reasoning vs. 1/18 safe-fallback-that-happened-to-be-right

Not fixed this round (see BUILD_LOG for why): no committed Playwright spec/preserved screenshots (partially mitigated — every fix above was re-verified live with a fresh screenshot); Groq API key needs rotation before any public push (user action, flagged); `recall_similar_resolutions` stays per-run-only (already disclosed, lower priority).

## External audit round 2 (2026-08-24) — verified the round-1 fix, found it hadn't fully shipped

Second independent agent, instructed to verify (not trust) round 1's fixes and actively try to break them. **Score: 79/100, up from 71.** The provider-aware calibration fix itself held under a direct adversarial probe (522 human-feedback-loop resolutions, still correctly escalates). But found the real Groq run's data was never actually persisted into the live `CalibrationHistory` the dashboard reads from (the script that produced it never passed `calibration_history=`) — the real evidence existed only as a static JSON snapshot, not in the running system's own state. Fixed this round:

- [x] Re-ran the real Groq batch (new seed 99) properly wired to the actual persistent `backend/data/*.db` files — real narrator decisions now genuinely accumulated in the live state, not just a side file
- [x] README test count corrected (45 → 50, same stale-doc failure class as an earlier fixed gap, caught recurring)
- [x] Added a permanent HTTP-level regression test for the resolve-loop-at-volume adversarial scenario (`test_resolving_many_mock_escalations_over_http_cannot_graduate_a_category`)
- [!] **Groq API key still not rotated** — flagged in round 1, still open in round 2, restated directly to the user

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
- [x] Judge-submitted scenario evaluation — `POST /api/transactions/evaluate` + "Break it" panel (3 presets: duplicate refund, netting-trap pair, clean control), editable JSON, goes through the same calibration gate as a batch run. Verified live in browser, all 3 presets correct, 0 console errors.

## Submission checklist (spec §10)

- [x] Clean local git history, clear README — **not yet pushed to the public remote, ask before pushing**
- [ ] 5-min pitch video — outside what code can produce; user's to record
- [x] Architecture doc (docs/track04-*.md, kept current through the build, several mid-build revisions logged in BUILD_LOG)
- [x] "What broke / how fixed" narrative — BUILD_LOG.md, real bugs with root causes, fixes, and verification, not invented after the fact
- [x] Reproducible setup instructions — README, followed independently by the external audit agent
- [x] Honest exception list surfaced in the UI — escalation queue with reasoning, never hidden
- [!] **Rotate the GROQ_API_KEY before pushing publicly or recording the pitch video** — it was shared in this session's chat and is not committed, but should be rotated at console.groq.com as a precaution

---
*This file is the resumption point if the session breaks mid-build — re-read it before starting again rather than re-deriving state.*
