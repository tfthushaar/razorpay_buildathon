# Build Progress — Settlement Reconciliation Copilot

Tracker for the build against [docs/track04-settlement-reconciliation-copilot.md](docs/track04-settlement-reconciliation-copilot.md).
Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked/needs input

## Core path (never-cut, per spec §8)

- [x] Repo scaffold (backend/frontend dirs, git, .gitignore)
- [x] Synthetic data generator + hidden ground truth + adversarial cases + 100%-adversarial stress batch (spec §6.1, §4) — 6/6 tests passing, see BUILD_LOG
- [x] Causal chain builder (spec §6.2) — 5-hop trace + ledger gap + SLA timing check
- [x] Matching engine — exact-match pass + deterministic structured diff pass (spec §6.3) — resolves 5 of 8 categories with zero LLM calls, 6/6 tests passing incl. 199-seed fuzz check
- [x] Naive baseline reconciler (spec §6.7) — pure amount equality, documented blind spots on timing_lag (false negative) and currency_rounding (false positive)
- [x] Calibration / auto-resolve layer — per-category accuracy + Wilson CI + threshold logic (spec §6.5) — applies only to narrator categories, genuine_error hard-never-auto-resolves; **provider-aware since 2026-08-24: only real-LLM decisions count toward the gate, mock decisions tracked separately (`mock_n`) but never earn auto-resolve** — closes a critical gap an external audit found (see BUILD_LOG)
- [x] Calibration history accumulator (`app/calibration/history.py`) — persists scored decisions across runs + human confirmations; single-batch N is provably too small to clear threshold alone, see BUILD_LOG
- [x] Audit logger (spec §6.8) — SQLite, append-only, source-row links
- [x] Pipeline orchestrator (`app/pipeline.py`) — full generate→chain→match→narrate→calibrate→audit→baseline→stress flow, 12/12 tests passing

## Agentic layer — three providers now (see BUILD_LOG for the full provider-survey rationale)

- [x] Mock backend (zero-cost, deterministic, uses real tool functions) — 100% on narration queue across 99-seed fuzz, see BUILD_LOG for why that number needs an asterisk
- [x] **Ollama backend (local, `qwen2.5:7b-instruct`, zero cost, zero rate limit) — recommended default as of 2026-08-24.** Surveyed every free-tier API alternative (Cerebras, Gemini, DeepSeek, GLM, SambaNova, OpenRouter, GitHub Models, Mistral) and found each one either not actually permanently free or still bottlenecked by an RPM/TPM/daily ceiling for our access pattern — went local instead. Real result: full batch + stress (160 txns, 55 narrated) in **~150s**, 94%+ accuracy, GPU-accelerated, fully offline. A genuine concurrent-dispatch attempt was tried, measured (no speedup, real accuracy cost), and reverted — see BUILD_LOG.
- [x] **Narrator category-schema validation** — round 6 of the judge-agent audit found a real live bug: an out-of-schema category (`timing_lag`) sailed through as a confident (0.9) answer because nothing checked `parsed["category"]` against `NARRATOR_CATEGORIES` before this. Fixed in both `narrate_groq`/`narrate_ollama` (fails safe exactly like malformed JSON does), 2 new regression tests added and verified load-bearing, 2 contaminated DB rows precisely identified and deleted, false "confidence 0.0" claim corrected in README/BUILD_LOG, and a real committed evidence file added (`docs/evidence/real-ollama-run-2026-08-24.json`) — see BUILD_LOG.
- [x] **Narrator response-shape validation + confidence clamping** — round 7 found round 6's fix was incomplete: a missing key, wrong container type, or non-numeric confidence still raised an UNCAUGHT exception that crashed the whole batch (reachable live through `/api/transactions/evaluate`, the pitch's own "break it" endpoint). Fixed by wrapping the full parse-validate-construct sequence in one try/except in both providers; also fixed `confidence` never being bounded to `[0.0, 1.0]` (was corrupting escalation triage's priority score), clamped at both call sites plus a Pydantic `Field(ge=0.0, le=1.0)` backstop. 3 new tests, written before the fix and confirmed to fail against it first — see BUILD_LOG.
- [x] **Narrator tool-call execution guard** — self-caught (not an audit finding) while writing up the previous fix: the tool-call branch had the identical unguarded-crash problem one call earlier (malformed tool-call arguments, a hallucinated/unknown tool name). Same fix pattern: wrapped in try/except, routes through `_fail_safe`, 2 new tests written first and confirmed to fail against the unfixed code — see BUILD_LOG.
- [x] **Orchestration-level narrator backstop** — round 8 found the tool-call guard above still missed `AttributeError` (3 live-reproduced shapes: `tc.function` being `None`, and `recall_similar_resolutions` receiving a JSON array/null instead of an object) and made the case that per-function whack-a-mole (rounds 5-8 each closing a different shape) can't fully close this class of bug, since the next shape is by definition unforeseen. Fixed both: added `AttributeError` to both providers' tool-call except tuples, AND wrapped `narrate()`'s dispatch itself in a broad `except Exception` backstop that fails safe on *anything* a provider function's own handling didn't anticipate — the provider-specific fail-safes stay (better diagnostics for known failures), this is the last line of defense for unknown ones. 3 new tests, written before the fix and confirmed to fail first. 66/66 tests passing — see BUILD_LOG.
- [x] Groq backend (openai/gpt-oss-20b, OpenAI-compatible, tool-calling loop) — kept as a second real option. Run for real twice 2026-08-24: Run 1 100% accuracy (docs/evidence/real-groq-run-2026-08-24.json), Run 2 4/4, 7/7, 6/7 with one honest fail-safe miss (docs/evidence/real-groq-run-2026-08-24b-persisted.json). Both runs took 11-70 minutes — the reason Ollama is now the default recommendation.
- [x] Agentic discrepancy narrator + 4 tools (spec §6.4) — lookup_fee_schedule, check_sla_window, check_batch_anomalies (duplicate+netting, consolidated), recall_similar_resolutions. Tool schema (`TOOL_SCHEMAS`) and the retry wrapper (`_call_with_retry`, now provider-parameterized) shared across Groq and Ollama.
- [x] `recall_similar_resolutions` retrieval over audit log (spec §7) — in-memory per-run, grows as batch is narrated

## Polish / differentiators (cut first if behind schedule)

- [x] Escalation triage ranking (spec §6.6) — ₹ amount x ambiguity
- [x] ₹-at-risk calculation — per category in CalibrationReport
- [x] Human-feedback loop (calibration updates live from resolved escalations) — `CalibrationHistory.confirm_human_resolution`, needs a FastAPI endpoint to expose it
- [x] Adversarial stress-test scorecard (spec §6.9) — `pipeline.StressScorecard`
- [x] Live threshold dial (backend support) — `calibrate()`/`CalibrationHistory.report()` are cheap re-aggregations, needs a FastAPI endpoint to expose it
- [x] Merkle-tree divergence pre-filter (optional stretch, spec §3) — `app/matching/merkle.py`, real measured number: 3,010 comparisons vs 50,000 brute-force (94% fewer) at 0.2% divergence; honestly documented that it provides no saving at this project's own ~33%-divergence demo batch density. 5/5 tests passing.

## Backend API (spec §7)

- [x] FastAPI app: POST /api/run, GET /api/runs/latest, GET /api/calibration (live dial), POST /api/escalations/resolve (feedback loop), GET /api/audit, POST /api/transactions/evaluate (live "break it" scenario eval), GET /api/health — 68/68 tests passing overall (current total as of the most recent audit round — see BUILD_LOG.md; per-file counts elsewhere in this file describe that section's own coverage at the time it was completed, not a running total). Round 8 caught this exact line stale at 61 when the real count was already 63 — it's the same recurring failure class as rounds 1-4's test-count drift, this file's own "current total" line included; if it's stale again, that's not a surprise, it's the pattern repeating.

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

## External audit round 3 (2026-08-24) — no critical/high findings, score 84/100

Third independent agent, held to a *higher* bar than rounds 1-2 (not a lower one) since it's a resubmission. Independently re-derived every load-bearing claim (regenerated seed 99's ground truth, queried the live CalibrationHistory and audit log directly, timed DB files before/after a test run, reran the new regression test standalone) rather than trusting BUILD_LOG's retelling. **Score: 84/100, up from 79. Explicit signal: no CRITICAL or HIGH finding — this could be the final round.** Only documentation-accuracy nits found, one being a *third* recurrence of the same stale-number failure class (round 1: UI copy, round 2: test count, round 3: test count again + spec doc + README, in new spots each time). Fixed this round:

- [x] Test counts corrected everywhere (51, not 50) — repo-wide grep this time, not a single instance
- [x] PROGRESS.md's Agentic-layer line updated to mention both real Groq runs, not just the first
- [x] docs/track04-*.md §7 tech stack line updated (Claude API → Groq, was only fixed in §6.5 before)
- [x] React 18 → React 19 (README, BUILD_LOG — wrong from the initial scaffold, not a later drift)
- [!] **Groq API key still not rotated** — flagged in all three rounds now, user action only

## External audit round 4 (2026-08-24) — found something real, score 83/100 (honest dip, not a regression)

Fourth independent agent, pointed at surface area rounds 1-3 hadn't specifically targeted: full frontend render-paths, live DB raw contents via direct SQL, actual installed toolchain requirements. **Score: 83/100** (down 1 from round 3, explicitly attributed to deeper scrutiny, not a regression). Found 5 real issues, most importantly: the tool-call trace — the spec's own headline "AI Judgment" proof — was captured correctly in the backend but never rendered anywhere in the UI. Fixed this round:

- [x] **Tool-call trace now visible** — expandable toggle in `AuditLogView.tsx` + `<details>` block in `BreakItPanel.tsx`. Verified live: 18/120 audit rows show a working toggle with the real tool trace.
- [x] README's Node version corrected (18+ → 20.19+/22.12+, matching Vite's actual declared minimum)
- [x] Removed `currency_rounding` as a possible narrator output (structurally unreachable — verified before removing) from `NARRATOR_CATEGORIES`, the system prompt, and `test_narrator.py`
- [x] Two per-file test counts in this file, wrong since the day they were written, corrected via a systematic per-file recount (not just the one instance flagged)
- [x] Cleaned up 860 rows of leftover pre-isolation-fix test contamination in `audit_log.db`, preserving the 240 rows of genuine evidence; corrected round 1's BUILD_LOG entry to state precisely what was deleted then vs. now
- [!] **Groq API key still not rotated** — flagged in all four rounds now

**Auditor's honest assessment, carried forward rather than edited out:** fixing all 5 findings was estimated to land ~87-90, not 95 outright — Throughput and Real Problem are close to an honest ceiling imposed by real, disclosed constraints (free-tier rate limits, Merkle providing no saving on this project's own dense demo data), and pushing past that would require overclaiming, which contradicts this project's entire approach. Continued rounds may oscillate rather than climb monotonically.

## External audit round 5 (2026-08-24) — the most significant finding of the whole loop, score 72/100

Fifth independent agent found something rounds 1-4 all missed: `_final_decision()` (pipeline.py) checked only whether a *category* had earned trust, never whether *this specific transaction's own classification* came from a real provider. Once a category legitimately earned trust (the intended end-state!), a subsequent mock-mode run's guess in that category would silently ride on trust it never itself earned — falsifying "only auto-resolves what it's proven itself accurate on" at the per-decision level, through entirely ordinary use (mock is the UI's default). Proved live with a real reproduction against the unmodified code. **Score: 72/100** — a real, warranted drop (AI Judgment 6, Bounded & Gated 5).

- [x] **Fixed the core gap**: threaded `output.provider` into `_final_decision()` and `_stress_scorecard()`'s equivalent check — now requires the category to be trusted AND this specific decision to be non-mock. Verified the fix is load-bearing by temporarily reverting it and confirming the new regression test (`test_provider_gate_applies_per_decision_not_just_per_category`) fails without it, then restored the fix. 53/53 tests passing.
- [x] Fixed a nonsensical mock-mode throughput display (was showing "120000000.0/s"; now "instant (mock — no network calls)")
- [x] Added the three-way baseline decomposition round 4's auditor recommended (naive vs. this project's own deterministic engine alone vs. full system) — `deterministic_only_resolved_count`/`amount_reconciled` fields, a third bar in `BaselineComparison.tsx`, honest copy for the case where the narrator hasn't earned auto-resolve yet in the current session
- [!] **Tension flagged, not resolved unilaterally:** round 5 estimates fixing everything found lands in the mid-to-high 80s, not 95 — Throughput's remaining ceiling is the free-tier token budget itself (raising it means a paid tier, conflicting with the user's own cost-minimization instruction), and Real Problem's Merkle disclosure is a correct, permanent feature of this project's chosen demo data, not a defect. This needs the user's input on how to proceed, not a 6th round manufacturing findings to force the number up.

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
- [x] Judge-submitted scenario evaluation — `POST /api/transactions/evaluate` + "Break it" panel (3 presets: duplicate refund, netting-trap pair, clean control), editable JSON, goes through the same calibration gate as a batch run. Verified live in browser, all 3 presets correct, 0 console errors. **Round 9 found this endpoint crashed with an opaque 500 (no category/reasoning, unlike every one of the narrator's own fail-safes) on a plausible malformed judge edit — a missing or mismatched order/payment/settlement/ledger reference.** Fixed: a specific 422 naming the broken reference for the known `KeyError` shape, plus a broader backstop for anything else, same two-part pattern as the narrator's own round-8 fix — see BUILD_LOG. 2 new tests, written before the fix and confirmed to fail against the unfixed endpoint first.

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
