# Build Log — what broke, what's working, and why

Chronological engineering journal. This is real, not reconstructed after the fact — it's the source material for the
submission's required "what broke during development and how I fixed it" narrative (spec §10). Nothing here is invented
retroactively to look good; entries are added as things actually happen during the build.

Format per entry: **date/phase — what was attempted — what happened — resolution/status**.

---

## 2026-08-23 — Project scaffold

- **Attempted:** `git init` a fresh local repo and connect it to the existing empty GitHub remote (`tfthushaar/razorpay_buildathon`).
- **What happened:** the repo had no git config at all (not even global `user.name`/`user.email`), so a bare `git commit`
  would have failed outright.
- **Resolution:** set `user.name`/`user.email` scoped locally to this repo only (not global), using the GitHub account's
  own identity. Working.

- **Known gap (not yet a bug, flagged in advance):** `ANTHROPIC_API_KEY` is not set in this environment. The agentic
  discrepancy narrator (spec §6.4) is being built against the real Claude API with a clean call interface, but with a
  deterministic mock/fallback mode so the rest of the pipeline (chain builder → matching → calibration → audit log →
  dashboard) can be built and tested end-to-end without live LLM calls. Swapping in a real key later should require no
  code changes — only setting the env var. **Status: by design, not a failure — tracked here so it isn't mistaken for
  an oversight later.**

- **Decision — LLM provider switched from Claude to Groq (Llama 3.3), user-directed:** the original
  spec named the Claude API for the narrator. Mid-build the user explicitly said not to treat
  Anthropic as load-bearing and to pick whatever is better/cheaper, with an explicit mandate to
  minimize running cost. Groq's API is OpenAI-tool-call-compatible, supports JSON mode, and its
  free tier is genuinely $0 at hackathon scale. The narrator is being built behind a provider
  interface so the concrete backend is a one-line swap (`LLM_PROVIDER=mock|groq`, Anthropic/OpenAI/
  Gemini could be added the same way later without touching call sites). **Status: architectural
  decision, not yet implemented — see PROGRESS.md.**

---

## 2026-08-23 — Synthetic data generator (spec §6.1, §4)

- **Built:** `backend/app/data_gen/{schemas,fee_schedule,generate}.py` — full entity set (Order,
  Payment, Refund, Settlement, LedgerEntry, GroundTruthEntry) with real-Razorpay-shaped fields
  (`utr`, `fee`/`tax`/`captured`/`entity`, amounts in paise not decimal rupees), a fee/SLA schedule
  shared with what the narrator's tools will query later, and 8 category generators: clean_match,
  timing_lag, fee_deduction, partial_refund, currency_rounding, duplicate_refund, netting_trap,
  genuine_error.
- **First run:** generated main batch (n=120) + stress batch (n=40) on the first attempt with no
  runtime errors. Distribution came out clean_match=72 (60%), explainable=30 (25%), adversarial
  (duplicate_refund+netting_trap)=12 (10%), genuine_error=6 (5%) — matches the spec's documented
  60/25/10/5 target exactly on this seed.
- **What I checked, and why it mattered:** wrote a throwaway verification script before trusting the
  generator, because a wrong arithmetic injection here would silently invalidate every accuracy
  number reported later. Checked: (a) every category's settlement/ledger delta equals exactly what
  that category claims to inject (e.g. fee_deduction's ledger-minus-settlement delta equals
  fee+tax, not some other number), (b) the netting_trap pairs genuinely sum to a clean batch total
  while each half is individually wrong — this is the property that makes the "causal chain
  matching, not row matching" pitch true rather than aspirational. All checks passed with zero
  mismatches across a 150-record batch.
- **Promoted to a real test suite:** `backend/tests/test_generate.py`, 6 tests (structural
  integrity, same-seed reproducibility, distribution-matches-spec, stress-batch-is-100%-adversarial,
  per-category arithmetic, netting-trap pair property). All passing. Kept as a permanent regression
  test, not thrown away after manual verification — if a future change to the generator breaks the
  arithmetic invariants, this catches it immediately instead of surfacing as a confusing accuracy
  drop three layers downstream.
- **Nothing broke in this step.** Noted here anyway (rather than only logging failures) so the
  audit trail doesn't read as cherry-picked — this is the first component built and it worked
  cleanly on the first real run after the design was thought through up front.

---

## 2026-08-23 — Causal chain builder + matching engine (spec §6.2, §6.3) — real bug, caught and fixed

- **Built:** `backend/app/chain/builder.py` (5-hop causal trace: order→capture→post-fee→post-tax→
  post-refunds→settlement, plus a separate ledger-gap and SLA-timing check) and
  `backend/app/matching/{engine,baseline}.py` (Pass 1 exact-match, Pass 2 deterministic structured
  diff, and the naive baseline for comparison).
- **Design decision made while building, not before:** Pass 2 turned out to fully and
  deterministically explain clean_match, fee_deduction, partial_refund, timing_lag, and
  currency_rounding using only arithmetic on the transaction's own records — no LLM call needed for
  ~85% of the main batch. Only duplicate_refund, netting_trap, and genuine_error (the genuinely
  unexplainable-from-records cases) need to reach the narrator. This wasn't planned this precisely
  upfront; it fell out of formalizing the hop model, and it directly serves the cost-minimization
  goal — most of the batch never touches a paid API call at all.
- **BUG FOUND:** `test_deterministic_categories_resolve_without_narration_and_match_ground_truth`
  failed on first run — one netbanking `timing_lag` transaction was classified `clean_match`
  instead. Root cause: the generator stretched the settlement delay *relative to a randomly
  sampled base SLA* (netbanking's base itself varies 2-5 days), so a low base (e.g. 2) plus a
  small stretch (+1) could land back inside what the matching engine considers normal variance
  for netbanking (tolerance ceiling of 5 days) — the injected "exception" wasn't reliably an
  exception.
  - **Fix:** moved the tolerance threshold (`SLA_TOLERANCE_DAYS`) into `fee_schedule.py` as the one
    shared source of truth between the generator and the engine, and changed the generator to
    stretch *from that shared ceiling*, not from its own random base — so a timing_lag case is now
    guaranteed to cross the same line the engine checks against, on every rail, every time.
  - **Verified the fix, not just the one failing seed:** re-ran the full pipeline across 199 seeds
    x 2 batches (main + stress, ~400 batches total) checking the same invariant — zero mismatches.
    Also added a permanent regression assertion (`settlement.sla_days > SLA_TOLERANCE_DAYS[rail]`)
    to `test_generate.py` so this can't silently regress again.
- **Why this is worth narrating in the submission:** this is a genuine example of the two-rail
  design (UPI/card fixed SLA vs. netbanking's variable SLA) creating a real edge case that only
  showed up once actual test assertions ran against actual generated data — exactly the kind of
  "identify a runtime failure, engineer a fix, verify it broadly" story the Failure Recovery
  criterion is asking for, and it's pulled directly from this log, not reconstructed after the fact.
- **Also verified deliberately:** the naive baseline's two blind spots are real and reproducible —
  it silently calls every `timing_lag` case "clean" (no date/SLA awareness) and flags every
  `currency_rounding` case as a false-positive mismatch (zero tolerance). Both are asserted in
  `test_matching.py` and feed directly into the baseline-comparison lift number (spec §6.7).

---

## 2026-08-23 — Calibration layer (spec §6.5)

- **Built:** `backend/app/calibration/{wilson,calibrator}.py` — Wilson score CI per category,
  threshold gate checked against the CI *lower bound*, and a hard rule that `genuine_error` can
  never auto-resolve regardless of measured accuracy (escalation is the correct outcome for that
  category by definition, not a fallback for low confidence).
- **Design refinement made while building:** calibration (and its Wilson CI) is applied only to
  narrator-classified decisions, not to the deterministic Pass 1/2 resolutions from the matching
  engine. A deterministic resolution (e.g. `fee_deduction`, proven by exact arithmetic on the
  records) isn't a statistical estimate — running a confidence interval over it is conceptually
  wrong, and could even wrongly gate a provably-correct category due to small-N penalty. Calibration
  is reserved for exactly the three categories that ever reach the narrator, which is also exactly
  where "AI Judgment" is actually being exercised.
- **Test failure, and what it actually revealed:** `test_high_accuracy_large_n_category_auto_resolves`
  first failed with 40/41 correct (97.6% point accuracy) still coming out "escalate" at a 90%
  threshold. Manually recomputed the Wilson lower bound by hand before assuming a bug — it genuinely
  comes out to ~87% at n=41, correctly below threshold. This was the test's expectation being wrong,
  not the code: a small sample shouldn't be trusted at a high point-accuracy just because it looks
  good, which is the entire reason the CI-lower-bound gate exists instead of a raw percentage. Fixed
  the test to use a large-enough N (102) where the lower bound genuinely clears the bar, and kept the
  small-N case as its own explicit test (`test_wilson_interval_penalizes_small_samples`) so the
  distinction stays documented rather than accidentally "fixed" away later.
- **Verified:** the live-threshold-dial property (same scored decisions, different threshold,
  decision flips without touching the underlying data) and the human-feedback-loop property (a
  category starting below threshold crosses it as more confirmed resolutions accumulate) both work
  as designed — 7/7 tests passing.

---

## 2026-08-23 — Agentic narrator + tools (spec §6.4) — second real bug, same failure family as the SLA one

- **Built:** `backend/app/narrator/tools.py` (4 tools: `lookup_fee_schedule`, `check_sla_window`,
  `check_batch_anomalies`, `recall_similar_resolutions`) and `backend/app/narrator/agent.py`
  (provider dispatch: `mock` vs `groq`, both behind one `narrate()` entry point).
- **Design gap caught before it became a bug:** the originally-sketched 4th tool was
  `check_duplicate_registry` with no way to detect `netting_trap` at all — a transaction whose
  delta is only explained by a *paired* transaction elsewhere in the batch is invisible to a tool
  that only inspects one payment's own refunds. Folded duplicate-detection and batch-netting
  detection into one `check_batch_anomalies` tool (still 4 tools total, not 5) before wiring
  anything up, rather than discovering the gap after the fact.
- **BUG FOUND (same family as the SLA-tolerance bug above — a paired/derived value sampled
  independently instead of shared):** `test_check_batch_anomalies_finds_the_real_duplicate_and_netting_signals`
  failed immediately — a real `netting_trap` pair's partner wasn't being found. Root cause: each
  half of a netting_trap pair got its settlement's SLA delay sampled *independently*
  (`_build_settlement` without an explicit `sla_days`), so the two settlements could land on
  different calendar dates and therefore get different `settlement_batch_id` strings — breaking
  the entire premise that they only look reconciled when netted *within the same batch*.
  - **Fix:** added a `batch_id_override` parameter to `_build_settlement`, and changed
    `_gen_netting_trap` to sample one shared `sla_days` and one shared batch id for both halves of
    the pair explicitly, rather than hoping two independently-derived timestamps coincidentally
    matched.
  - **Verified broadly, not just the failing seed:** re-ran generation + chain-building + matching +
    narration across 99 seeds x 2 batches (~4,000 narrated transactions total). Zero missed
    duplicate/netting detections.
- **Why this keeps happening, noted honestly:** both real bugs so far share a root cause —
  a value that two related records need to agree on (SLA tolerance line; settlement batch
  membership) was instead being sampled independently per record, and only sometimes disagreed by
  chance. Worth calling out explicitly in the submission's failure-recovery narrative: the fix
  pattern each time was "make the shared invariant a single source of truth instead of two
  independent random draws," not a one-off patch.
- **Mock-mode accuracy result — reported with an explicit caveat:** the mock narrator hit 100%
  (4029/4029) on the narration queue across the 99-seed fuzz run. This is expected and NOT a claim
  about real LLM judgment quality — the mock stubs only the final synthesis step and applies a
  fixed rule over the same deterministic tool signals every time, so of course it's consistent.
  The real accuracy number that belongs in the submission is whatever `narrate_groq` actually
  achieves once a `GROQ_API_KEY` is available — that's the number that reflects actual agentic
  judgment, not this one. Flagging this now so it can't be quoted out of context later.
- **Still blocked on:** no `GROQ_API_KEY` in this environment, so `narrate_groq` is implemented and
  reviewed but not yet run against the real API. Needs the user to supply a key (free tier at
  console.groq.com) before the real accuracy/calibration numbers can be produced.

---

## 2026-08-23 — Audit logger, escalation triage, pipeline orchestrator — a real design finding, not a bug

- **Built:** `backend/app/audit/logger.py` (SQLite, append-only, one row per decision with the
  reasoning + tool-call trace + source-row links), `backend/app/matching/escalation.py` (₹ amount
  x ambiguity triage), and `backend/app/pipeline.py` (the full generate -> chains -> matching ->
  narration -> calibration -> audit -> baseline -> stress-scorecard orchestration in one callable
  `run_batch()`).
- **Ran the full pipeline for the first time and read the actual numbers** (mock provider,
  seed=42, main_n=150, stress_n=50, threshold=0.90) before trusting any of it:
  - Amount reconciled automatically: 80.1% of batch value; naive baseline's "clean" count: 66.7%.
  - Baseline blind spots confirmed on real output: 10 timing_lag cases silently called clean
    (false negative), 9 currency_rounding cases flagged as mismatches (false positive).
  - Stress scorecard: 50/50 handled correctly, 0 wrongly auto-resolved.
  - **But the calibration table came back with every single narrator category — duplicate_refund
    (n=3), netting_trap (n=12), genuine_error (n=7) — set to "escalate", all at 100% measured
    accuracy.** That's not obviously wrong on its face, so it got checked rather than shipped.
- **Finding, not a bug:** hand-verified the Wilson lower bound at n=12, 100% correct: ≈75.7%,
  correctly below a 90% threshold. Checked how large n would need to be for 100%-accurate to clear
  90%: worked it out at roughly n=40. At the spec's own suggested batch size (50-200 records), the
  ~10% adversarial share split across two auto-resolvable categories (duplicate_refund,
  netting_trap) never gets there in a single batch — meaning calibration reset per-batch would
  escalate every narrator-classified transaction, in every batch, forever. Demonstrably-safe, but
  never demonstrably *autonomous* — which undercuts exactly the "AI Judgment" story this whole
  build exists to prove.
  - **Fix (an architecture change, not a patch):** built `backend/app/calibration/history.py` — a
    SQLite-backed `CalibrationHistory` that accumulates scored decisions across every batch run
    *and* every human-confirmed escalation resolution, instead of scoring each batch in isolation.
    `pipeline.run_batch()` now takes an optional `calibration_history`; when provided, the
    calibration report is computed over the full accumulated history, not just the current batch.
  - **Verified both halves of the claim, not just the fix:** `test_single_batch_alone_cannot_clear_threshold_but_accumulated_history_can`
    confirms a lone ~120-record batch does NOT clear the threshold for duplicate_refund/netting_trap
    (guards the premise, not just the fix), and that accumulating 7 batches' worth of mock decisions
    through the same `CalibrationHistory` DOES cross it for at least one category — while
    `genuine_error` still never auto-resolves regardless. 7/7 pipeline tests passing.
  - **Updated the spec doc itself** (docs/track04-settlement-reconciliation-copilot.md §6.5) to
    describe this properly — framed as a strength for the pitch, not a caveat: the system is
    appropriately conservative by default (a handful of same-batch wins shouldn't unlock autonomy)//
    and demonstrably capable of earning it as evidence accumulates, which is a more honest and more
    interesting story than a threshold that was never actually tested against real accumulation.

---

## 2026-08-23 — FastAPI layer (spec §7) — third real bug, a threading issue this time

- **Built:** `backend/app/main.py` — `POST /api/run`, `GET /api/runs/latest`, `GET /api/calibration`
  (the live threshold dial), `POST /api/escalations/resolve` (the human-feedback loop over HTTP),
  `GET /api/audit`, `GET /api/health`. Module-level singleton state (one `AuditLogger`, one
  `CalibrationHistory`) — deliberate for a single-session demo tool, not a multi-tenant service.
- **BUG FOUND on the very first API test run:**
  `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread.`
  Root cause: FastAPI runs synchronous endpoint functions in a worker threadpool (via
  `anyio.to_thread.run_sync`), but `audit_logger`/`calibration_history` are module-level singletons
  whose `sqlite3.connect()` call ran once, at import time, in the main thread. Every actual request
  then touched that connection from a different thread and Python's sqlite3 module refuses that by
  default.
  - **Fix:** `sqlite3.connect(path, check_same_thread=False)` on both connections. Safe at this
    app's scale — one demo session, one presenter, requests are not meaningfully concurrent — and
    documented as such in both files so it isn't mistaken for a production-multi-tenant-safe
    pattern later.
  - **Verified:** all 5 new API tests pass (`test_api.py`), exercising the run -> calibration-dial
    -> resolve-escalation -> audit round trip over real HTTP via FastAPI's TestClient, not just
    calling the pipeline functions directly in-process. Full suite: 34/34 passing.
- **Pattern across all three bugs found so far, worth stating plainly in the submission:** every
  one was caught by an actual test run, not by inspection, and every one was a "two things need to
  agree but were computed independently" class of bug (SLA tolerance line, settlement batch
  membership, thread ownership of a shared connection) rather than three unrelated mistakes. That
  consistency is itself worth a line in the "what broke and how I recovered" narrative.

---

## 2026-08-23 — React dashboard (spec §6.10) + full-stack browser verification

- **Built:** Vite + React 18 + TypeScript dashboard — run controls, summary tiles, baseline
  comparison, adversarial stress-test scorecard, the live calibration threshold dial, the triaged
  escalation queue with a resolve-against-source-records action, and a collapsible audit log view.
- **Actually ran the full stack and drove it in a real headless browser** (Playwright against
  `chromium-cli`'s cached Chromium, since `chromium-cli` itself wasn't available in this
  environment) rather than stopping at `tsc --noEmit` and a production build — those only prove
  the code compiles, not that the feature works, per this project's own standard for UI changes.
  Verified: batch run populates every panel, zero console errors, zero failed network requests,
  120/120 audit rows for a 120-record run.
- **UX coherence gap found by actually looking at the screenshot, not by inspection:** at the
  default 90% run-time threshold, the calibration table correctly showed all three narrator
  categories as "Escalate" — but after dragging the live threshold dial down to 30% and watching
  `duplicate_refund`/`netting_trap` flip to "Auto-resolve" in the calibration table, the escalation
  queue below still listed every one of those transactions with no visual link to the table above.
  Functionally correct (the run-time decision doesn't retroactively change), but incoherent as a
  demo: a viewer would reasonably ask "then why is this still sitting in the queue?"
  - **Fix:** lifted the live calibration report up from `CalibrationPanel` via an `onReportChange`
    callback, derived the current auto-resolve category set in `App.tsx`, and passed it down to
    `EscalationQueue` so each item gets a "Would auto-resolve at current dial" badge and a dashed
    border when its category crosses live, without literally removing it from the list (removing
    it would look like data silently disappearing).
  - **Verified the fix by re-driving the same browser flow:** dragging to 30% now marks exactly the
    items in `duplicate_refund`/`netting_trap` (12 of them) and leaves every `genuine_error` item
    unmarked — matching the calibration table exactly, screenshot-confirmed.
- **Playwright test-script bugs, not app bugs, noted for completeness:** `locator.fill()` doesn't
  work on `<input type="range">` (needed a manual value-setter + dispatched `input` event instead),
  and scoping a locator with `.first()` before calling `.count()` on it always returns 0 or 1 by
  construction — neither reflects a defect in the shipped code, both are listed here only so they
  aren't mistaken for one on a re-read.

---

## 2026-08-23 - README + reproducibility check found one more gap

- Built README.md (reproducible setup, spec's submission checklist) and .env.example for both
  backend and frontend.
- Caught while writing the README, not by a test: requirements.txt has included python-dotenv
  since the very first backend scaffold, but nothing ever called load_dotenv() - a backend/.env
  file would have been silently ignored, and GROQ_API_KEY/LLM_PROVIDER would only ever have worked
  if exported directly into the shell. Writing "copy .env.example to .env" into the README and then
  mentally tracing what would actually happen exposed it. Added load_dotenv() to the top of
  app/main.py. Re-ran the full suite (34/34 still passing).

---

## 2026-08-23 — Live "break it" evaluation path (spec's most-emphasized demo moment)

- **Built:** `POST /api/transactions/evaluate` — accepts a hand-crafted or judge-submitted
  scenario (one or more transactions, not a pre-generated batch), runs it through the exact same
  causal chain builder, matching engine, agentic narrator, *and* the same calibration gate a
  batch-derived transaction goes through (reused `pipeline._final_decision` rather than special-
  casing this path). A "Randomize" control was also added next to the seed field so a full batch
  run can be shown as provably unscripted too, not just single transactions.
- **Frontend:** a "Break it" panel with three hand-built presets (duplicate refund, a netting-trap
  *pair*, and a clean control) loaded into an editable JSON textarea — load a preset, tweak a
  number, resubmit, live.
- **Verified in a real browser, all three presets, in one pass:** duplicate refund correctly
  escalated as `duplicate_refund` (confidence 0.90); the netting-trap pair correctly identified
  *both* transactions as `netting_trap`, each one's reasoning naming the other transaction_id by
  ID — this is the strongest available proof that causal-chain matching is really per-transaction,
  not batch-aggregate; the clean control resolved as `clean_match` at Pass 1 with no false alarm.
  Zero console errors across all three.
- **Visual bug found from actually looking at the screenshot:** the decision badge used a single
  fixed amber "warning" color for every resolution label, so "Clean (exact match)" rendered
  visually identical to "Escalated" — backwards for a demo meant to show the system distinguishing
  good outcomes from bad ones. Fixed by coloring the badge green for clean/auto-resolved
  outcomes and amber only for escalated/needs-narration, and moved the category name to a neutral
  badge instead of overloading the same green as a second signal. Re-verified visually.
- **This is the single feature most directly aligned with spec §10's pitch-video instruction**
  ("lead with a live break it moment... show the system correctly escalate instead of guessing")
  — it's no longer something to stage by re-running a fixed generated batch, it's a live,
  editable, resubmittable input.

---
