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

- **Known gap (not yet a bug, flagged in advance):** I haven't set a hosted-LLM API key in this environment yet. I'm
  building the agentic discrepancy narrator (spec §6.4) against a real, named hosted LLM API with a clean call interface,
  but with a deterministic mock/fallback mode so I can build and test the rest of the pipeline (chain builder → matching →
  calibration → audit log → dashboard) end-to-end without live LLM calls. Swapping in a real key later should require no
  code changes — only setting the env var. **Status: by design, not a failure — tracking it here so I don't mistake it for
  an oversight later.**

- **Decision — switched the LLM provider I'd originally planned to Groq (Llama 3.3):** my original
  spec named a specific hosted LLM API for the narrator. Mid-build I decided not to treat that
  vendor as load-bearing and to pick whatever's better/cheaper, with my own mandate to
  minimize running cost. Groq's API is OpenAI-tool-call-compatible, supports JSON mode, and its
  free tier is genuinely $0 at hackathon scale. I'm building the narrator behind a provider
  interface so the concrete backend is a one-line swap (`LLM_PROVIDER=mock|groq`, other providers
  could be added the same way later without touching call sites). **Status: architectural
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
  reviewed but not yet run against the real API. I still need to get a key myself (free tier at
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

- **Built:** Vite + React 19 + TypeScript dashboard — run controls, summary tiles, baseline
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

## 2026-08-24 — Real Groq narrator: a real rate-limit failure, handled properly

- **Model name went stale between planning and building:** the spec named
  `llama-3.3-70b-versatile`; by the time a real `GROQ_API_KEY` was available, Groq had retired it
  (`404 model_not_found`). Queried `client.models.list()` rather than guessing a replacement from
  training-data memory, tool-call-tested the three plausible candidates
  (`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.6-27b`) directly, confirmed all three
  support tool calling, and picked `openai/gpt-oss-20b` — cheapest of the three, consistent with
  the cost-minimization mandate.
- **First real batch run hit a real `RateLimitError`** on transaction 4 of an 18-transaction
  narration queue: 8000 TPM limit on this free-tier account. Diagnosed the *exact* cause (not
  guessed) by running the full queue transaction-by-transaction with the raw error surfaced —
  17/18 succeeded, one hit the cap.
  - **Fix:** `_call_with_retry` in `app/narrator/agent.py` — retries `RateLimitError` /
    `APIConnectionError` / `InternalServerError`, honoring the API's own `retry-after` response
    header when present, exponential backoff otherwise, re-raising after 4 attempts. `narrate_groq`
    now also fails safe (escalates as `genuine_error` with an honest reason string) rather than
    crashing the batch if the narrator's JSON response is malformed or the API is unavailable even
    after retries — an LLM producing a bad response is a real runtime failure mode, not a
    hypothetical one, and this is the direct, demonstrable "identify a runtime failure, engineer a
    graceful fallback" story the Failure Recovery criterion asks for.
  - **Verified with fast, free, deterministic tests** (`test_retry.py`, 5 tests) using a fake
    `RateLimitError` rather than re-triggering the real limit: retry-then-succeed, honoring
    `retry-after`, exponential fallback without it, re-raising after exhaustion, and connection
    errors retrying too. All passing alongside the full 45-test suite.

---

## 2026-08-24 — Merkle-tree divergence pre-filter (optional stretch, spec §3/§9)

- **Built:** `app/matching/merkle.py` — a `MerkleComparator` that builds one shared tree shape over
  a sorted key set (so two "views" of the same keys, e.g. ledger vs. settlement amounts, can be
  diffed by direct index-aligned node comparison rather than a general tree-diff algorithm), and
  prunes any subtree whose rolled-up hash matches on both sides.
- **Correctness proven, not assumed:** `test_matches_brute_force_exactly_across_random_trials`
  checks the diverging-key set against brute-force set difference across 20 randomized trials
  (varying size, branching factor, and perturbation rate) — exact match every time. Also
  cross-checked against this project's own real generated batches: hashing
  (ledger_expected_amount, settled_amount) pairs and diffing them finds *exactly* the same
  transactions as `ledger_gap != 0` on the real causal chains.
- **Real measured number for the pitch, not an estimate** (`test_50000_record_scale_demonstration`):
  50,000 keys, 100 deliberately injected mismatches (0.2% divergence, modeling a realistic
  "mostly-clean daily settlement batch") — **3,010 comparisons made vs. 50,000 brute-force
  (94.0% fewer)** to correctly find all 100 divergences. This is the exact spec §3 pitch line
  ("compared 50,000 records using ~200 comparisons") — the real number came out to ~3,000, not
  ~200, and that's what's reported; the point was never to hit a pre-picked number, only to have a
  real one.
- **Honest finding, caught by the test itself:** ran the same comparator against this project's own
  ~120-200 record demo batches and found it provides *no* comparison saving there — the demo batch
  is deliberately ~33% non-clean (spec's own 25% explainable + 10% adversarial + 5% ambiguous
  distribution), and Merkle pruning only pays off when divergence is *sparse*. At 33% divergence,
  most subtrees must be descended into regardless, and internal-node overhead can exceed a flat
  per-leaf scan. Documented this explicitly rather than asserting a saving that wouldn't hold:
  the 94%-fewer-comparisons number is real and belongs in the pitch, but it's honestly a claim
  about realistic-scale, mostly-clean batches — not about this project's own deliberately dense
  demo data. Overclaiming past that line would be exactly the "one cherry-picked match proves
  nothing" failure mode the whole project is built to avoid.
- **Scope decision:** kept this as a standalone, fully-tested, provably-correct utility rather than
  rewiring the already-working matching engine's Pass 1 to route through it. Spec explicitly marks
  this the first thing to cut if behind schedule; building it correctly and proving the real number
  captures the pitch value without adding regression risk to a working, verified pipeline.

---

## 2026-08-24 — Real Groq narrator results (main batch + full stress batch)

First full run against the live API after the retry/fallback hardening above. Seed 42, main_n=120,
stress_n=40, threshold=0.90, model `openai/gpt-oss-20b`. Raw result saved at
[docs/evidence/real-groq-run-2026-08-24.json](docs/evidence/real-groq-run-2026-08-24.json).

- **Elapsed: 665s (~11 minutes)** for 120 + 40 transactions, the large majority of that time spent
  in rate-limit backoff sleeps, not model inference — worth stating plainly rather than implying
  this is fast: a free-tier account processing a batch this size at this narration rate (~15% of
  records reaching the LLM) takes real wall-clock time. A paid tier or a higher-TPM model would
  remove almost all of it; the retry logic is what makes it *finish correctly* either way instead
  of crashing partway through.
- **Main batch (n=120):** 86.0% of batch value (Rs.11,21,046.35 of Rs.13,02,997.38) auto-reconciled
  deterministically; 18 transactions escalated. Per-category real narrator accuracy:
  **duplicate_refund 4/4 (100%), genuine_error 6/6 (100%), netting_trap 8/8 (100%)**. All three
  still show `decision: escalate` at the 90% threshold, and correctly so — Wilson lower bounds at
  n=4/6/8 are 51.0%/61.0%/67.6%, nowhere near clearing 90% regardless of the (real, not mocked)
  100% point accuracy. This is the exact "appropriately conservative by default" behavior
  documented in calibration/history.py, now confirmed with real LLM output, not just mock output.
  **Precision correction (caught by my audit loop below, 2026-08-24):** 17 of these 18 were
  classified via genuine tool-informed reasoning; one (`order_dfba37bc5ff7`, genuine_error,
  confidence 0.0) hit the tool-call round budget and resolved via `_fail_safe`'s "did not converge"
  fallback, which happened to match ground truth. It's correctly counted as accurate — the fallback
  category was right — but the earlier phrasing here ("every single one was correct") implied all
  18 were reasoned through by the model, which overstates the mechanism behind that one case. 17/18
  reasoned, 1/18 safe-fallback-that-happened-to-be-right is the precise claim.
- **Stress scorecard (100% adversarial batch, n=40): 37/37 narrator-classified cases correctly
  handled, 0 wrongly auto-resolved.** This is the real number for the pitch's "break it" claim —
  not the mock-mode number from earlier in this log, which was flagged at the time as not
  representative of actual LLM judgment. This one is.
- **Baseline comparison on the same batch:** naive baseline "clean" count 80/120 (66.7%) vs. this
  system's 102/120 resolved (85.0%) — consistent with the mock-mode lift number reported earlier,
  now confirmed with the real narrator in the loop. 8 false-negative timing_lag cases, 7
  false-positive rounding cases, same blind spots documented earlier, now measured on a real run.
- **Everything in the "v1.1 upgrades" and README's "what's real vs mock" caveats can now be updated
  from "implemented but untested against the live API" to "tested against the live API, numbers
  attached."**

---

## 2026-08-24 — My audit loop, round 1: a serious gap found and closed

This is my own idea: I built an audit loop where an independent AI agent scores this entire project
the way a Razorpay buildathon judge would — reading the spec, README, BUILD_LOG, PROGRESS, every
backend/frontend module, running the test suite itself, and cross-checking specific BUILD_LOG claims
against actual code rather than trusting the narrative. I told it explicitly to be adversarial: find
overclaims and real gaps, don't rubber-stamp. Full findings below; I only narrate in detail the fixes
I actually applied.

**Scores: AI Judgment 7/10, Failure Recovery 8/10, Measured Accuracy 8/10, Throughput 5/10, Bounded
& Gated 5/10, Real Problem 8/10, Submission Readiness 8/10. Overall 71/100.**

### Gap #1 (CRITICAL, fixed) — calibration couldn't tell mock decisions from real LLM decisions

My audit loop empirically demonstrated the single most important finding of this build: 6-7
consecutive **mock-mode** (default, zero-cost, zero-LLM) batch runs through `CalibrationHistory`
crossed the 90% Wilson-lower-bound threshold for `netting_trap` — `auto_resolve` reached with no
LLM ever having been called. `ScoredDecision` and the SQLite `scored_decisions` table carried no
`provider` field, so a deterministic rule-based stand-in built purely for zero-cost testing could
silently satisfy "the AI has proven itself accurate on this category" — directly defeating the
exact claim behind the two most heavily-weighted criteria (AI Judgment, Bounded & Gated).

- **Fix:** added `provider: str` to `ScoredDecision` (calibrator.py) and threaded it through
  everywhere a decision is scored — `pipeline.py`'s `scored_decisions` list (from
  `NarratorOutput.provider`, which already existed), `EscalationItem` (matching/escalation.py, so
  the human-feedback loop knows what it's confirming), `CalibrationHistory`'s SQLite schema and
  `confirm_human_resolution` (history.py), and the `/api/escalations/resolve` endpoint (main.py).
  `calibrate()` now computes accuracy/CI/the auto-resolve decision from **real-provider decisions
  only**; mock decisions are still recorded and reported (`CategoryCalibration.mock_n`) for
  transparency, but categorically excluded from the gate. A category with real_n=0 always escalates
  ("no real-provider decisions yet"), the same posture as insufficient evidence generally.
- **`calibration_history.db` was incompatible with the new schema and already contaminated** (see
  Gap #2) — I deleted it (gitignored, local-only, not real demo history) rather than migrate it.
  **Correction (caught in round 4 of my audit loop, 2026-08-24):** this entry originally claimed I'd
  deleted `audit_log.db` too — I hadn't (no schema change required it). It kept 860 rows of
  the same pre-isolation-fix test contamination Gap #2 describes, undetected until round 4 queried
  the live DB directly. I cleaned it up then: deleted only those contaminated `run_id` groups, preserving
  the 240 rows of genuine evidence (two real batch runs) that had accumulated since.
- **Verified, not just implemented:** rewrote the test that used to prove "mock accumulation
  crosses the threshold" (that was the bug) into
  `test_accumulated_mock_history_never_clears_threshold_regardless_of_volume` — 7 accumulated mock
  batches now correctly produce zero auto-resolve categories, with `n=0`/`mock_n>0` on every one.
  Added `test_accumulated_real_provider_decisions_can_clear_threshold` to prove the gate still
  works for genuine data (`provider="groq"`, directly constructed to avoid spending real API budget
  on a unit test). Added `test_mock_decisions_never_count_toward_the_gate` at the calibrator-unit
  level, including a mixed mock+real case. **Confirmed live in the browser**, not just in tests:
  dragged the threshold dial to 1% with only mock data loaded — every category correctly stayed
  "Escalate," showing "0 (+N mock, not counted)."

### Gap #2 (HIGH, fixed) — the test suite was silently wiping the live demo's accumulated history

`test_api.py` imported `app.main`'s live `calibration_history`/`audit_logger` singletons directly
and called `.clear()` on them in 4 of 5 tests — the exact SQLite files a real dashboard session
persists to. Running `pytest` (the README's own documented verification step) destroyed whatever
accumulated trust or audit history existed from actual demo usage. I found 39 accumulated
run_ids in the real DBs, all shaped like repeated test-batch sizes, with the actual Groq evidence
run's `run_id` entirely absent — the tests had already erased it once.

- **Fix:** added `tests/conftest.py` with an `isolated_app_state` fixture that monkeypatches
  `app.main.audit_logger`/`calibration_history`/`state` to temp-file-backed instances for the
  duration of each test, torn down after. Updated every stateful test in `test_api.py` to use it
  instead of touching the live singletons.
- **Verified:** ran the full suite before/after checking `backend/data/*.db` file size and mtime —
  unchanged. The real Groq evidence data (regenerated after this fix) now survives running `pytest`.

### Gap #3 (MEDIUM, fixed) — Throughput, the one criterion Razorpay names explicitly, had no instrumentation

The 665s real-run figure existed only as hand-typed prose in this log; `BatchRunResult` never
measured wall-clock time, and the evidence JSON had no timing field at all — the one number
without a traceable artifact, for the one criterion literally named "Throughput."

- **Fix:** added `elapsed_seconds` (measured via `time.monotonic()` around generation + chain
  building + matching + narration, main batch only — the stress batch is deliberately excluded so
  the number reflects what a merchant's actual reconciliation run would cost) and `narrated_count`
  to `BatchRunResult`, plus a `transactions_per_second` computed field. Surfaced as a new tile on
  the dashboard.
- **Caught while wiring the computed field:** `@property` alone doesn't serialize on a pydantic
  model — needed `@computed_field`, and the naive `total/elapsed` formula divides by zero when mock
  mode completes in under a microsecond (real, observed: 0.0s on an 80-record batch — timer
  resolution, not a bug). `float("inf")` is not valid JSON and would have broken the frontend's
  `response.json()` parsing with a SyntaxError; floored the divisor at 1e-6 for the rate calculation
  only, while `elapsed_seconds` itself still reports the true measured value including genuine 0.0.
- **Verified:** `test_throughput_is_measured_not_estimated`, plus a direct JSON round-trip check
  confirming no literal `Infinity` token in the serialized response.

### Gap #4 (trivial, fixed) — stale UI copy

`RunControls.tsx` still said "groq (Llama 3.3...)" after the model was switched to
`openai/gpt-oss-20b` earlier the same day. A judge cross-referencing BUILD_LOG against the live
dashboard would have hit a contradiction in the one artifact they touch directly. One-line fix.

### Gap #5 (MEDIUM, fixed) — an overclaim of precision in the "100% accuracy" headline

Of the 18 real-Groq escalations, one (`order_dfba37bc5ff7`, genuine_error) resolved via
`_fail_safe`'s "did not converge within the tool-call budget" path, not genuine tool-informed
reasoning — it only counts as correct because the fallback category happened to match ground
truth. My original phrasing ("every single narrator-classified transaction... was correct")
implied all 18 were reasoned through by the model. I corrected both BUILD_LOG and README to state
17/18 via reasoning + 1/18 via safe fallback — still a materially excellent result, stated precisely
instead of rounded up.

### Gaps not fixed this round, and why

- **Gap #6 (frontend test coverage):** my audit loop correctly noted no committed Playwright spec or
  preserved screenshots back up this log's repeated "screenshot-confirmed" claims. Partially
  addressed in spirit — every fix in this entry was re-verified live in a browser with a fresh
  screenshot before being logged — but a committed, re-runnable spec is still outstanding.
- **Gap #7 (API key hygiene):** confirmed via `git log --all --full-history -- backend/.env` that
  the key was never committed. Noting it plainly for myself: rotate the Groq key at
  console.groq.com before any public push or recorded pitch video, since it was shared in plaintext
  during this session.
- **Gap #8 (`recall_similar_resolutions` is per-run only, unlike calibration which now accumulates
  across runs):** already honestly disclosed in PROGRESS.md as an in-memory-per-run limitation.
  Lower priority than the six items above; left as a known, disclosed gap rather than rushed.

---

## 2026-08-24 — My audit loop, round 2: verified the fix, found the fix hadn't actually shipped

I ran round 2 of my audit loop — a fresh agent with no shared context from round 1 beyond what's in
this log — and told it specifically to verify round 1's "fixed" claims rather than trust them, and to
try to break the provider-aware calibration fix directly.

**Score: 79/100, up from round 1's 71** (AI Judgment 8, Failure Recovery 8, Measured Accuracy 9,
Throughput 7, Bounded & Gated 8, Real Problem 8, Submission Readiness 7).

**The fix itself held under direct attack.** My audit loop built its own adversarial probe — 29 mock
batches, then 522 human-feedback-loop resolutions via `confirm_human_resolution`, always confirming
the model "correct" (the best case for an attacker) — and confirmed every category still correctly
shows `decision="escalate"`, `n=0`. It also fuzzed `narrate(provider=...)` with near-miss strings
(`"Mock"`, `"MOCK"`, `"openai"`, other near-miss vendor-name strings, `"fake"`) and confirmed every
one raises `ValueError` rather than being silently treated as real, real-provider is not a magic bypass.

**But: the real Groq run's data never actually reached the live system's persistent state.** The
2026-08-24 "real Groq narrator results" run above called
`run_batch(seed=42, ..., provider="groq")` **without** passing `calibration_history=` — meaning it
used the in-memory-only `calibrate()` fallback, not the persistent `CalibrationHistory` the actual
dashboard reads from. I confirmed this by querying `backend/data/calibration_history.db`
directly: 100% `provider="mock"` rows, zero `provider="groq"`, and by cross-matching stored
confidence values against the evidence JSON's own transaction IDs (the DB held the mock narrator's
hardcoded 0.3/0.85/0.9 confidences, not the real run's 0.0/0.95/0.95). The real evidence existed as
a static JSON file and BUILD_LOG prose — genuinely real, but never actually accumulated into the
running system's own state, which is where the "trust builds live" pitch moment is supposed to
happen. A subtle but real gap: proving the mechanism is safe is not the same as proving the
mechanism has real evidence sitting in it right now.

**Also found:** `docs/evidence/real-groq-run-2026-08-24.json` predates the throughput fields
(elapsed_seconds/narrated_count/transactions_per_second) added later the same day, so the one
real-provider run has no structured timing data, only the hand-typed "665s" in this log. README
still said "45 tests" after the suite grew to 50 (same failure class Gap #4 already caught once,
recurring elsewhere — a pattern worth watching for, not just patching each instance). No committed
test exercised the resolve-loop-at-volume scenario I'd had to construct ad hoc for this round.

**Fixes applied:**
- Re-ran the real Groq batch (seed 99, new seed so it's not a replay) with `calibration_history=`
  and `audit_logger=` explicitly pointed at the real `backend/data/*.db` paths — this time the real
  narrator's decisions land in the actual persistent state the dashboard queries, not just a side
  JSON file. See the numbers below.
- Corrected README's test count (45 → 50).
- Added `test_resolving_many_mock_escalations_over_http_cannot_graduate_a_category` to
  `test_api.py` — a permanent, lighter-weight version of the ad hoc adversarial probe from this
  round, run over real HTTP through `/api/escalations/resolve`, not just at the calibrator-unit level.
- **Still not actioned: rotating the Groq API key.** I flagged it in round 1, still hadn't rotated it
  by round 2. This is a real-world action only I can do outside the codebase — restating it plainly
  rather than letting it quietly age into a third round: **rotate the key at console.groq.com before
  any public push or recorded pitch video.**

---

## 2026-08-24 — Real Groq run #2: properly persisted, and a more credible result than a clean sweep

Seed 99 (new seed, not a replay), main_n=120, stress_n=40, threshold=0.90, `openai/gpt-oss-20b`,
this time with `calibration_history=`/`audit_logger=` explicitly pointed at the real
`backend/data/*.db` paths. Raw output:
[docs/evidence/real-groq-run-2026-08-24b-persisted.json](docs/evidence/real-groq-run-2026-08-24b-persisted.json).

- **Wall-clock time: 4,174s (~70 minutes) for the full run; the measured `elapsed_seconds` field
  (main batch only, by design) came out to 179.1s.** The gap is almost entirely the stress batch's
  34 narrated transactions hitting much heavier rate-limit retries than the main batch did — a
  real, worth-stating cost of running a stress-batch-sized narration load against a free-tier
  account, not a measurement bug. `elapsed_seconds` intentionally excludes the stress batch (see
  its docstring) so the reported throughput number reflects what a merchant's actual reconciliation
  run would cost, not the extra load this project's own testing adds on top.
- **Per-category real accuracy this run: duplicate_refund 4/4 (100%), genuine_error 6/7 (85.7%),
  netting_trap 7/7 (100%).** Unlike the first real run (all three at 100%), this one has a genuine
  miss — and it's worth narrating exactly, not rounding away:
  - **The one miss was itself a real API failure, correctly handled by the existing fail-safe, that
    happened to guess wrong.** `order_bf02a69cada6`'s true label is `netting_trap`; the narrator's
    final response came back empty (`_parse_json_response` correctly raised, `_fail_safe` correctly
    engaged) and defaulted to `genuine_error` per the fail-safe's design. That default was wrong
    this time (round 1's equivalent fail-safe case happened to be right). **The important property
    held regardless: the fail-safe always defaults to the one category that can never auto-resolve
    — a real narrator failure produced a wrong classification, but not a wrong autonomous action.**
    This is a cleaner demonstration of "Failure Recovery" than a clean sweep would have been: a
    real failure occurred, and the system's designed-in safety margin (escalate, don't guess an
    auto-resolvable category) is exactly what absorbed it.
- **Stress scorecard: 34/34 narrator-classified cases correctly handled, 0 wrongly auto-resolved**
  (6 more resolved deterministically) — consistent with the first real run's clean stress result,
  on an entirely different random batch.
- **Calibration now genuinely reflects both real and mock evidence, separated correctly:**
  `duplicate_refund n=4 (real) + mock_n=8`, `genuine_error n=7 (real) + mock_n=9`,
  `netting_trap n=7 (real) + mock_n=10` — all still `escalate` at the 90% threshold, correctly,
  since Wilson lower bounds at n=4/7/7 don't clear it regardless of point accuracy. This is now the
  actual live state a judge could query through the running dashboard, not just a side file.
- **The two real runs together are a better pitch artifact than either alone:** one clean sweep,
  one with a real, honestly-narrated miss caused by an actual API hiccup and correctly contained by
  the fail-safe design. That combination is more credible under scrutiny than a suspiciously
  perfect 100% would have been on its own.

---

## 2026-08-24 — My audit loop, round 3: no critical/high findings, score 84/100

I ran round 3 of my audit loop, again told to verify rather than trust, with an explicit instruction
to hold this round to a *higher* bar for polish, not a lower one, given it had already been reviewed
twice. It re-derived every load-bearing claim itself rather than reading BUILD_LOG's retelling:
regenerated `seed=99` independently and confirmed the "one honest miss" transaction's true label
really is `netting_trap`; queried the live `CalibrationHistory` directly and confirmed it matches
the evidence file exactly (18 `groq`-tagged rows, all `source='batch'`, zero cross-contamination
from human-confirmed resolutions); queried the audit log directly and confirmed the failure
signature text matches the exact code path in `agent.py`; timed `backend/data/*.db` mtimes
before/after a full test run and confirmed byte-identical (the isolation fix still holds); reran
the new adversarial regression test standalone and confirmed it produces 75 real escalations across
5 seeds, not a vacuous pass.

**Score: 84/100, up from round 2's 79** (AI Judgment 8, Failure Recovery 9, Measured Accuracy 9,
Throughput 8, Bounded & Gated 9, Real Problem 8, Submission Readiness 7).

**Explicit stop/continue signal from this round: no CRITICAL or HIGH finding, this could be the
final round.** Everything structurally important — provider-aware gating, real persistence, the
honest-miss failure-recovery story, test isolation, the new regression test — independently
re-derived and held exactly as claimed. The only findings were documentation-accuracy nits, the
same class of bug as an already-fixed round-1 item (stale UI copy) recurring in new spots:

- README.md and PROGRESS.md both said "50 tests" after the suite grew to 51 (a *third* recurrence
  of this exact failure class — round 1 caught it in UI copy, round 2 in a test count, round 3 in
  the same test count again after it moved again).
- PROGRESS.md's Agentic-layer checklist line still only described the first real Groq run, not the
  second.
- docs/track04-settlement-reconciliation-copilot.md §7 still named the LLM vendor I'd originally
  planned to use, not Groq (only §6.5 had been updated when I made the switch).
- README.md/BUILD_LOG.md said "React 18"; the actual installed version (`frontend/package.json`) is
  React 19 — this one was wrong from the initial scaffold, not something that changed later.

**Fixed all four this round** (test counts, the Agentic-layer summary, the spec doc's tech stack
line, both React version mentions) rather than patching one instance and letting a fourth
recurrence happen. **Still not actioned: the Groq key rotation** — flagged in all three rounds now,
a real-world action only I can do, restated again below rather than dropped.

**Pattern worth naming plainly:** every round's *mechanism* findings (calibration gating, test
isolation, failure recovery) have been real, fixed, and held under a fresh independent adversarial
check each time. Every round's *documentation* findings have been the same failure mode recurring
in a new location — a number or version string stated once and not kept in sync as the codebase
moved. The fix this round is the same as the last two: find every instance via a repo-wide search
before calling a round done, not just the one instance a reviewer happened to point at.

---

## 2026-08-24 — My audit loop, round 4: found something real, score 83/100 (an honest dip)

I ran round 4 of my audit loop, explicitly instructed to hold this round to a *higher* bar (a fourth
review, not a first look) and to search surface area the first three rounds hadn't specifically
targeted: full frontend render-paths (not just the calibration/summary components), the live
persisted DB's *raw* contents via direct SQL, the installed toolchain's actual declared
requirements, and a systematic re-derivation of every specific number claimed anywhere in the docs
rather than just the ones already fixed once.

**Score: 83/100, down 1 from round 3's 84 — an honest result, not an error.** Round 4 was
explicit that this reflects genuinely different surface area being examined at higher resolution,
not a regression. Five findings, all real:

1. **(Medium, fixed) The tool-call trace — the architecture doc's own headline proof of "AI
   Judgment" — was captured correctly end-to-end but never rendered anywhere a person could see
   it.** `AuditLogView.tsx` fetched `tool_calls_json` in every row and never touched it;
   `BreakItPanel.tsx`'s `EvaluatedTransaction.tool_calls` was correctly typed and never rendered
   either. The spec's own words (§3.3): *"the tool-call trace is shown alongside the verdict...
   not an architecture-diagram claim"* — in the shipped product, it effectively was exactly that,
   until now. **Fix:** added an expandable "N tool calls" toggle per row in `AuditLogView.tsx`
   (parses `tool_calls_json`, renders `{tool, arguments, result}`) and a `<details>` block under
   each Break-It result. **Verified live in the browser:** 18 of 120 audit rows (exactly the
   narrator-classified ones) now show a working toggle; expanding one shows the real
   `check_batch_anomalies`/`recall_similar_resolutions` trace with actual arguments and results,
   not a placeholder. Deterministic rows correctly show "—" (they never called a tool). Zero
   console errors.
2. **(Medium, fixed) README's stated "Node 18+" doesn't satisfy the installed Vite's own declared
   minimum** (`node_modules/vite/package.json`: `"node": "^20.19.0 || >=22.12.0"`) — a real
   reproducibility gap against spec §10's own checklist item, undetected for 3 rounds because every
   sandbox this was tested in already happened to have a compliant Node. **Fix:** corrected to
   "Node 20.19+ (or 22.12+)."
3. **(Low-medium, fixed) The real narrator's own system prompt told the live LLM it could output
   `currency_rounding`** — structurally impossible, since `matching/engine.py`'s Pass 2 always
   catches any `abs(settlement_delta) <= ROUNDING_EPSILON` deterministically before a transaction
   ever reaches "needs_narration", and every category that does reach the narrator injects a delta
   an order of magnitude larger than that threshold by construction. Verified this is genuinely
   unreachable (checked every narrator-category generator's injected delta size against
   `ROUNDING_EPSILON`) before removing it, rather than just assuming round 4's finding was right. **Fix:**
   dropped `currency_rounding` from `NARRATOR_CATEGORIES` and the system prompt's output list,
   added it to the "already resolved, don't output" sentence, updated `test_narrator.py`'s expected
   set.
4. **(Low, fixed) Two per-file test-count claims in PROGRESS.md were wrong — and had been wrong
   since the day they were written, missed by all three prior "fix the stale count" passes.**
   `test_matching.py` was claimed "18/18"; it has always had 6 tests. `test_pipeline.py` was
   claimed "7/7"; it currently has 10. Root cause of *why* three rounds of grepping missed this:
   each prior fix was keyed to the one number a reviewer had just flagged (45→50, 50→51,
   React 18→19), never to systematically re-collecting every count claim against
   `pytest --collect-only` file-by-file. Did that this time: collected all 8 test files' counts
   individually (6, 6, 8, 3, 10, 8, 5, 5 — sums to 51, matching the total) and corrected both wrong
   lines.
5. **(Low, fixed) `audit_log.db` still held 860 rows of the exact pre-isolation-fix test
   contamination Gap #2 was about**, undetected because round 1's fix entry claimed both
   `audit_log.db` and `calibration_history.db` were deleted — only the schema-incompatible
   `calibration_history.db` actually was. Verified directly which `run_id` groups were real evidence
   vs. contamination (the 8 contaminated groups are sized exactly 100/120/150/60, in two clusters
   seconds apart, matching the old `test_api.py` batch sizes before `conftest.py` existed) before
   deleting anything. Deleted only the 8 contaminated `run_id` groups (860 rows), preserving the 240
   rows of genuine evidence from two real batch runs that had accumulated since. Corrected the
   round-1 BUILD_LOG entry to state precisely what was actually deleted then vs. now.

**All five fixed and re-verified** (51/51 tests still passing, `npm run build` clean, tool-call
rendering confirmed live in a real browser).

**Honest assessment from this round, worth carrying forward rather than editing out:** fixing
these five was estimated to land around 87-90, not 95 outright. Throughput and Real Problem are
already close to an honest ceiling — free-tier rate-limit-dominated wall-clock time and a Merkle
pre-filter that genuinely provides no saving on this project's own dense demo-batch distribution
are disclosed, real limitations, not defects; pushing those scores higher would require
overclaiming past what's actually true, which is exactly what this project's entire philosophy has
refused to do at every prior decision point. Continued rounds may oscillate rather than climb
monotonically — noted here so a future round isn't surprised by that, or tempted to manufacture a
finding just to justify another point of movement.

---

## 2026-08-24 — My audit loop, round 5: the most significant finding of the whole loop

I ran round 5 of my audit loop — my own target for this loop is 95/100 (I'm excluding the video and
unpushed-repo status from scoring throughout). It confirmed round 4's tool-call-trace fix is genuinely
correct — not just by reading the code, but by installing playwright-core, starting both dev
servers, and driving the live app in a headless browser to watch a toggle actually expand real
data. Then it found something rounds 1-4 all missed.

**Score: 72/100 — a real, warranted drop, not variance.** (AI Judgment 6, Failure Recovery 9,
Measured Accuracy 9, Bounded & Gated 5, Throughput 6, Real Problem 7, Submission Readiness 8.)

### THE FINDING: the auto-resolve gate checks category membership, never the specific decision's own provider

`pipeline.py`'s `_final_decision()` — the function that actually decides `auto_resolved_calibrated`
vs. `escalated` for a live transaction — only ever checked `narrator_category in
auto_resolve_categories`. `auto_resolve_categories` correctly reflects the accumulated REAL-provider
history for a category (round 1's fix, still solid). But nothing then checked whether **this
specific transaction's own classification** — the one actually being decided on right now — came
from a real provider before letting it ride on that category's earned trust. The identical gap
existed in `_stress_scorecard()`'s equivalent check and in `/api/transactions/evaluate`'s call site.

**Round 5 proved this live**, not theoretically: it seeded 40 real (`provider="groq"`)
all-correct `netting_trap` decisions into a fresh `CalibrationHistory` (crossing the threshold,
exactly the way `test_accumulated_real_provider_decisions_can_clear_threshold` already
demonstrates it should), then ran the real, unmodified `run_batch(provider="mock", ...)` against
that same history — and read back a real, persisted audit row showing a **mock-classified**
transaction silently marked `auto_resolved_calibrated`, absent from `result.escalations`, with zero
real LLM ever consulted for that specific transaction.

**Why this is worse than the round-1 gap it's adjacent to:** round 1's bug meant mock evidence
could never *earn* trust. This bug meant that once a category legitimately *had* earned trust
(exactly the intended, celebrated end-state — "watch it cross the threshold live"), every
*subsequent* mock-mode run's guess in that category would silently ride on trust it never itself
earned. It falsifies "only auto-resolves categories it has proven itself accurate on" at the
per-decision level, using the default provider (`mock` is `RunControls.tsx`'s default selection),
reachable through entirely ordinary use — not an adversarial trick, just the natural next step of
using the system as designed for long enough. It was latent (not yet visibly triggered) in today's
live demo state only because no category had crossed the threshold yet — the design defect was
real regardless of whether today's specific data happened to expose it.

**Fix:** threaded `output.provider` (the specific decision's own provider, not the category's
accumulated history) into `_final_decision()` — now requires `narrator_category in
auto_resolve_categories AND narrator_provider != "mock"` — and the equivalent check in
`_stress_scorecard()`. Updated both call sites (`pipeline.py`'s `run_batch()`,
`main.py`'s `/api/transactions/evaluate`).

**Verified the fix catches the bug, not just that it compiles** — the same rigor applied to every
fix in this loop: wrote `test_provider_gate_applies_per_decision_not_just_per_category`
(reproducing round 5's exact scenario), confirmed it **passes** with the fix in place, then
temporarily reverted the one-line condition back to the buggy version and confirmed the test
**fails** (`assert result is not None` — without the fix, mock-classified transactions in a
trusted category never appear in escalations at all, so the test correctly can't find one to
check). Restored the fix immediately after. 53/53 tests passing.

### Two legitimate, non-overclaiming improvement levers (also from round 5)

Round 5 pushed back on round 4's "~87-90 ceiling without overclaiming" — correctly on the
reported-number honesty, but round 5 argued round 4 conflated "honest" with "already optimized."
Two real levers existed and hadn't been pulled:

- **Throughput: the mock-mode rate display could render a nonsensical number** (observed live:
  "120000000.0/s") when `elapsed_seconds` rounds to ~0. Not a lie (the floor-division guard exists
  specifically to avoid serializing `Infinity`), but absurd on its face in a project about not
  overclaiming. **Fix:** `SummaryTiles.tsx` now shows "instant (mock — no network calls)" below a
  sane threshold instead of computing a rate against a near-zero denominator.
- **Real Problem: the deterministic engine's own ~85%-with-zero-LLM-calls contribution (documented
  in prose since 2026-08-23) was never surfaced as its own number** next to the naive baseline and
  the full system — meaning the pitch could show total lift, but not decompose how much of it is
  "good deterministic engineering" vs. "the agentic layer specifically." **Fix:** added
  `deterministic_only_resolved_count`/`deterministic_only_amount_reconciled` to `BatchRunResult`
  (computed directly from `match_results`, before the narrator or calibration are ever involved —
  can't be influenced by their behavior even by accident), a third bar in
  `BaselineComparison.tsx`, and `test_three_way_decomposition_isolates_what_the_narrator_adds`
  proving the ordering `naive <= deterministic-only <= full system` holds and that the
  deterministic engine alone already meaningfully beats the naive baseline.
  - **UX catch during my own live verification, not something round 5 flagged:** at the current live demo state
    (no category has crossed threshold yet), the narrator's own contribution renders as a flat
    "+0.0%", which reads as "the agentic layer does nothing" to anyone who doesn't already know why.
    Fixed the copy to explain the mechanism honestly when this happens ("hasn't auto-resolved
    anything *yet*... watch this number become positive as trust is earned") rather than let a
    correct-but-uncontextualized zero look like a dead feature.

### Honest reassessment of the path to 95

Round 5's own estimate: fixing the provider-per-decision gap alone should restore AI Judgment to
~8-9 and Bounded & Gated to ~9 (worth roughly +10, landing near 82); the two levers above add a
few more points each (Throughput → ~8, Real Problem → ~9), landing in the mid-to-high 80s.
Round 5 was explicit that reaching 95 from there is not just "keep finding bugs" — it named a
concrete, real remaining constraint: Throughput's ceiling below 9-10 is the free-tier TPM budget
itself (raising it means a paid tier — a real cost trade-off I'd already decided to minimize,
not a code fix), and Real Problem's Merkle-provides-no-saving disclosure is a correct, permanent
feature of this project's own chosen demo distribution, not a defect to be engineered away.
**This tension — a hard 95 target vs. two genuinely externally-bounded categories — I'm reporting
here plainly rather than resolving unilaterally; it needs a decision from me on how to proceed, not
a 6th round manufacturing findings to force the number up.**

---

## 2026-08-24 — Pivoted the narrator's throughput ceiling entirely: local inference via Ollama

Round 5's own honest ceiling assessment named the free-tier token budget as Throughput's
irreducible limit *if a hosted API stays the constraint*. Instead of accepting that ceiling or
paying for a higher tier (I'd already decided early on to minimize running cost), I decided to
survey every alternative, including local inference, before settling.

### The provider survey

Checked, against each provider's own official docs where possible (not just aggregator blogs,
several of which disagreed with each other and with the provider's own pages — see the Cerebras
entry below for a concrete case of that mattering):

| Provider | Real limit found | Disqualifying issue |
|---|---|---|
| Groq (original) | 8,000 TPM | Hit directly — the reason this survey started |
| Cerebras | 5 RPM / 30K TPM | **Not actually permanently free** — the "$5 credit" requires a verified payment method and expires in 30 days (official docs, not a blog claim) |
| DeepSeek | 500-2,500 *concurrent connections*, not RPM/TPM | **Not permanently free** — 5M tokens is a one-time signup grant, then payment required |
| Gemini 2.5 Flash | 10 RPM, 250 req/day | Genuinely free and permanent, but still RPM-bound the same way Groq/Cerebras are |
| GitHub Models | ~10 RPM, 50-150 req/day | Same class of ceiling; explicitly documented as "prototyping only" |
| SambaNova | 20 RPM, **20 requests/day total** | Permanently free, no card — but the daily cap alone is smaller than one narration queue |
| OpenRouter (`:free` models) | 20 RPM, 50/day unfunded | The workable 1,000/day tier requires **$10 of historical spend** — not actually free-forever |
| Mistral (Experiment tier) | ~1B tokens/month, RPM unpublished | Explicitly framed by Mistral as "for prototyping, not for running a real product" |
| Zhipu GLM-4.7-Flash | ~1 req/sec, ~1,000 req/day | Genuinely permanent, no card, praised specifically for tool-calling reliability — the strongest *hosted* candidate found |
| Cloudflare Workers AI | 10,000 "Neurons"/day | Custom billing unit, unclear per-request cost, added integration complexity for uncertain benefit |

Every hosted option shares the same structural problem for this project's access pattern (many
small sequential tool-calling requests per transaction): they're all metered by RPM, TPM, or a
daily cap, and our narrator makes 1-4 requests per transaction across 18-55+ transactions a batch.
GLM-4.7-Flash was the best of the hosted options — but "best hosted option" still means an external
dependency, a rate limit, and (for a live pitch recording) a real risk of the API being slow or
down at exactly the wrong moment.

### The actual fix: don't use a hosted API at all

Installed Ollama (`winget install Ollama.Ollama`), pulled `qwen2.5:7b-instruct` (~4.7GB,
GPU-tool-calling-tested against three candidate models before committing — see the earlier probe
in this log's "smoke test" style, repeated here for Ollama), confirmed GPU acceleration
(`ollama ps` reports 100% GPU on this machine's AMD Radeon RX 9060 XT — AMD+Windows local-LLM
support turned out to work cleanly via Ollama's own backend, contrary to the higher risk assumed
before checking).

Implemented `narrate_ollama` in `app/narrator/agent.py`, mirroring `narrate_groq`'s structure with
two real, verified API differences (checked directly against the `ollama` package's types, not
assumed): `ToolCall.function.arguments` is already a parsed `dict`, not a JSON string needing
`json.loads()`; tool-result messages have no `tool_call_id` to correlate against (Ollama matches by
message order). Generalized `_call_with_retry` (previously hardcoded to Groq's three exception
types) to accept an explicit `retry_on` tuple, and gave `narrate_ollama` its own
(`ollama.RequestError`, `ollama.ResponseError`, `httpx.ConnectError`, `httpx.TimeoutException`) —
**caught before shipping** that `httpx.ConnectError` does not subclass the builtin
`ConnectionError` (verified via `.__mro__` directly rather than assumed; would have silently
skipped the retry path on "ollama serve isn't running" otherwise). Renamed `GROQ_TOOL_SCHEMAS` to
`TOOL_SCHEMAS` since the OpenAI-style function-calling schema is genuinely shared across both
providers, not Groq-specific.

**Real results, run twice:**
- Isolated narration queue (18 transactions): 94.4% accuracy (17/18), 53.9s (~3.0s/txn avg).
- Full pipeline (`run_batch`, 120 main + 40 stress, 55 narrated total): 148.9s wall clock, 86.0%
  auto-reconciled, calibration `duplicate_refund` 4/4, `genuine_error` 6/7, `netting_trap` 7/7,
  stress `37/37` correctly handled, `0` wrongly auto-resolved.

**Versus the real Groq runs logged earlier this same day: 11-70 *minutes* for the same shape of
workload.**

**Precision correction (caught by round 6 of my audit loop below, 2026-08-24): the sentence
that used to sit here claimed the single miss "carries the identical honest safe-fallback signature
already documented for both Groq runs" — confidence 0.0, not a confident wrong guess. That was
false, and it was checkable false: round 6 queried the live `calibration_history.db`
directly and found the actual row was `provider=ollama, predicted_category=timing_lag`, not
`genuine_error`, at **confidence 0.9**. `timing_lag` is not one of the three categories the narrator
is allowed to output — it's resolved deterministically before a transaction ever reaches the
narrator, and the system prompt says so explicitly. The model produced syntactically valid JSON
with a semantically invalid category, and nothing downstream of the JSON parse checked it against
`NARRATOR_CATEGORIES` before this. See the "Category validation" entry below for the fix. This
correction is left in place rather than silently edited away, same as the precedent earlier in this
log (the confidence-0.0 Groq case at line ~412) — the point of this file is the honest record,
including this one being wrong the first time.

### A real hang, chased carefully, that turned out not to exist

Driving a full `provider=ollama` batch run through the actual browser (not the CLI) appeared to
hang — no response after 270+ seconds, `ollama ps`'s keep-alive counter counting down instead of
refreshing (looked like idle, not active work). Rather than assume and patch, reproduced narrower
and narrower:
1. `narrate_ollama` called in a loop from inside a `concurrent.futures.ThreadPoolExecutor` thread
   (mimicking FastAPI's execution model) — **succeeded**, 48.3s for 18 transactions.
2. The exact `run_batch(..., audit_logger=..., calibration_history=...)` call the real API endpoint
   makes, from the same kind of thread — **succeeded**, 150.4s.
3. A plain `curl -X POST /api/run` directly against the real running FastAPI server, bypassing the
   browser and Playwright entirely — **succeeded**, 141.0s, HTTP 200.

Step 3 settled it: the backend was never hung. The earlier reading of `Get-Process`'s low CPU-time
accumulation as evidence of a stall was a **misdiagnosis** — an I/O-bound process waiting on GPU
inference over HTTP legitimately shows low CPU time while genuinely working; that's expected, not
suspicious. The real lesson banked here, not just the specific bug: verify a suspected hang against
the simplest possible reproduction (raw `curl`) before trusting a richer, harder-to-instrument one
(a full browser session), and don't let "it's slower than I expected" become "it's broken" without
a control to compare against.

### A genuine architecture attempt, measured, and reverted honestly

Given a 141-150s synchronous request is fragile regardless of whether it was ever truly hung, and
wanting to make the architecture faster, I implemented concurrent narrator
dispatch: a `ThreadPoolExecutor` in `_process_batch` (`pipeline.py`) running up to 4 narration
calls in parallel, since each call is I/O-bound (waiting on a response), not CPU-bound.

**Measured it before keeping it — and it didn't help.** Re-ran the identical batch (seed 42, same
sizes): wall clock **156.1s, not faster** than the 148.9-150.4s sequential baseline. `ollama ps`
during the run showed no change in the model's GPU-serialization behavior — a single GPU-resident
model instance processes one request at a time regardless of how many are dispatched
client-side, so parallel dispatch just meant several idle Python threads waiting on the same
serial queue, not genuine overlap.

**Worse: it introduced a real, if modest, correctness cost.** `genuine_error` accuracy dropped to
66.7% (6/9) from 85.7% (6/7) on the identical seed. Root cause, not guessed: `recall_similar_resolutions`
reads `context.audit_log`, which is appended to as each narration completes — under concurrent
dispatch, "prior resolutions so far in this run" becomes genuinely order-dependent (a transaction
dispatched later can finish first and be visible to one dispatched earlier, or vice versa),
changing what evidence the model sees for borderline classifications between runs of the identical
input. GIL semantics mean this never *corrupts* data (`list.append` stays atomic), but it does mean
this tool's answer is a non-deterministic snapshot under concurrency in a way it isn't sequentially.

**Reverted, not kept "just in case."** No speed benefit and a real (if small-sample) accuracy cost
is not a trade worth making, and the same discipline I've applied to every audit-loop finding in
this log — measure before believing an idea, find the real numbers rather than assume the
improvement it makes — applies exactly the same to a change I made myself. The correct,
honest conclusion: the sequential design was already right for a single-GPU local deployment; the
actual win here was replacing the *provider* (hosted, rate-limited → local, unlimited), not
concurrency within it. If a future session wants to revisit real parallelism, the sanctioned lever
is Ollama's own `OLLAMA_NUM_PARALLEL` server-side batching setting, not client-side threading — not
attempted here given the same audit_log order-dependence would still apply, and the first attempt
already showed the GPU itself is the bottleneck, not request dispatch.

### What's shipped

- `ollama` added to `requirements.txt`, `narrate_ollama` + generalized `_call_with_retry` in
  `agent.py`, `"ollama"` added to the `narrate()` dispatcher and the frontend's provider dropdown
  (`RunControls.tsx`) — labeled as the recommended option, `mock` kept as the UI default so the
  dashboard still works with zero setup for anyone who hasn't installed Ollama.
- New tests in `test_retry.py` proving the generalized `retry_on` parameterization is real: a
  custom, non-Groq exception is retried when explicitly passed, and — the property that would have
  silently broken narrator_ollama's retry path if this generalization were ever done carelessly —
  is *not* caught by the default (Groq-only) exception tuple. 56/56 tests passing.
- README.md rewritten to recommend Ollama first, with the Groq path kept as a documented
  alternative rather than removed.

---

## 2026-08-24 — My audit loop, round 6: the narrator's category output was never validated

I ran round 6 of my audit loop, same brief as every round: verify, don't trust prior claims. Given the
Ollama pivot above I told it specifically to check whether round 5's provider-gate fix generalizes
to the new code path — it does — and to independently check the 94.4%/~150s claim against real
evidence, since it's now the project's headline number.

**Score: 70/100.** (AI Judgment 8/10, Failure Recovery 6/10, Measured Accuracy 6/10, Bounded &
Gated 7/10, Throughput 8/10, Real Problem 8/10, Submission Readiness 6/10 — weighted: 16.0 + 12.0 +
9.0 + 10.5 + 8.0 + 8.0 + 6.0.) A real drop from round 5's post-fix estimate ("mid-to-high 80s"),
for a legitimate reason: the Ollama pivot opened a genuinely new gap round 5 never had to face.

### THE FINDING: a category the narrator isn't allowed to output sailed through as a confident answer

`NARRATOR_CATEGORIES = ("duplicate_refund", "netting_trap", "genuine_error")` in `agent.py` was
enforced **only as a prompt instruction** — nothing checked `parsed["category"]` against it on
either real provider's success path. Round 6 proved this wasn't theoretical by querying the
live `calibration_history.db` directly and finding a real row: `provider=ollama,
predicted_category=timing_lag, confidence=0.9`, transaction `order_dfba37bc5ff7`, from run
`9eabac8d-83c4-4fbf-8bd3-e684f4ccd45b`. `timing_lag` is resolved deterministically before a
transaction ever reaches the narrator — the system prompt says so explicitly — so this was a real
model hallucination that the pipeline had no code-level defense against. It happened to escalate
rather than auto-resolve only because no category named `timing_lag` had ever accumulated enough
history to clear the Wilson threshold, not because anything caught the error itself. Worse: it
directly contradicted a specific claim already in this log and in README.md, that the run's one
miss "carries the identical honest safe-fallback signature" (confidence 0.0) — the real row was a
confident 0.9, not a safe fallback. That correction is left in place a few sections up, not edited
away.

### The fix

Added the same allowlist check both real providers' success paths were missing, in `agent.py`:

```python
if parsed.get("category") not in NARRATOR_CATEGORIES:
    return _fail_safe(f"Narrator returned a category outside the valid set: {parsed.get('category')!r}")
```

immediately after the existing malformed-JSON check, in both `narrate_groq` and `narrate_ollama` —
same shape as the fail-safe that already handles a JSON parse failure, so an out-of-schema category
now escalates as an honest `genuine_error`/confidence 0.0 instead of sailing through. Two new tests
in `test_narrator.py` mock each provider's client to return exactly this payload
(`{"category": "timing_lag", "confidence": 0.9, ...}`) and assert the fail-safe fires. Verified
load-bearing the same way round 5's fix was: stripped the check, confirmed both new tests fail
against the real live bug's exact payload, restored it, confirmed they pass. 58/58 tests passing.

Also fixed, all flagged in the same round: `backend/.env.example` didn't mention `ollama` as a
provider option (the same "stale doc in a new location" failure class that's recurred nearly every
round — UI copy, test counts twice, Node version, now this); a stale code comment still listing only
`"mock" | "groq"` on `NarratorOutput.provider`.

### Evidence cleanup and a real, committed Ollama evidence file

Deleted exactly the two contaminated rows this one bad decision produced — `scored_decisions` id
324 in `calibration_history.db`, `audit_log` id 2168 in `audit_log.db` — identified precisely by
`transaction_id`/`run_id`/`category` match, same precision-cleanup precedent as rounds 1 and 4
(240 genuine rows preserved then; nothing else touched now). Confirmed the live
`CalibrationHistory.report()` now shows exactly the 3 valid categories, no phantom fourth row.

Round 6 also flagged (HIGH) that unlike both Groq runs, no real Ollama run had ever been dumped
to `docs/evidence/` — the 94.4%/~150s claim rested on log prose alone. Fixed by running a real
`provider="ollama"` batch wired to the live DB objects (`app.main`'s actual `audit_logger`/
`calibration_history`, not fresh ones — the exact mistake round 2 caught and fixed), same seed=42
as every other documented Ollama number:
[`docs/evidence/real-ollama-run-2026-08-24.json`](docs/evidence/real-ollama-run-2026-08-24.json).

**Real numbers from this run, cross-checked against ground truth directly, not just trusted from
the dashboard:** 17/18 (94.4%) on the main narration queue, 50.75s for that queue (~2.8s/txn) —
reproducing the previously-logged figures exactly, now with a citable artifact behind them. The
one miss this time (`order_6f26b6e3d4da`, predicted `genuine_error`, true `netting_trap`) really
does carry confidence 0.0 — a genuine safe fallback, the claim that was false for the *previous*
run is true for *this* one. And **the exact transaction that hallucinated `timing_lag` last time,
`order_dfba37bc5ff7`, now resolves correctly to `genuine_error` at confidence 1.0** — not
necessarily proof the new validation code path fired (the model may simply have answered correctly
this time; LLM sampling isn't fully deterministic even at a fixed data seed), but a clean,
concrete, real demonstration that this exact previously-problematic case is healthy now, with a
code-level backstop in place either way. Stress batch: 37/37 correctly handled, 0 wrongly
auto-resolved. Live accumulated calibration state at time of writing (not this run alone —
`CalibrationHistory` accumulates across every real run in this session, by design): `duplicate_refund`
n=15 100% accuracy but Wilson lower bound 79.6% (still escalates — small-N conservatism working as
intended), `netting_trap` n=28 100% accuracy, Wilson lower bound 87.9% (still just under 90%, still
escalates), `genuine_error` n=28 82.1% accuracy (always escalates regardless, by design). Nothing
auto-resolves yet in the live history — an honest, un-cherry-picked snapshot, not a staged demo
number.

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Every round has found
something real and distinct — this is the sixth time in a row, not diminishing returns. User's
stopping target for this loop: ~90.

---

## 2026-08-24 — My audit loop, round 7: the same bug class, one call deeper

I ran round 7 of my audit loop, told explicitly to check whether round 6's fix was complete, not just
correct — and to look for the same failure *pattern* elsewhere, not just the same failure.

**Score: 74/100.** (AI Judgment 8/10, Failure Recovery 5/10, Measured Accuracy 9/10, Bounded &
Gated 8/10, Throughput 8/10, Real Problem 8/10, Submission Readiness 6/10 — weighted 16.0 + 10.0 +
13.5 + 12.0 + 8.0 + 8.0 + 6.0 = 73.5.)

### THE FINDING: round 6's fail-safe only guarded the JSON-parse step, not what came after it

`narrate_groq`/`narrate_ollama`'s `try/except (json.JSONDecodeError, KeyError)` wrapped `_parse_json_response()`
only. Round 6's category-validity check and the `NarratorOutput(...)` construction that followed it
sat **outside** that block — a leftover of how round 6's fix was written as an early-return `if`
statement bolted onto the existing structure, not a rewrite of the structure itself. Round 7
proved this crashes the real pipeline, not just a unit in isolation, with five concrete payloads
fed through the same client-mocking technique round 6's own tests used:

```
{"reasoning": "...", "category": "genuine_error"}                    -> missing "confidence" key -> UNCAUGHT KeyError
{"category": "genuine_error", "confidence": 0.5}                     -> missing "reasoning" key   -> UNCAUGHT KeyError
[{"category": "genuine_error", "confidence": 0.5, "reasoning": "x"}] -> top-level array, not obj  -> UNCAUGHT AttributeError
null                                                                  -> top-level JSON null       -> UNCAUGHT AttributeError
{"category": "genuine_error", "confidence": "high", "reasoning": "x"} -> confidence not numeric    -> UNCAUGHT ValueError
```

All five are syntactically valid JSON — `_parse_json_response` succeeds on every one — so round 6's
guard never triggers, and nothing downstream checked the shape before touching it (`parsed["confidence"]`,
`parsed["reasoning"]`, `float(parsed["confidence"])`). Round 7 confirmed the exception isn't
contained to one transaction: it propagates uncaught through `narrate()` -> `_process_batch()`'s dict
comprehension (`pipeline.py`) -> `run_batch()`, and `main.py` has no handler around `/api/run` or
`/api/transactions/evaluate` — so it surfaces as a raw HTTP 500 that loses the *entire* batch's
results, not just the one bad transaction. `/api/transactions/evaluate` is the live "break it" demo
endpoint the spec names as the lead pitch-video moment — round 7's phrase for what a judge would
see: "a raw crash, not the 'correctly escalates instead of guessing' story the whole project is
built to tell." Also confirmed: no existing test exercised this path (the only "malformed JSON"
reference in the old test file was a docstring comment) — which is exactly why it survived six
rounds.

### The fix

Merged the category-validation `if` into a second `try/except` that wraps the *entire*
validate-and-construct sequence, in both `narrate_groq` and `narrate_ollama`:

```python
try:
    if not isinstance(parsed, dict):
        raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
    if parsed.get("category") not in NARRATOR_CATEGORIES:
        raise ValueError(f"category outside the valid set: {parsed.get('category')!r}")
    output = NarratorOutput(
        ...,
        confidence=max(0.0, min(1.0, float(parsed["confidence"]))),
        ...,
    )
except (KeyError, TypeError, ValueError) as e:
    return _fail_safe(f"Narrator's final response was not a usable answer ({type(e).__name__}: {e}): {(msg.content or '')[:200]!r}")
```

The `isinstance` check runs before any `.get()`/subscript, so a wrong container type (list, `None`)
raises a deliberate `TypeError` instead of an incidental `AttributeError` — narrower, more readable
except clause, no need to catch `AttributeError` at all. **Verified before trusting it, not
assumed**: `pydantic.ValidationError` (what a bad-typed field would raise inside `NarratorOutput(...)`
itself) subclasses `ValueError` — checked directly (`issubclass(pydantic.ValidationError, ValueError)`
-> `True`, then round-tripped an actual bad-type construction through a real model to confirm),
so a type error surviving all the way to construction is caught here too, same as the round-6
`httpx.ConnectError`/`ConnectionError` check applied the same discipline to a different assumption.

Tests written **before** the fix this time (lesson from round 6's own process, see below): added
`test_narrate_groq_fails_safe_on_structurally_malformed_final_answer` and the `_ollama` twin in
`test_narrator.py`, looping over the five payloads above, confirmed all fail against the pre-fix
code, then applied the fix, confirmed all pass. 61/61 tests passing.

### The MEDIUM finding, fixed alongside: confidence was never bounded

Round 7 also asked whether `confidence` — another model-supplied value — was validated, since
it's the same trust-without-checking question one field over. It isn't corrupting the calibration
math (`calibrate()` only uses correct/incorrect booleans, confirmed by reading it directly), but it
does feed `escalation.py`'s `ambiguity = 1.0 - confidence` -> `priority_score = amount * ambiguity`:
a `confidence: 5.0` produces a *negative* priority score, sinking exactly the case that most needs a
human's attention to the bottom of the triage queue — the opposite of the feature's own stated
purpose. It would also render as "500.0%" or a negative percentage in the dashboard
(`frontend/src/formatters.ts`'s `pct()` has no clamping). Fixed at both real call sites
(`confidence=max(0.0, min(1.0, float(...)))`) and structurally backstopped with a Pydantic
`Field(ge=0.0, le=1.0)` on `NarratorOutput.confidence`, so any future call site that forgets to
clamp fails loudly instead of silently accepting a bad value. Checked the edge case a clamp alone
doesn't obviously handle — Python's `json` module accepts non-standard `NaN`/`Infinity` literals —
and verified directly (not assumed) that `max(0.0, min(1.0, float('nan')))` resolves to `1.0`
(Python's min/max keep the first argument when a comparison against NaN is False, so it's
well-defined here, if not obviously so from reading the expression alone) and that Pydantic accepts
the already-clamped result; added `test_narrate_groq_clamps_out_of_range_confidence` for the
ordinary case (`confidence: 5.0` -> `1.0`).

### Also fixed: the recurring doc-staleness pattern, swept systematically this time

Round 7's own diagnosis of *why* stale test counts keep recurring: prior fixes were "keyed to the
one number a reviewer had just flagged... never to systematically re-collecting every count claim."
I took that seriously — ran `pytest --collect-only` and cross-checked **every** `N/N tests passing`
claim in PROGRESS.md against the real per-file counts, not just the two round 7 happened to
name. I found exactly those two actually wrong (pipeline: claimed 10/10, real 12/12; the API-layer
line's "51/51 overall" was stale relative to the current 58-then-61 total) and confirmed the rest
(generator 6/6, matching 6/6, merkle 5/5, round 5's own "53/53" checkpoint) were each correct for
what they specifically describe — added a parenthetical to the one "overall" line clarifying it's a
running total, not a per-section snapshot, so this ambiguity stops being the recurring root cause.
Also fixed: `docs/track04-settlement-reconciliation-copilot.md` still named the pre-build tool
`check_duplicate_registry` in both its §6.4 tool list and its architecture-diagram ASCII art (the
name was replaced by `check_batch_anomalies` before either was ever implemented — BUILD_LOG's very
first narrator entry documents the consolidation — but the spec doc itself was never updated to
match, despite PROGRESS.md's own claim that the doc is "kept current through the build").

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Seven
rounds, seven genuinely distinct findings — the last three rounds specifically have each found a
different layer of "an LLM-supplied value trusted without checking it against what the rest of the
system assumes" (provider identity, category membership, response shape/type). User's stopping
target for this loop: ~90.

---

## 2026-08-24 — Self-caught: the same bug class, one call earlier still (not an audit finding)

While writing up round 7's fix, re-read the surrounding code one level up — the `if msg.tool_calls:`
branch, which runs *before* a final answer is ever produced — and the same pattern was sitting there
too, unfixed. Verified it directly before trusting the hunch, same as every other finding in this
log: mocked a Groq tool-call response with `function.arguments = "{not valid json"` and separately
with `function.name = "some_hallucinated_tool"`, ran both through `narrate_groq` with no other
change, and both crashed uncaught (`json.JSONDecodeError`, then `ValueError: unknown tool: ...`).
Confirmed the identical shape in `narrate_ollama` (`dict(tc.function.arguments)` raising `ValueError`
on a non-mapping argument value).

This is the same failure mode round 7 just fixed for the *final answer* — a value the model
supplies (here, a tool call's own name and arguments, before any reasoning about the transaction has
even happened) trusted without a guard, able to crash the whole `run_batch()` the same way, through
the same uncaught-exception path into `main.py`'s handler-less endpoints.

**Not something one of my audit rounds found — caught by re-reading the code myself while fixing
the adjacent bug**, worth recording as a distinct, honest data point: the discipline of "read the
whole function once you're already in it, not just the line you were sent to fix" catches things a
narrower fix wouldn't. Following the same process round 7 itself established: wrote
`test_narrate_groq_fails_safe_on_an_unusable_tool_call` and the `_ollama` twin first, confirmed both
fail against the unfixed code (real `JSONDecodeError`/`ValueError` propagating out of the test, not
an assertion failure), then wrapped the tool-execution loop in both providers in
`try/except (json.JSONDecodeError, TypeError, ValueError)`, routing through the existing
`_fail_safe`, confirmed both tests pass. 63/63 tests passing.

With this fix, every point in both `narrate_groq`/`narrate_ollama` where a model-supplied value is
used — the tool call itself, the tool call's arguments, the final category, the final confidence,
the final reasoning's presence — is now guarded. Not claiming there's no possible further layer,
just that a systematic pass was made rather than stopping at the first fix.

---

## 2026-08-24 — My audit loop, round 8: the whack-a-mole pattern, named and structurally closed

I ran round 8 of my audit loop, told explicitly that rounds 5 through the self-caught fix had each found
a different instance of the same bug class, and asked to form its own view on whether that pattern
was actually exhausted rather than assume either "surely fixed now" or "there must be one more."

**Score: 78/100.** (AI Judgment 17/20, Failure Recovery 12/20, Measured Accuracy 13/15, Bounded &
Gated 13/15, Throughput 8/10, Real Problem 9/10, Submission Readiness 6/10.) Net improvement over
round 7's 74 — real, verified progress — but the specific claim that closed round 7 ("every
model-supplied value in the narrator loop is now guarded," written in PROGRESS.md) did not survive
direct testing.

### THE FINDING: the tool-call guard still had a gap, and there's a better shape of fix than another patch

Three concrete, independently-reproducible crashes, all `AttributeError`, all missed by the
`(json.JSONDecodeError, TypeError, ValueError)` tuple the self-caught fix added:

```
tc.function = None                                          -> 'NoneType' object has no attribute 'arguments'
recall_similar_resolutions receives arguments = "[1,2,3]"    -> 'list' object has no attribute 'get'
recall_similar_resolutions receives arguments = "null"       -> 'NoneType' object has no attribute 'get'
```

The second case needed no SDK-internals knowledge to hit — it's just the model writing a JSON array
instead of an object for a tool call's arguments, and `_execute_tool`'s one tool that actually reads
its arguments (`recall_similar_resolutions`, via `arguments.get("category_guess", ...)`) has no
defense against that shape. Confirmed independently before touching any code (not just trusted from
the report): mocked the exact three payloads against `narrate_groq` and got the exact three
`AttributeError`s back.

**Round 8's more important point wasn't the specific gap, it was the shape of the fix.** Direct
quote: *"applying [a fail-safe] once at the orchestration layer would have prevented rounds 5-8's
entire whack-a-mole pattern from being possible in the first place."* Four rounds in a row (5, 6, 7,
and the self-caught fix) had each found a different unguarded model-supplied value inside
`narrate_groq`/`narrate_ollama`'s own exception handling — a real, recurring pattern, and the honest
read is that the *next* one, whatever shape it takes, is by definition not one either function's
except tuple names yet, because nobody knows what it is until it happens.

### The fix, in two parts (both done, not just the cheaper one)

1. **Narrow**: added `AttributeError` to both providers' tool-call except tuples — closes the three
   specific reproduced cases with a decent, specific `reasoning` string ("Narrator requested a tool
   call that could not be executed...").
2. **Structural**: wrapped `narrate()`'s own dispatch to whichever provider function in a broad
   `try/except Exception`, converting *any* exception a provider function doesn't already handle
   into a safe `genuine_error`/confidence-0.0 result, tagged with the correct `provider`. This does
   not replace the provider-specific fail-safes — those still fire first and produce a more
   informative reasoning string for a *known* failure shape — it's the last line of defense for
   whatever isn't a known shape yet. `except Exception` (not narrower) is deliberate here and scoped
   specifically to this one boundary: `narrate()` is the single seam between "arbitrary code calling
   into an inherently unreliable external system" and "the rest of the pipeline," the same place a
   web server's top-level request handler earns a broad catch that inner application logic doesn't.
   `KeyboardInterrupt`/`SystemExit` aren't `Exception` subclasses, so an actual interrupt still
   propagates correctly — checked, not assumed.

Three new tests, written before the fix (established practice since round 7): two reproduce the
specific `AttributeError` shapes against the real provider functions, one
(`test_narrate_dispatcher_fails_safe_on_a_completely_unforeseen_exception`) mocks `narrate_groq`
itself to raise a plain `RuntimeError` with no special meaning — proving the orchestration backstop
works for a genuinely arbitrary failure, not just the ones already known about. All three confirmed
to fail against the pre-fix code, then confirmed to pass. 66/66 tests passing.

### Two MEDIUM doc findings, also fixed

- PROGRESS.md's own "current total" test-count line (added in round 7 specifically to stop this
  exact class of drift) was already stale at 61 when the real count was 63 — the identical failure
  class recurring in the very line meant to guard against it. Fixed to 66, with an added note that
  if it's stale again, that's the pattern repeating, not a surprise.
- `docs/track04-settlement-reconciliation-copilot.md` described `recall_similar_resolutions` as "a
  plain SQLite lookup... no vector DB needed" in two places, but the shipped implementation is a
  pure in-memory list rebuilt fresh per run (`ToolContext.audit_log`), never touching SQLite, with
  no cross-run memory — a real, known limitation disclosed honestly elsewhere (BUILD_LOG's original
  Gap #8 entry, PROGRESS.md) but never corrected in the architecture doc itself across 7 rounds of
  otherwise-thorough doc sweeps. Fixed both mentions to describe what's actually shipped.

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Eight rounds, eight genuinely distinct findings (plus one self-caught fix in between) — the
whack-a-mole pattern that defined rounds 5 through 8 is now closed at the structural level, not just
patched at the specific-instance level, which is a materially different kind of fix than the ones
before it. My stopping target for this loop: ~90.

---

## 2026-08-24 — My audit loop, round 9: the narrator holds; the same pattern, one subsystem over

I ran round 9 of my audit loop, told explicitly to spend real effort trying to defeat round 8's structural
backstop specifically, then to spend the *majority* of its budget away from the narrator entirely —
four rounds of concentrated attention there had earned a real look at the rest of the system instead
of a fifth narrow pass over the same file.

**Score: 82/100.** (AI Judgment 17/20, Failure Recovery 13/20, Measured Accuracy 14/15, Bounded &
Gated 13/15, Throughput 8/10, Real Problem 9/10, Submission Readiness 8/10.) A real step up from
round 8's 78 — and this time two of the round's headline conclusions were *positive*, independently
verified rather than assumed:

- **The `narrate()` backstop genuinely holds.** Round 9 read it line-by-line and specifically
  tried to defeat it: checked live (not assumed) that every exception type in both providers' own
  retry/API-error paths (`GroqError` and its subclasses, `httpx.ConnectError`/`TimeoutException`,
  `ollama.RequestError`/`ResponseError`) really does subclass `Exception`, confirmed the only
  pre-`try` code in `narrate()` is trivial and non-raising, and grepped the whole repo to confirm
  `narrate_groq`/`narrate_ollama`/`narrate_mock` are never called directly from production code,
  only from `narrate()` and from `test_narrator.py`'s intentional unit-test isolation. Four rounds
  of narrator-focused pressure (5-8) are now closed, verified independently, not just claimed.
- **Doc staleness — the failure class that recurred in nearly every round, including round 8
  finding round 7's own anti-staleness fix already stale — was actually fully fixed this time.**
  Test counts matched everywhere claimed current; the `recall_similar_resolutions` correction from
  round 8 checked out against the real implementation.
- **The 94.4% Ollama accuracy claim was re-verified from scratch a third time** (rounds 6, 7, and
  now 9 have each independently regenerated the seed-42 ground truth and cross-checked it against
  `docs/evidence/real-ollama-run-2026-08-24.json` — same 17/18, same specific miss, every time).

### THE FINDING: the exact same pattern, one subsystem over — an unguarded boundary with untrusted input

`POST /api/transactions/evaluate` — the live "break it" endpoint, the spec's own lead pitch-video
moment — had zero exception handling anywhere in `main.py`, and `build_all_chains` in
`chain/builder.py` does three unguarded dict lookups (`payments_by_order[order.order_id]`,
`settlements_by_payment[payment.payment_id]`, `ledger_by_order[order.order_id]`) that assume every
order has a matching payment, settlement, and ledger entry. That assumption is *correct* for
`run_batch`'s path — `generate()` always produces referentially-complete records by construction —
but this is the one endpoint where a judge submits or edits a scenario by hand, with no such
guarantee. A missing record, or a settlement pointing at the wrong `payment_id` (a plausible typo),
crashed the endpoint with a bare `"Internal Server Error"` — no category, no reasoning, nothing like
the specific, honest fail-safe messages the narrator now produces for every one of its own failure
modes. Round 9 reproduced three separate realistic payloads live and confirmed the crash didn't
take the whole server down (`/api/health` still returned 200 afterward) and that the frontend didn't
white-screen — but the error banner it did show said nothing useful to a judge.

This is structurally the identical lesson as round 8, in a different subsystem: an unguarded
boundary where untrusted (here, judge-submitted) input meets code that assumes well-formed data.

### The fix: the same two-part pattern round 8 established, applied to this boundary

1. **Specific**: catch `KeyError` from `build_all_chains` and return a `422` naming the exact
   missing reference (`e`'s own key), with a one-line explanation of what every order needs.
2. **Structural**: wrap the endpoint's full body in a broader `except Exception`, returning a
   generic-but-honest `422` for anything unforeseen — the same backstop shape as `narrate()`'s own,
   applied to the one other place in the system where untrusted input enters processing.

2 new tests, written before the fix (established practice since round 7): one submits a scenario
with an order that has a payment and settlement but no ledger entry, confirms the pre-fix code
actually crashes (verified via `git stash` this time, not `git checkout` — see below), then confirms
the post-fix response is a clean `422` naming the specific order. The other mocks
`run_matching_engine` itself to raise a plain `RuntimeError`, mirroring round 8's own
"totally unforeseen failure" test, proving the broader backstop isn't tied to the one `KeyError`
shape already found. 68/68 tests passing.

**Process note, an actual improvement over an earlier mistake in this same log**: verifying the fix
against the pre-fix code required temporarily setting the fix aside. The provider-gate fix (round 5)
and the category-validation fix (round 6) both used `git checkout -- <file>` for this, and round 6's
attempt at that pattern actually backfired once — a `git checkout` meant to restore a temporary
strip-to-test state instead reverted all the way back to the last *commit*, wiping an uncommitted
fix entirely (caught immediately, reapplied, noted in this log at the time). This round used
`git stash push -- <file>` / `git stash pop` instead — reversible, scoped to exactly the one file,
no risk of overshooting past uncommitted work. Worth remembering as the correct tool for "temporarily
set aside an uncommitted change, then bring it back" going forward.

### Three LOW findings, also fixed

- `CalibrationPanel.tsx` and `EscalationQueue.tsx` both caught background-refresh/resolve failures
  with `.catch(console.error)` and nothing visible to the user — a stale table or a silently
  re-enabled button, no indication anything went wrong. Both now surface a visible error message
  (reusing the existing `.error-text` style) while keeping the last-known-good data on screen rather
  than blanking it.
- No top-level React `ErrorBoundary` — added one (`ErrorBoundary.tsx`, a class component, the only
  way React supports this) wrapping `<App />` in `main.tsx`. Low practical risk today (every FastAPI
  response is validated through a pydantic model before serializing) but real defense-in-depth for
  the one endpoint that accepts free-form input, at near-zero cost.
- `SummaryTiles.tsx` could render the literal string `"NaN%"` with a deliberately zero-sized batch
  (`main_n=0`, `total_amount`/`total_transactions` both 0, `0/0` is `NaN` in JS) — the backend
  already handles this input fine (an honest all-zero result, not a crash), only the frontend
  display math was unguarded. Fixed by guarding both divisions rather than trying to prevent the
  input (the HTML `min` attribute the batch-size field already has is a soft hint a user can type
  past, not real enforcement).

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Round 9: 82. Four consecutive rounds of net improvement (70 → 74 → 78 → 82) after round 6's dip
— each round's fix has held under the next round's independent re-verification, not just been
claimed and moved past. My stopping target for this loop: ~90.

---

## 2026-08-24 — My audit loop, round 10: the third occurrence, this time on the endpoint that matters most

I ran round 10 of my audit loop, explicitly told round 9 found the "unguarded boundary" pattern once
already (on a secondary endpoint) and asked to check whether it shows up a third time anywhere
else, while also trying fresh attack angles against round 9's own fix rather than just re-confirming
what round 9 already checked.

**Score: 70/100** (AI Judgment 15/20, Failure Recovery 7/20, Measured Accuracy 14/15, Bounded &
Gated 10/15, Throughput 8/10, Real Problem 9/10, Submission Readiness 7/10) — a real drop from round
9's 82, and a deserved one: this round found the same underlying lesson a third time, on `/api/run`
itself, the endpoint every ordinary batch run goes through, not a bonus demo path. It also found a
second, entirely independent bug with a different root cause.

### CRITICAL #1: the exact pattern, a third subsystem, the primary endpoint this time

`narrate()`'s provider-validity check sat *before* its own `try` block — the round-8 backstop
protects everything inside that block, and this line was never inside it:

```python
def narrate(chain, context, provider=None):
    provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    if provider not in ("mock", "groq", "ollama"):
        raise ValueError(...)   # <- outside the try/except below, unprotected
    try:
        ...
    except Exception as e:
        ...   # the backstop rounds 8-9 believed closed this whole class of bug
```

Live-reproduced against the real running server: `POST /api/run {"provider": "gpt4-turbo-not-a-real-provider"}` → bare HTTP 500. README.md itself documents `provider` as a normal per-request field
(`§Setup`, "or pass `"provider": "ollama"` / `"provider": "groq"` per-request"), so this needs
nothing adversarial — a typo or wrong capitalization is enough. **This had already been written down
in this project's own audit trail and dropped**: round 7, diagnosing a different (since-
fixed) bug, noted in this very file that "`main.py` has no handler around `/api/run` or
`/api/transactions/evaluate`." Round 9 fixed the second endpoint that sentence named. The first sat
unfixed for three more rounds.

**Fix**: moved the validity check inside `narrate()`'s own try block (closes it at the actual root —
any future caller of `narrate()`, not just these two endpoints, now gets the same protection), and
wrapped `/api/run`'s body in the same two-part backstop pattern already proven at
`/api/transactions/evaluate`. Additionally — and this is the more useful fix for a real caller, not
just a crash-proofing one — both `RunRequest.provider` and `TransactionScenario.provider` are now
typed `Literal["mock", "groq", "ollama"] | None` instead of `str | None`, so a bad provider string
is rejected immediately with one clear message at request-parsing time, before a whole batch runs
and every narrated transaction fails safe individually with the same confusing repeated message.

### CRITICAL #2: an independent bug — the shared SQLite connections were never actually safe under concurrent access

`AuditLogger` and `CalibrationHistory` each hold one `sqlite3.connect(..., check_same_thread=False)`
connection, with a code comment claiming this was "fine at this app's scale... effectively
serialized request handling." **Round 10 disproved that empirically**, not by inspection: fired 8
simultaneous `POST /api/run` calls at a live server — 7 of 8 failed with `sqlite3.InterfaceError:
bad parameter or other API misuse` or `SystemError: error return without exception set`. Even just 2
simultaneous requests (an entirely ordinary "two tabs open" or "double-click" scenario, not an
adversarial one) failed about half the time across repeated trials.

The root cause is a real, well-known sqlite3 gotcha: `check_same_thread=False` only disables
Python's *own* thread-affinity check — it does not make the underlying C-level connection safe for
genuinely concurrent use from multiple threads at once, and FastAPI's sync-endpoint threadpool
really does dispatch concurrent requests to real, different worker threads in parallel, not
"effectively serialized" the way the comment assumed. Nobody had ever actually tested this claim
before round 10 did.

**Fix**: added a `threading.Lock` to both `AuditLogger` and `CalibrationHistory`, held around every
operation that touches the shared connection (reads included, not just the writes the audit
reproduced). Reproduced the exact failure independently before fixing (see below), then confirmed
the fix holds under repeated runs, not just once — ran the new concurrency test 5 times in a row
after the fix, 5/5 clean.

### HIGH: an out-of-range threshold could force the calibration gate open

`RunRequest.threshold` and `/api/calibration`'s query parameter had no bounds check. Live-
reproduced: `POST /api/run {"threshold": -0.5, ...}` returned a calibration report marking
`duplicate_refund` and `netting_trap` as `"decision": "auto_resolve"` on evidence that had never
actually cleared 90% — the Wilson lower-bound gate itself, not just a single decision, flipped open.
Round 10 was careful to caveat what was and wasn't directly witnessed (didn't spend real API
quota to force a live non-mock auto-resolution through this end-to-end; proved the calibration
report's own gate mechanism flips, which is what any subsequent real-provider decision would be
checked against) and confirmed this is unreachable via the shipped UI (the slider is clamped to
`[0.5, 0.99]`, `RunControls.tsx` hardcodes `0.9`) but *is* reachable via direct API calls — exactly
the interaction pattern the "break it" panel explicitly invites.

**Fix**: `Field(ge=0.0, le=1.0)` on `RunRequest.threshold`, `fastapi.Query(0.90, ge=0.0, le=1.0)` on
`/api/calibration`'s threshold parameter — the same structural-constraint pattern already used for
`NarratorOutput.confidence` since round 7.

### MEDIUM: duplicate primary keys silently drop a submitted transaction

`build_all_chains` (`chain/builder.py`) builds internal dicts keyed by `order_id`/`payment_id` with
no uniqueness check — two orders sharing an `order_id` in a submitted scenario silently returned 1
result instead of 2, no error, no warning. **Fix**: a `model_validator` on `TransactionScenario`
checking all four record types `build_all_chains` keys by (`order_id`, `payment_id`,
`settlement_id`, `ledger_id`) for duplicates, not just the one shape the audit reproduced, since the
same silent-overwrite risk applies structurally to any of the three dicts that function builds.

### Verification, and a process note carried forward from round 9

5 new tests (`test_api.py`): an unknown-provider rejection, an out-of-range threshold on both
`/api/run` and `/api/calibration`, a duplicate-`order_id` rejection, and the concurrency test itself
(8 parallel requests via `ThreadPoolExecutor` against the real `TestClient`, which dispatches
through the same `run_in_threadpool` machinery a real server does). Written before the fix, verified
against the actual pre-fix code via `git stash push -- <files>` / `git stash pop` (not `git
checkout`, per the lesson recorded in round 9's own entry) — all 5 failed against the unfixed code,
including the concurrency test, which reproduced the exact same `SystemError: error return without
exception set` round 10 saw, spontaneously, confirming CRITICAL #2 independently a second time. All 5 pass
after the fix; the concurrency test specifically was re-run 5 additional times to check it wasn't
passing by luck. 73/73 tests passing.

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Round 9: 82. Round 10: 70. The second real dip in the loop, and — like round 6's dip before it —
driven by a genuinely new, previously-undiscovered class of problem (concurrency safety) plus a
third occurrence of a pattern believed closed twice already. Worth naming explicitly: the same
underlying lesson ("an unguarded boundary where untrusted or unusual input meets code that assumed
well-formed data") has now recurred in the narrator (rounds 5-8), the live evaluate endpoint (round
9), and the primary run endpoint (round 10) — three subsystems, one lesson, each time closed only
after being found live rather than anticipated. My stopping target for this loop: ~90.

---

## 2026-08-24 — Self-caught: the fourth occurrence, found while round 11 was cut off mid-audit

Round 11 hit a session usage limit partway through (its own last words, before termination: "Let me
do one more quick live check on round 10's threshold-bounds fix, since I've now re-verified 3 of its
4 fixes directly this round") — it had independently re-confirmed most of round 10's fixes but never
delivered a score or a findings list. Rather than immediately kick off another full audit round into
the same exhausted quota, I picked up the one specific thread round 11 was assigned but hadn't reached
yet: `POST /api/escalations/resolve` had been flagged across two rounds as never fuzzed live, only
code-reviewed. I read it directly myself instead of waiting for another round to.

### THE BUG: the fourth instance of the pattern, and this one's a real race condition, not just missing validation

```python
escalation = state.latest_escalations_by_id.get(req.transaction_id)   # (1) check
if escalation is None:
    raise HTTPException(404, ...)
...
calibration_history.confirm_human_resolution(...)                      # (2) act
del state.latest_escalations_by_id[req.transaction_id]                 # (3) delete
```

Check, act, and delete were three separate steps with no lock between them — a textbook
time-of-check-to-time-of-use race under FastAPI's genuinely-concurrent threadpool (the same
"effectively serialized" assumption round 10 already disproved once, for the SQLite connections,
now disproved a second time for this in-memory dict). Reproduced live before writing anything down,
same discipline as every other finding in this log: fired 5 concurrent resolve requests at the same
escalation. Two consequences, both real:

- **Silent data corruption**: more than one concurrent request can pass the `.get()` check before
  either reaches the `del`, so more than one writes a `human_confirmed` entry into
  `calibration_history` for the *same* real-world resolution — one genuine data point silently
  double-counted as two (or more) independent observations, corrupting the Wilson interval's
  sample-independence assumption in exactly the direction that makes a category look more trustworthy
  than the evidence actually supports.
- **A crash**: the second (and any further) concurrent request then hits `del` on an already-deleted
  key — `KeyError`, uncaught, propagating through `run_in_threadpool` to a bare HTTP 500. Confirmed
  directly: `dict.__delitem__` on a missing key raises `KeyError` in isolation first, then reproduced
  it live end-to-end through the real endpoint.

### The fix

```python
with _escalation_lock:
    escalation = state.latest_escalations_by_id.pop(req.transaction_id, None)
if escalation is None:
    raise HTTPException(404, ...)
```

`dict.pop(key, None)` makes "check and claim" one atomic step under a `threading.Lock`
(`_escalation_lock`, module-level, next to `state`): only the request that actually removes the
entry proceeds to record a resolution; every other concurrent request correctly sees it as already
gone and 404s cleanly, the same outcome as if it had arrived a moment later rather than at the exact
same instant. No more separate `del` for a second request to crash on. One minor, deliberately
accepted behavior change: if `true_label` turns out to be missing (the pre-existing "stale run?"
404), the escalation is now popped anyway rather than left lingering in the pending dict — reasoned
through and judged inconsequential, since that dict is pure server-side bookkeeping the frontend
never reads directly, and a ground-truth-less escalation was never actually recoverable once that
happened regardless of whether it stayed in the dict or not.

Test written first, verified against the actual pre-fix code via `git stash push -- backend/app/main.py`
/ `git stash pop` (the process lesson recorded in round 9's entry continues to hold): fired 5
concurrent resolves, asserted exactly one `200` and four clean `404`s plus exactly one new
`calibration_history` row. First version of the test had its own bug — asserted zero existing rows
for the transaction *before* resolving, not accounting for the fact that the batch's own mock
narration already writes a `provider="mock"` row for every narrated transaction, resolved or not;
fixed to assert the *delta* a resolve adds instead of absolute presence. Failed against the unfixed
code with the exact predicted `KeyError`, passed after the fix, re-run 5 additional times to rule
out a lucky pass (5/5 clean both times). 74/74 tests passing.

### Worth naming plainly, as promised in round 10's entry

This is the fourth subsystem where the same underlying lesson has shown up: the narrator (rounds
5-8, tool-calling and response validation), the live evaluate endpoint (round 9, referential
integrity), the primary run endpoint (round 10, provider validation and SQLite concurrency), and now
the escalation-resolve endpoint (this entry, an in-memory-dict race). Four for four, each one found
live rather than anticipated, each one closed the same way once found: validate or lock at the exact
boundary where something outside the system's control (a model's output, a judge's hand-crafted
input, two requests arriving at the same instant) meets code that assumed a single well-behaved
caller. Whether this means every remaining unscrutinized corner has one more instance waiting, or
whether four is where it actually ends, is a question for whichever round looks next — not something
to guess at here.

---

## 2026-08-24 — My audit loop, round 11: the fifth instance, and closing the whole class of it

I ran round 11 of my audit loop (the first attempt at this round hit a session usage limit mid-run and
never delivered a score — see the self-caught entry above; this is a fresh, complete round with no
memory of that attempt). I told it explicitly to check whether the pattern found four times already shows
up a fifth time, and to spend the majority of its budget on genuinely fresh ground otherwise.

**Score: 80/100** (AI Judgment 16/20, Failure Recovery 13/20, Measured Accuracy 13/15, Bounded &
Gated 13/15, Throughput 8/10, Real Problem 8/10, Submission Readiness 9/10). Up from round 10's 70 —
real, independently-verified improvement, but the round's own headline finding is that the
"unguarded boundary" pattern the last four fixes (rounds 5-8, 9, 10, and the escalation-lock
self-caught fix) each closed for one subsystem apiece had a fifth instance sitting right next to the
fourth one, in code touched by the *previous* fix itself.

### THE FINDING: `/api/run`'s three state writes still weren't atomic relative to what `/api/escalations/resolve` reads

The escalation-lock fix (previous entry) protected the *pop* in `/api/escalations/resolve`, but
`/api/run` still committed its three related fields — `latest_result`, `latest_escalations_by_id`,
`latest_ground_truth` — as three separate, unlocked statements. A concurrent `/api/run` could
overwrite `latest_ground_truth` with a *different* run's data in the narrow gap between those three
assignments, while a resolve reading in that same window would get one run's escalation paired with
another run's ground truth — silently stranding an entire run's escalation queue as permanently
"stale run?" 404s, even though `/api/runs/latest` still showed them as live and resolvable.

Round 11 didn't just claim this — it reproduced the exact desync live using `sys.setswitchinterval()`
to amplify thread-scheduling (a standard, legitimate technique for exposing a genuine race by making
interleaving far more likely, not for manufacturing a fake one): 32 concurrent `/api/run` calls
desynced the state on the **first trial**. It also honestly calibrated how hard this is to hit by
accident — 8 concurrent requests (the exact level this project's own committed concurrency test
uses) never reproduced it in 20 trials; 2 concurrent requests (a realistic double-click, also guarded
client-side by `RunControls.tsx` disabling the button mid-request) never reproduced it in 60 trials.
Real, but needing deliberate concurrent load to hit — which the project's own exposed `/docs` Swagger
UI makes easy for anyone motivated to try, especially a judge testing the README's own headlined "8
concurrent runs all succeed" claim at slightly higher load.

Round 11 also flagged, more tentatively: `/api/escalations/resolve` had no try/except backstop
unlike its two sibling endpoints (an inconsistency, not a proven live crash), and the escalation-lock
fix's own accepted tradeoff (an escalation popped before its ground-truth check is now lost rather
than retryable) technically doesn't hold in the sub-case where a *live* concurrent `/api/run`
causes the ground-truth miss, rather than a genuinely stale one — tried to trigger this specifically
(60 trials, same amplification technique) and got 0/60, reported honestly as logically real but
empirically elusive, sharing its root cause with the main finding.

### The fix: not a fourth lock-and-hope patch, a structural one

Verifying this by writing the reproduction myself first (same discipline as every other finding in
this log) turned up something the narrow "add another lock" fix wouldn't have caught: even with
`_state_lock` correctly protecting the *commit* in `/api/run` and the *read sequence* in
`/api/escalations/resolve`, a hypothetical future reader that didn't know to acquire that lock could
still observe a torn state — the lock only provides mutual exclusion among code that actually asks
for it. Wrote a test that samples `state` from an unlocked background thread during 16 concurrent
runs (the same amplification technique) and got **8598 violations** against the lock-only version of
the fix — a real, cleanly-reproducible gap, not a false alarm.

Rather than patch that too, restructured `_AppState` around one frozen `_RunSnapshot` dataclass
holding all three related fields together:

```python
@dataclass(frozen=True)
class _RunSnapshot:
    result: BatchRunResult | None = None
    ground_truth: dict[str, str] = field(default_factory=dict)
    escalations_by_id: dict[str, dict] = field(default_factory=dict)

class _AppState:
    def __init__(self):
        self.latest = _RunSnapshot()
```

A commit is now one atomic reference swap (`state.latest = _RunSnapshot(...)`) — a single Python
attribute assignment, atomic under the GIL by construction, with no lock required for this specific
property. Every reader captures `snapshot = state.latest` **once**, then reads every field off that
same captured object — guaranteed internally consistent because the three fields were constructed
together and the dataclass is frozen against reassignment. `_state_lock` is kept, but now only for
the compound check-and-claim sequence in `/api/escalations/resolve` (protecting one specific
transaction_id's pop against a concurrent double-resolve), not for cross-field consistency — that's
handled structurally now, for any reader, including one nobody's written yet.

This is a different *kind* of fix than the four before it. Rounds 5 through 10 each closed one
specific unguarded boundary by adding validation or a lock at that exact spot — correct, but each
one only protects the callers that exist today. This fix removes the need to remember a convention
at all for the specific property it protects, which is exactly the shape of failure that produced
all five findings in this loop: someone (an audit round, a fix, eventually a future maintainer)
not knowing a lock needs to be held, or a value needs to be checked, at a spot that looks
unremarkable until it isn't.

Test rewritten to sample `state.latest` as one atomic reference (matching how a real caller must use
it, not the two-separate-unsynchronized-reads shape that would trivially fail even against a correct
fix) — 8598 violations against the pre-fix three-separate-writes code, 0 against the snapshot-based
fix, re-run 5 additional times to confirm. 75/75 tests passing.

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Round 9: 82. Round 10: 70. Round 11: 80. Second dip-then-recovery cycle in the loop (round 6
dipped then recovered across rounds 7-9; round 10 dipped, round 11 recovers to 80) — each dip has
been a genuinely new class of problem, and each recovery has held under the next round's independent
re-verification rather than just being claimed. My stopping target for this loop is now an
explicit hard 95, not the earlier ~90 softening — the loop continues.

---

## 2026-08-24 — Targeted Failure Recovery pass: a call that never returns, and a frontend that never says so

Round 11 landed at 80/100 with Failure Recovery still the lowest-scoring criterion at 13/20 despite
five rounds of attention. I decided to work on improving it specifically myself, rather than wait for
the next full audit round (which I ran in parallel, deliberately steered toward other criteria since
this one had already had five rounds of scrutiny). Two real, previously-unfound gaps, both closed.

### The backend gap: every fix in this whole loop protects against a call that raises — none protect against one that never returns

Eleven rounds of fail-safe fixes all share one assumption: that a failing narrator call eventually
*raises* something — a bad category, a malformed response, an unknown provider, a `KeyError` — and
gets caught. Checked what happens when a call simply hangs instead, and found it directly rather
than assumed: `ollama.Client()`, constructed with no keyword arguments (as `narrate_ollama` did),
resolves to `timeout=None`. Verified precisely, not inferred — `httpx.Client()`'s own bare default
is a sane `Timeout(timeout=5.0)`, but the `ollama` package's constructor explicitly overrides that to
unbounded (checked both directly: `httpx.Client().timeout` vs. `ollama.Client()._client.timeout`).

This means a genuinely hung local model call — a GPU driver stall, a generation loop that never
terminates, a dropped connection the OS doesn't notice — would block `client.chat(...)` **forever**.
Not for a long time: forever. Every fail-safe this project has built, including `_call_with_retry`'s
own `httpx.TimeoutException` handling in `ollama_retry_exceptions` (already correctly listed, since
round 8), was already wired to catch exactly this and fail safe cleanly — it just had nothing to
ever catch, because no timeout could ever fire to raise it. The tied-up request thread would sit
there indefinitely, and enough of them would eventually exhaust FastAPI's whole threadpool, since
every endpoint shares it — a genuine availability risk for the entire application, not just one
narrator call, on the recommended default provider.

**Fixed**: `Client(timeout=60.0)` in `narrate_ollama`. 60 seconds is generous relative to the
measured ~3s/txn average (BUILD_LOG's own earlier Ollama entries) but finite, so a real hang now
fails safe within a bounded time instead of tying up a thread indefinitely. Also made `narrate_groq`'s
timeout explicit (`timeout=60.0`) even though Groq's SDK default was already sane (verified:
`groq._base_client.DEFAULT_TIMEOUT` is `connect=5.0, read/write/pool=60.0`) — not fixing a live bug
there, just documenting the actual bound in this file instead of leaving it implicit in whatever the
SDK happens to default to today. New test proves the client is constructed with a real, finite
timeout without waiting out an actual 60-second hang (that would make the suite unbearably slow) —
mocks the client and asserts on its construction kwargs, failed against the pre-fix code, passes
after. 76/76 tests passing.

### The frontend gap: nothing tells a viewer a long-running request isn't a hang

A real Groq run can take 11-70 minutes (this log's own earlier entries). The only feedback during
*any* run, mock or real, used to be a static "Running…" button — no elapsed time, no explanation,
no distinction between "still working" and "stuck." This is the identical trap I already fell into
once myself, with a live Ollama run (this log's "A real hang, chased carefully, that turned out not to
exist" entry) — except now it's client-facing risk during an actual demo, not just my own momentary
confusion working through it privately.

**Fixed three things:**
- `RunControls.tsx` now shows a live elapsed-seconds counter (`Running… 47s`) and, past 10 seconds,
  an explicit note: *"Still working — this is expected, not a hang. Mock and Ollama runs typically
  finish in seconds to a couple minutes; Groq's free tier can take much longer... because of
  rate-limit backoff, not because anything is stuck."*
- `App.tsx` now checks `/api/health` proactively on page load, not just reactively on the first
  failed action — a stopped backend now shows a clear, actionable banner (with the exact command to
  start it) the instant the page loads, rather than only surfacing after a user clicks something and
  gets a confusing error.
- `api.ts`'s shared `request()` helper now distinguishes a genuine network failure (`fetch()` itself
  rejecting — no backend to talk to at all) from a handled server error (a response came back, just
  not a success one) — previously both surfaced as whatever raw error the browser happened to throw,
  which for a network failure is an unhelpful `TypeError: Failed to fetch` with no indication of what
  to actually do about it.

Verified live in a real browser (Playwright): zero console errors, and specifically confirmed no
false-positive "backend unreachable" banner when the backend is actually up and healthy. `npm run
build` (strict `tsc -b && vite build`) passes clean. No component-level frontend test framework
exists in this project (verification has consistently been live-browser + build, same standard every
frontend change in this log has used) — didn't introduce one just for this change.

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Round 9: 82. Round 10: 70. Round 11: 80. This entry isn't a numbered audit round — it's my own
direct pass to work Failure Recovery up from round 11's 13/20, in parallel
with round 12 (running concurrently, deliberately pointed elsewhere). Whether it moved the number is
for round 12 or whichever round looks at Failure Recovery next to say — not something to claim here
without independent re-verification, consistent with how every other fix in this log has been
treated. My stopping target remains a hard 95.

---

## 2026-08-24 — My audit loop, round 12: the sixth instance, and the strongest AI Judgment evidence found yet

I ran round 12 of my audit loop in parallel with the direct, targeted Failure Recovery pass (the
previous entry) — deliberately pointed it away from the concurrency-hardening arc that had already had
five rounds of attention, toward AI Judgment, Real Problem, Throughput, and Submission Readiness
specifically, plus gave it permission to report a sixth pattern instance if genuinely found rather than
manufactured.

**Score: 77/100** (AI Judgment 16/20, Failure Recovery 12/20, Measured Accuracy 13/15, Bounded &
Gated 13/15, Throughput 8/10, Real Problem 8/10, Submission Readiness 7/10) — down 3 from round
11's 80. An honest dip: Failure Recovery dropped one further point from finding the sixth instance
below (before the fix landed), and Submission Readiness dropped two from real, if minor, findings.
Every other criterion held under fresh, independent scrutiny.

### THE FINDING: a sixth instance of the pattern, in `CalibrationHistory` this time

Confirmed the `_RunSnapshot` fix from the previous entry genuinely holds — re-ran its test three
additional times, traced every read/write path by hand, found no gap. But looked one level deeper,
at `CalibrationHistory`, the other piece of shared mutable state `/api/run` touches, and found the
identical underlying pattern in a subsystem the `_RunSnapshot` fix never covered: `add()` and
`report()` were each individually lock-safe, but not atomic *as a pair*. A concurrent
`reset_history` request's `clear()` could fire in the gap between one request's own `add()` and
that same request's later `report()` — reproduced live: request A added 9 decisions, request B's
`clear()` fired, B added its own 22, and A's own subsequent `report()` came back reflecting B's 22,
with A's own 9 permanently gone. Not delayed — gone, no error, silently corrupting the exact ledger
the "trust accumulates over time" pitch is built on. Realistically reachable, too: real-provider
runs take 50s-70 minutes per this project's own docs, and an impatient judge clicking "Run batch"
again with "reset" checked while a prior run is still in flight needs no thread-scheduling
amplification to happen — a plausible interaction, not a manufactured edge case.

**Fixed** the same way the last several rounds have: added `CalibrationHistory.add_and_report()`,
which inserts a call's own decisions and reads back the report in one lock acquisition, so no other
thread's `clear()` can run in the gap. Found and fixed the identical gap in
`confirm_human_resolution()` (the escalation-resolve feedback loop) too, once looking for the
pattern specifically — it had the exact same add-then-separately-report shape. Both `run_batch`'s
calibration commit and `/api/escalations/resolve` now use the atomic path. New test fires 30
concurrent `add_and_report` calls against a thread hammering `clear()` and requires every one to
see its own just-added decisions in its own returned report — failed against the pre-fix code
(the method didn't exist yet), passes after, re-run 5 additional times clean.

### The AI Judgment evidence exists — and re-verifying it directly turned up something better than what was cited

Round 12 found and cited one concrete example of the narrator using a tool's numeric output to
set a different decision's confidence — real evidence against the highest-weighted criterion (20%),
previously undocumented anywhere. Checked it directly rather than taking the citation at face value,
querying the live audit log myself: the cited transaction (`order_671da51349f1`) has been narrated
16 times across this session's accumulated history, and *13 of those 16* are `mock` decisions whose
"match" is tautological — `narrate_mock`'s `genuine_error` confidence is a hardcoded `0.3` constant,
and if that's also the dominant value already in a run's own `recall_similar_resolutions` history,
the average trivially converges to the same constant. That's not evidence of anything.

The 3 real (Ollama) runs are the actual evidence, and they're more convincing than one citation:
three independent real runs, three genuinely *different* confidence values (`0.533`, `0.25`,
`0.62` — not a repeated constant), each one exactly matching what `recall_similar_resolutions` had
just told it for that specific run's own prior context. Matching a varying, run-specific average by
coincidence three times with three different numbers is a much stronger claim than matching a fixed
constant once. Added this — the honest version, explicitly distinguishing it from the tautological
mock matches rather than citing the inflated 16-observation count — to README's "What's real vs.
mock" section, since a judge working under real time pressure (a real run costs 50s-70 minutes) may
default to mock mode and never see genuine reasoning directly otherwise.

### Two Submission Readiness findings, both fixed

- `docs/track04-settlement-reconciliation-copilot.md`'s §10 submission checklist (all six items
  `[ ]`, the original pre-build plan) directly contradicts `PROGRESS.md`'s own mirror of the same
  six items (five of six `[x]`) — and README links the track04 doc prominently as "Full design
  rationale," so a judge reading cold is likely to open it first and see an apparently 0%-complete
  project. The exact "stale/contradictory claims across files" failure class this project has
  already caught and fixed four separate times (UI copy, test counts twice, Node version), just not
  yet in this specific spot. Fixed with a one-line pointer to PROGRESS.md as the actual status
  source, rather than syncing two lists that would just drift apart again.
- README's real-provider setup instructions were bash-only (`export LLM_PROVIDER=ollama`) on a
  project actually built on Windows, where `export` is not valid PowerShell/cmd syntax — confirmed
  against this session's own environment. `backend/.env.example` already documented both variables
  and `main.py` already calls `load_dotenv()`, so the cross-platform-safe path already existed in
  the code, README just didn't lead with it. Fixed to lead with copying `.env.example`, `export`
  kept as a documented *nix-only alternative underneath.
- (Also flagged: uncommitted frontend work from the parallel Failure Recovery pass — already
  resolved by the time this entry was written, that work landed as its own commit before round 12's
  report came back.)

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Round 9: 82. Round 10: 70. Round 11: 80. Round 12: 77. A dip, honestly earned by a real finding
(the sixth pattern instance) rather than noise — the same shape as round 10's dip. Six confirmed
instances of the same underlying lesson now, across the narrator, three different API endpoints,
and the calibration layer — "an unguarded boundary where untrusted input or concurrent access meets
code that assumed a single well-behaved caller or well-formed data." My stopping target
remains a hard 95.

---

## 2026-08-24 — My audit loop, round 13: gaming the gate without any race at all

I ran round 13 of my audit loop. I told it the sixth threading instance had likely closed that specific
vein and asked it to (a) verify the `add_and_report` fix quickly, (b) check the one remaining
untouched stateful piece (`AuditLogger`) for completeness, then (c) spend most of its effort on
Bounded & Gated, Measured Accuracy, Real Problem, and Throughput specifically.

**Score: 78/100** (AI Judgment 16/20, Failure Recovery 17/20, Measured Accuracy 11/15, Bounded &
Gated 10/15, Throughput 8/10, Real Problem 8/10, Submission Readiness 8/10). Up 1 from round 12 —
Failure Recovery jumped +5 on verified confirmation the fix holds and `AuditLogger` is genuinely
clean, offset by real drops in Bounded & Gated and Measured Accuracy from a new, different kind of
finding: not a race this time, gaming via an entirely ordinary sequence of legitimate calls.

### `add_and_report` — verified, holds. `AuditLogger` — checked carefully, genuinely clean

Traced `add_and_report`'s lock scope line by line and grepped the entire live app for any
`.add(`/`.report()` pair outside it — none exist; the only two production call sites (`pipeline.py`,
`confirm_human_resolution`) both correctly use the atomic path. Then checked whether `AuditLogger`
had the identical gap and found a good, well-reasoned answer for why it doesn't: it has no
destructive operation at all (no `clear()`, no delete endpoint) for anything to race against, each
row is self-contained by `run_id`, and `/api/run` is fully synchronous — `log_many()` always
completes before a `run_id` is ever returned to a client, so a client cannot structurally race its
own read against its own write. First genuinely clean result in six rounds of checking this class
of thing — worth trusting, not re-litigating every future round.

### THE FINDING: the calibration gate can be gamed with zero threading race, using only the "Run batch" button

`generate()` is fully deterministic per seed — verified directly, not assumed: three independent
`generate(seed=42, ...)` calls produce the byte-identical 18-transaction narration queue, every
single time (4 `duplicate_refund` / 6 `genuine_error` / 8 `netting_trap`). `CalibrationHistory` has
never deduplicated by `transaction_id` — every `add`/`add_and_report` call inserts unconditionally,
and `calibrate()` treats every row as an independent Wilson trial. The dashboard defaults the seed
to 42 and never changes it unless a user explicitly clicks "Randomize."

**Net effect, entirely without any race:** repeatedly clicking "Run batch" against a real provider
re-observes the identical small set of cases and inflates the Wilson `n` with *correlated*, not
independent, samples — a sequence of individually ordinary, legitimate API calls that undermines
the exact thing the gate exists to guarantee. **This wasn't hypothetical — it was already visible in
this project's own committed evidence**: `docs/evidence/real-ollama-run-2026-08-24.json` reports
`duplicate_refund n=15`, but seed 42 alone only ever produces 4 distinct `duplicate_refund`
transactions. The accumulated 15 could only have come from re-scoring those same 4 cases across
several separate runs over the course of this build. Verified directly: `wilson_score_interval(40, 40)`
(the shape a repeated-but-correct small case set produces) returns a lower bound of **91.2%** — which
would have cleared the 90% threshold on 4 real-world cases, not 40, had accuracy stayed this high.
No live wrong-auto-resolve has actually happened yet (every affected category's *current* CI still
sits below 90% independent of this gap), but the mechanism was proven, not speculative — the single
most central claim this whole project makes ("only auto-resolves what it's *proven* itself accurate
on") was resting on an assumption about sample independence that nothing in the code enforced.

### The fix: gate on distinct cases, not just decision count

Added `MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE = 15` and a new `distinct_transaction_count` field
to `CategoryCalibration` (`len({d.transaction_id for d in real_items})`), checked *in addition to*
the existing Wilson bound, not instead of it — the statistical-confidence requirement and the
evidence-diversity requirement are both real and neither substitutes for the other. Considered
folding distinct-count directly into the Wilson math instead (using it as the sample size in place
of raw `n`) but rejected that: it would change what the extensively-tested existing `n`/`correct`/
`ci_lower` fields mean, versus adding one new, independent, purely-additive check that can only ever
turn an `auto_resolve` into an `escalate`, never the reverse — verified this against every existing
test that asserts `decision == "auto_resolve"` before writing the fix (all of them already use ≥15
distinct transaction_ids in their fixtures, so none needed changing).

Surfaced in the UI too, not just the API: `CalibrationPanel.tsx`'s N column now shows `"15 (8
distinct)"` when the two numbers differ, with a tooltip explaining why — a judge should be able to
see this distinction without reading the source. Two new tests: one reproduces the exact gaming
shape (4 distinct cases, each re-scored 10 times, 100% accurate, `ci_lower` alone would clear 90%)
and asserts it still escalates; the other proves the fix doesn't over-block legitimate accumulation
(40 *genuinely* distinct transactions at the same accuracy still auto-resolves past the floor).
Verified live against the actual accumulated demo history after restarting the dev server (it had
gone stale mid-session and needed a restart to pick up the change — caught by noticing the response
didn't include the new field at all): `duplicate_refund` sits at 8 distinct (of this project's own
real accumulated testing this session, not a synthetic example) — correctly still escalating, now
with a reason string that says exactly why. `netting_trap` happens to sit at exactly 15 distinct —
right at the floor — and correctly falls through to the (still-failing) Wilson check underneath,
proving the two gates compose correctly rather than one silently overriding the other.

### Two smaller Submission Readiness findings, also fixed

- `PROGRESS.md`'s matching-engine line claimed "199-seed fuzz check" as part of its 6/6 passing
  tests — that sweep was real, but a one-time manual verification during a specific bug fix
  (BUILD_LOG's 2026-08-23 entry), not a committed automated test. Unreproducible via `pytest
  tests/`, the README's own documented verification step — the same "stale/unreproducible claim"
  class caught roughly seven times before, recurring once more in a spot not previously checked.
  Reworded to separate the 6 committed fixed-seed tests from the historical one-time sweep.
- `main_n`/`stress_n` on `RunRequest` had no bounds, unlike `threshold` and `provider` right next to
  them. Doesn't crash unbounded (verified: a negative value degrades gracefully to an empty batch,
  now also a committed regression test) — this was about closing the inconsistency and a mild
  availability risk, not a live crash. `Field(ge=1, le=2000)` / `Field(ge=0, le=2000)` added for
  symmetry.

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Round 9: 82. Round 10: 70. Round 11: 80. Round 12: 77. Round 13: 78. Still oscillating rather
than climbing cleanly toward 95 — six threading-shaped findings are now genuinely closed and
holding under repeated re-verification, but this round found a real gap in a completely different
dimension (statistical validity of the evidence itself, not concurrency), which is a healthy sign
the loop is still finding real things rather than exhausting a single vein. My stopping target
remains a hard 95.

---

## 2026-08-24 — My audit loop, round 14: a new high-water mark, and an honest answer on 95

I ran round 14 of my audit loop, deliberately not anchoring it to round 13's own score — I told it to
reach its own independent number rather than adjust round 13's 78, and to give me a direct, honest
opinion on whether 95 is realistically reachable through more code changes, given thirteen rounds had
never exceeded 84.

**Score: 84/100** (AI Judgment 16/20, Failure Recovery 17/20, Measured Accuracy 13/15, Bounded &
Gated 14/15, Throughput 8/10, Real Problem 9/10, Submission Readiness 7/10) — ties round 3's record,
reached independently rather than by anchoring. Bounded & Gated and Measured Accuracy both recovered
from round 13's dip: read `calibrator.py` end-to-end, confirmed the distinct-transaction gate is
wired exactly as claimed, and specifically checked whether `/api/transactions/evaluate` could feed
the gate at all (it can't — grepped the whole file, confirmed it only ever calls `.report()`, never
`.add()`/`.add_and_report()`, so the "hand-crafted near-duplicate scenarios" vector doesn't
structurally exist) rather than assuming the fix's scope. Then live-verified the fix against the
real accumulated demo history, not just test fixtures: the live calibration table showed
`netting_trap` sitting at exactly 15 distinct transactions with its Wilson CI independently still
failing 90% at the time — the two gates composing correctly under real data.

### Two small, genuine findings, both fixed

- **The three committed evidence JSONs had gone stale relative to the fix they're meant to
  demonstrate.** `calibrator.py`'s `reason` strings now always mention "across N distinct
  transactions"; the committed files (frozen at generation time, as evidence files necessarily are)
  still showed the old format. **Regenerated the Ollama evidence file** (free, local, no cost) —
  and the fresh run turned up something genuinely better than what it replaced: `netting_trap` has
  now, for the first time in this project's history, accumulated enough real distinct evidence (15
  transactions, 90.4% Wilson lower bound) to **actually auto-resolve with a real provider** — 8 real
  `netting_trap` decisions in this one run show `"decision": "auto_resolved_calibrated"` in the raw
  output, not just escalated. That's the calibrated-autonomy pitch paying off end-to-end with a real
  model, not just structurally possible in theory — added to README as its own headline claim, not
  buried. Also found a 4th independent real observation of the confidence-matches-recall-tool
  evidence (round 12's finding): `order_671da51349f1` narrated again in this same run, confidence
  `0.427`, again exactly matching `recall_similar_resolutions`'s `avg_confidence` — updated README's
  "three independent real runs" claim to four, since it's now what's actually in the data. **Did not
  regenerate the two Groq evidence files** — that costs real hosted-API quota on a key that's been
  flagged for rotation five separate times and still not actioned; added a one-sentence note instead
  explaining they predate the fix and why re-running wasn't worth the cost just for a string format.
- **`docs/track04-settlement-reconciliation-copilot.md` §6.5 never described the distinct-transaction
  floor** — the same "architecture doc not updated for a mid-build design change" class this log has
  now caught eight times, per round 14's own count. Added a paragraph describing the gap, the fix,
  and the reasoning, matching the existing style of that section's other "found during the build, not
  planned upfront" callouts. This also restores the accuracy of PROGRESS.md's own claim that the
  spec doc is "kept current through the build" — true again now, not edited to make it true.

### A real, independently-verified bug, unrelated to any of the above

Round 14 brute-forced every `main_n` from 0-2000 (the API's own accepted range, per `main.py`'s
`RunRequest` bounds) and found exactly one value, `main_n=6`, where `generate_main_batch` silently
produced 7 transactions instead of 6: `round(6*0.60) + round(6*0.25) + round(6*0.10) = 4+2+1 = 7`,
one more than the requested total, and the remaining "ambiguous" share's count going negative was
never clamped — `range(-1)` in Python just yields zero iterations rather than raising, so the
overshoot from the first three categories was never caught or corrected. Verified independently
before fixing (reproduced the exact 6→7 mismatch directly), fixed by absorbing any negative
remainder into the clean-match share so the batch total always equals the requested `n` exactly —
correct by construction, not just for the one value found. Re-verified the fix across the full
0-2000 range myself (zero mismatches) before committing a smaller 0-150 sweep as the permanent
regression test, since the full range added ~50s to the test suite's runtime for a fix whose
correctness doesn't actually depend on exhaustive coverage — the absorption logic algebraically
guarantees the total for any `n`, so the committed test is for regression protection, not proof.

### The honest answer on 95

Asked directly, and answered directly rather than manufacturing a path: **round 14 does not believe
95 is realistically reachable through further code changes alone**, for two reasons that echo what
rounds 4 and 5 already concluded independently, earlier in this same log:

1. Throughput's ceiling is the free-tier/local-inference choice itself — a real, deliberate
   cost constraint I set myself (my own standing instruction to prefer cheap/local models over
   a paid tier), not an engineering gap. Real Problem's Merkle-tree disclosure ("no comparison
   saving at this project's own dense demo distribution") is an honest property of a hackathon-scale
   synthetic batch, not a defect — scoring either higher would mean spending exactly the resources
   this build has repeatedly, deliberately chosen not to spend.
2. Submission Readiness has a structural ceiling of its own, not just a discipline one: with 2000+
   lines of BUILD_LOG cross-referencing README, PROGRESS, the spec doc, and now four evidence JSONs,
   every fix has a nonzero chance of quietly invalidating some claim elsewhere. This is the eighth
   distinct instance of exactly that failure class across fourteen rounds, each time in a new spot —
   not because of carelessness, but because the surface area of hand-maintained cross-references
   grows with the project faster than any single round can audit it closed.

### Score trajectory so far

Round 1: 71. Round 2: 79. Round 3: 84. Round 4: 83. Round 5: 72. Round 6: 70. Round 7: 74. Round 8:
78. Round 9: 82. Round 10: 70. Round 11: 80. Round 12: 77. Round 13: 78. Round 14: 84. Fourteen
rounds, a new tie for the highest score yet, reached independently rather than by drift. Given the
honest ceiling assessment above — echoed by three separate rounds now (4, 5, 14) — the next move
this loop makes should be a direct decision from me about whether to keep spending rounds
chasing 95 through code alone, or whether the remaining gap needs a decision only I can make
(accept the ceiling, or spend the resources three independent rounds have identified as the actual
blockers). Not a decision to make unilaterally from inside the loop.

### After 95: an architecture pass for real scale, before the first push

Presented with the honest ceiling above, I decided outside the loop to move toward push
prep, but first check whether a different architecture — not more code changes to the same one —
could change the picture, because my plan is to actually deploy this on real, larger data, not
just clear a rubric. Three tiers came out of that analysis, ordered by risk and leverage. Tier 1 —
wire the already-built-but-unused Merkle-tree pre-filter (`matching/merkle.py`) into the live
pipeline, and benchmark it honestly at a realistic scale — I approved doing now, before push prep.
Tiers 2 (worker-pool narration, frontend pagination) and 3 (Postgres, async job queue) I'm deferring
to a real post-push production decision.

**Groundwork: a `clean_ratio` generator parameter.** The demo batch is deliberately dense (~33-40%
non-clean) so every category class is reliably exercised even at small N — nothing like a real
settlement batch, which is overwhelmingly clean. Added `clean_ratio` to `generate_main_batch`/
`generate()` so a realistic large batch (e.g. 50,000 records, 97% clean) could be generated for an
honest benchmark. First attempt generalized the hardcoded 60/25/10/5% split into a ratio-derived
formula (`non_clean_ratio * 0.625`, etc.) — checked directly, not assumed, whether this was actually
equivalent to the original at the default 60% split, and it wasn't: 62 separate values of `n`
between 0 and 2000 round to a different integer than the original literal `round(n*0.25)` etc.,
purely from floating-point arithmetic order. Fixed by branching: the exact original literal
expressions stay for `clean_ratio=0.60` specifically; the general formula only runs for a non-default
ratio, where it was never tested against the original anyway. Verified at n=50,000/clean_ratio=0.97:
exactly 48,500 clean_match (97.00% precisely), with the remaining 1,500 split proportionally across
the other categories. Two new tests cover both the byte-for-byte default-path parity and the sparse
large-batch shape.

**The pre-filter itself.** `matching/merkle_prefilter.py` compares two per-key views — the ledger's
expected amount and the settlement's actual amount, each tagged with the SLA verdict (`"{amount}|OK"`
vs `"{amount}|LATE"`) — so that "identical" under Merkle means exactly Pass 1's own clean_match
condition (`ledger_gap == 0 and within_sla`), not just matching amounts. `run_matching_engine` grew
an optional `merkle_clean_ids` parameter: for ids the pre-filter has already proven clean, it skips
straight to the same `clean_match` result Pass 1 would produce, instead of running Pass 1/2's checks.
Two correctness tests before anything else: the pre-filter's "provably clean" set matches the chain-
derived ground truth exactly (`ledger_gap == 0 and within_sla`) at both the dense default and sparse
ratios, and — the invariant that actually matters — running the matching engine with vs. without the
Merkle-derived clean set produces byte-identical `MatchResult`s for every transaction, across four
seeds and three clean ratios, not just the fast-pathed ones. An optimization that changes even one
result is a correctness bug, not a stretch goal, so this ran before any benchmark did.

**What the honest benchmark actually found.** At n=50,000/clean_ratio=0.97: 1,500 real divergences,
found exactly (zero false positives, zero false negatives) — but `comparisons_made` was 22,279 out
of 50,000 brute-force (44.6%), nowhere near the 94%+ reduction the existing dense-vs-sparse framing
in `test_merkle.py` might suggest. The reason is real and specific to this data: the generator
shuffles every record together (`generate_main_batch`'s own `rng.shuffle`), so the ~3% divergent keys
are scattered uniformly across the full key space rather than clustered. At branching_factor=16, a
16-key leaf group has only a ~60% chance (0.97^16) of containing zero divergences, so ~40% of groups
still had to be fully descended into and rehashed. Sparse-and-scattered is a materially worse case
for Merkle pruning than sparse-and-clustered, and this batch is the former by construction.

More importantly: wired into `run_matching_engine`, the wall-clock difference was 181.2ms unfiltered
vs. 179.3ms filtered — about 1% faster. Measuring why turned up the real story. `build_all_chains`
alone costs ~2,403ms at this scale (Pydantic model construction, dominant by two orders of magnitude
over the matching decision logic it's skipping), and `run_merkle_prefilter` itself costs ~259ms
(computing on the order of 100,000 SHA-256 hashes plus the tree levels above them). Skipping Pass
1/2's own already-cheap conditional for the clean majority saves on the order of 2ms — nowhere near
enough to cover the pre-filter's own 259ms hashing cost. Wired into the live pipeline's hot path, this
would be a net regression, not a speedup, and reporting otherwise would be exactly the kind of
overclaim this project has tried not to make anywhere else.

**The actual conclusion, and why it's not a wasted tier.** `matching/merkle_prefilter.py` is not
called from `pipeline.py`. It's correct, fully tested (parity + the honest benchmark, both committed,
not one-off checks), and left as a documented, available capability rather than the default path —
because the default path measurably doesn't benefit from it. The deeper reason is architectural, not
a tuning problem: Merkle hashing's entire value is avoiding transmitting full data across a network
or process boundary before comparing it — two parties each hash their own side locally and only
exchange the tree levels that actually diverge. When ledger and settlement data already live in the
same process, as they do here, there's no transfer cost to avoid, so hashing is pure overhead over
just comparing the two dicts directly. In a real system where ledger and settlement records live in
separate services, the same pre-filter would eliminate most of the cross-service fetches needed to
build a transaction at all — that's where this pattern actually pays for itself, and it's not what
this project's own in-memory implementation has to offer. The real lever for this project's own
path to genuine scale is `build_all_chains`'s ~2.4s at 50k records — a Pydantic-construction cost,
not a matching-logic cost — and that's a distinct, separate optimization target, not something Tier
1 was ever going to solve. Noted honestly rather than folded into a false Tier 1 win.

### Round 15 (2026-08-25) — the final pre-push round, per direct instruction: 82/100

I ran round 15 of my audit loop, explicitly tasked as the last round before the actual `git push`,
telling it not to chase 95 by any means and not to grade-inflate because it's the last one either. Its job
was mainly to verify the Tier 1 write-up above rather than trust it. It re-ran the 50k-record
benchmark independently (got ~2.27s chain-build, ~205ms prefilter, ~149ms vs. ~157ms matching —
same order of magnitude, same conclusion: the prefilter's own hashing cost dwarfs anything it saves),
independently re-confirmed the 62-value floating-point discrepancy by brute force, checked that the
parity test actually exercises the SLA-blown-but-amount-matching case the SLA-tagging design exists
for (it does — `timing_lag` records are present in every test batch), and spot-checked that nothing
from rounds 1-14 regressed (the diff since round 14's commit touches only the generator, the new
Merkle-prefilter module, and docs — zero changes to calibration/escalation/matching-decision logic).
**Score: 82/100** (AI Judgment 16/20, Failure Recovery 17/20, Measured Accuracy 12/15, Bounded&Gated
13/15, Throughput 6/10, Real Problem 7/10, Submission Readiness 8/10) — consistent with round 14's
84, a small deduction for the one real gap below, no regression anywhere else.

Two findings, both minor:

- **Medium — the overflow-absorption guard was never actually swept for the non-default clean_ratio
  branch.** The same `n_ambiguous < 0` guard that fixed `main_n=6` on the original hardcoded
  60/25/10% split sits after the if/else split, so it protects the new ratio-derived branch too by
  the same algebraic argument — but that argument had never actually been tested, only asserted.
  Verified myself (brute-forced n=0-2000 at clean_ratio=0.97, 0.85, 0.95, 0.99, confirmed the guard
  does fire and correctly restore the total, e.g. clean_ratio=0.97 hits negative `n_ambiguous`
  around n=80-83) before adding a committed regression test
  (`test_main_batch_always_totals_exactly_the_requested_n_at_non_default_clean_ratios`) rather than
  trusting the algebra untested — exactly the failure class this log has flagged before (a one-time
  manual check standing in for a committed test). 87/87 tests passing.
- **Low, informational — the commit title ("Wire Merkle pre-filter into live pipeline...") doesn't
  match the actual behavior** (it's explicitly NOT wired into `pipeline.py`'s default path, and the
  commit body/BUILD_LOG/PROGRESS.md all say so correctly). A real headline/body mismatch, but not a
  documentation-drift problem since every prose description elsewhere is accurate — left as-is since
  amending a pushed... not-yet-pushed but already-real commit isn't warranted for a title nit the
  body itself already clarifies.

**The round's own cost/benefit read on Tier 1, worth keeping**: "worth doing, marginally... a
well-executed, low-risk, zero-runtime-benefit addition that strengthens the submission's credibility
narrative without inflating its actual capabilities — exactly the kind of 'know when to stop'
discipline [rounds 4/5/14] already showed." I'm recording this as the final score before the first
push to GitHub, exactly as I'd planned.

### Pitch-readiness pass, after the first push: real screenshots, a real deployment story, honest numbers instead of invented ones

After pushing, I got feedback pointing at real gaps for a pitch video: no visuals in the README, no
concrete money-impact narrative, no detail on what "100%-adversarial" actually means, no deployment
story, and the escalation queue/calibration dial — genuinely my best UX ideas — buried in technical
prose. Some of the example phrasing in that feedback (a specific "12ms" resolve time, a flat
"10k+ tx/day" scaling claim) wasn't something I'd ever measured, so I didn't just paste it in —
I built the same narratives from real numbers instead.

**Frontend UX pass**: a spacing/typography/shadow pass, color-mapped legends on the calibration and
baseline charts, a staggered reveal on the escalation queue and audit log (capped at ~1.2s, keyed
off the escalations list's own reference so it only plays on a genuinely new run, never on a
threshold drag), a real empty/loading state instead of a blank flash, a positive "clean sweep" state
when nothing escalates, and a dismissible 3-step guided tour of escalate → resolve → recalibrate
that highlights the real first escalated case (tool-call trace included) and auto-advances the
moment I actually click Resolve. `tsc -b`/`npm run build`/lint clean; verified live in headless
Chromium with zero console errors across a normal run, a zero-escalation run, and a slow-rerun-over-
stale-data scenario.

**Real screenshots, not mockups**: captured live via Playwright against a real (mock-provider) run
— the empty state, the summary tiles + baseline comparison, the calibration dial mid-drag, an
escalated case with its tool-call trace expanded (the actual `check_batch_anomalies`/
`check_sla_window`/`recall_similar_resolutions` args and results), and the guided tour highlighting
that same case. All five are in `docs/screenshots/` and now embedded in the README.

**A real-numbers narrative, not an invented one**: pulled three actual transactions straight out of
a real generated batch (seed 42) — a ₹49,823.00 UPI settlement that landed on day 4 against a 2-day
tolerance (amounts matched exactly, so it's `timing_lag`, not a real gap), a netting-trap pair that
nets to zero at the batch level (₹150.00 short on one side, ₹150.00 over on the other) but is wrong
on each individual leg, and a ₹153.74 refund legitimately issued once but deducted from the
settlement twice. Also pulled the real committed evidence: `netting_trap` auto-resolving
₹59,97,863.76 with a 90.4% CI lower bound over 15 distinct cases, while `genuine_error` at 82.9%
measured accuracy stays escalated anyway, by design.

**A stress-test table with real citations**: a markdown table naming each adversarial case
(timing lag, currency rounding, netting trap, duplicate refund, genuine error), what a naive
amount+date matcher actually does wrong on each (citing the exact committed tests —
`test_naive_baseline_silently_misses_timing_lag`, `test_naive_baseline_false_positives_on_rounding_noise`
— rather than asserting it), and what this system does instead.

**A deployment story, honestly caveated**: `backend/Dockerfile`, `frontend/Dockerfile`, and a root
`docker-compose.yml`. I don't have Docker installed in this dev environment, so I said so directly
in the README rather than claim it's been run — written and reviewed carefully (checked the real
`requirements.txt`/`package.json`, checked how `VITE_API_BASE_URL` and the SQLite data directory
actually resolve, noted the real `OLLAMA_HOST`/`host.docker.internal` gotcha for reaching a
host-machine Ollama from inside a container), but that's not the same claim as "verified working."
For the "how far does one instance actually go" question, I computed rather than guessed: the real
Ollama evidence run processed 120 transactions (18 narrated) in 55.8 measured seconds — 2.15 tx/sec
— which extrapolates to roughly 185,000 tx/day sustained on one instance, free local inference, no
API cost. That's a real, derived number, clearly labeled as extrapolated rather than measured at
that volume, and it's a stronger, truer claim than the "10k+/day" the original feedback suggested.

### Borrowing from other domains, deliberately: a circuit breaker and an EWMA drift check

After the pitch-readiness pass, I asked myself whether techniques from other engineering domains —
not just finance — could genuinely strengthen this without being novelty for its own sake. Checked
a few candidates against my own data before proposing anything: Benford's Law (forensic accounting)
turned out NOT to apply here, since `AMOUNTS_INR` draws from a fixed list of 9 round values, not a
naturally log-distributed range — I verified this before suggesting it, and dropped it rather than
force a claim that wouldn't hold. Picked two that did hold up: a circuit breaker (reliability
engineering) and an EWMA drift check (statistical process control, Six Sigma).

**Circuit breaker** (`app/narrator/circuit_breaker.py`): a real gap existed in how the narrator
handles a genuinely down or rate-limited provider. `_call_with_retry` already retries a single
transaction's own call, but nothing remembered that fact across transactions — a rate-limit storm
or a downed Ollama service currently gets re-discovered from scratch by every remaining transaction
in the queue, each paying the full retry-with-backoff cost before failing safe. Added one breaker
per provider: after 3 consecutive *real* API/connectivity failures (deliberately not model-reasoning
failures like a malformed answer — the provider responding fine, just unusably, isn't evidence it's
unhealthy), it stops attempting calls for a cooldown window and fails safe immediately. Wrote the
unit tests for the breaker class itself first (injectable clock, no real sleeping, 6 tests covering
open/close/half-open/thread-safety), then integration tests proving it's actually wired in
correctly — and one of those integration tests caught a real bug in my own first wiring attempt: an
early `CircuitBreakerOpenError` return path referenced `tool_calls_log` before it was assigned in
that call, a `NameError` I'd have shipped without the test. 11 new tests, all passing.

**EWMA drift check** (`app/calibration/drift.py`): the Wilson CI in `calibrator.py` is an all-time
aggregate — once a category earns trust, it stays trusted as long as the aggregate holds up, which
is exactly what makes it slow to react to a genuine *recent* regression. Added an EWMA-based check
(Montgomery's control-chart formula for monitoring a proportion) as an additional gate alongside
(not instead of) the Wilson CI and distinct-transaction floor, the same pattern that floor itself
established. Building this the honest way — parameter search against real scenarios rather than
picking numbers that felt right — found something worth recording: my first attempt (a category
with 100 historical-correct decisions and 2 wrong ones appended at the very end) tripped the
detector even though 2/102 is completely normal variance for a 98%-accurate process; a wide search
over lambda/control-limit combinations couldn't separate that case from a genuine sustained decline
using a single parameter set on data of this size. The real issue turned out to be two *pre-existing*
tests that clustered their "wrong" decisions at the tail of a list purely for construction
convenience, with no actual temporal meaning — an artifact my new recency-sensitive check was
correctly reacting to. Fixed by scattering those two tests' failures to a realistic (non-tail)
position rather than tuning the detector to tolerate an artificial ordering; re-verified the
untouched default parameters (lambda=0.3, 3-sigma) correctly ignore scattered historical misses AND
correctly catch a genuinely sustained recent decline. Also found and fixed a real, independent bug
while wiring this in: `CalibrationHistory`'s two `SELECT` queries had no `ORDER BY`, so the row order
SQLite happened to return (insertion order, in practice, but never guaranteed by the SQL standard)
was being relied on implicitly for the first time — added `ORDER BY id ASC` explicitly rather than
build a time-ordering feature on an undefined assumption. 7 new tests for the drift module itself
(including a hand-computed EWMA arithmetic check, not just "looks reasonable"), plus the two
existing calibration tests fixed. `CategoryCalibration` gained `ewma_accuracy`/`drift_alert` fields,
surfaced in `CalibrationPanel.tsx` as a `⚠ recent EWMA X%` note when triggered — verified it renders
without error via a live headless-browser check (no real drift scenario exists in the demo data to
show the badge actually firing, but the field is real and load-bearing, not decorative).

**`scripts/audit_calibration.py`**: a small, real addition to make the calibration numbers this
README quotes independently checkable — reads `data/calibration_history.db` read-only and calls
the exact same `calibrate()` function the live app calls, rather than just linking to a JSON
snapshot. Running it against the real, live database turned up one more honest bug on the first try:
printing the ₹ glyph (U+20B9) crashed with `UnicodeEncodeError` on Windows' default terminal codepage
(cp1252) — fixed by using "Rs." for this script's plain-text output, the same convention the
narrator's own `_rupees()` helper already uses. Running it for real reproduced the netting_trap
auto-resolve and genuine_error escalation this README describes, live, from the committed database —
not re-typed from a screenshot.

**README overhaul**: reorganized for narrative flow (impact-first, not architecture-first — money
story and screenshots before the technical deep-dive), added a Quick Start, promoted the real
netting_trap auto-resolve result out of a buried paragraph into its own headline section, added a
one-line business-impact parenthetical to each "what makes this different" point, added the new
circuit-breaker/drift-check capabilities as their own points in that same list, and added a
"Reproducing the results" section pointing at the real DB paths, the real evidence JSONs, and the
new audit script — plus an honest "what it doesn't do yet" section consolidating every disclosed
limitation in one place instead of scattered through the file. Test count now 105.

### Round 16 (2026-08-25) — a new all-time high: 87/100

I ran round 16 of my audit loop specifically to check the three commits since round 15 (the doc
voice rewrite, the pitch-readiness pass, and the circuit-breaker/EWMA-drift addition) before
pushing any of it. Told it explicitly not to grade-inflate and to treat the drift feature's two
test-data reorderings as the single most judgment-sensitive thing in the diff, deserving a clear
verdict either way, not a rubber stamp.

**Score: 87/100** (AI Judgment 17/20, Failure Recovery 18/20, Measured Accuracy 13/15, Bounded&Gated
13/15, Throughput 9/10, Real Problem 8/10, Submission Readiness 9/10) — a new all-time high, beating
round 14's 84.

**The judgment call, verified independently rather than taken on my own word**: reading the actual
diff of both modified tests, every assertion (`n`, accuracy, CI bounds, decision outcome) is
byte-for-byte unchanged — only the position of the pre-existing wrong decisions moved from the tail
to the middle of the list. Since EWMA drift is order-sensitive but `n`/accuracy/Wilson-CI are not,
this is confirmed as the minimum change needed to make each test represent what its own docstring
always claimed, not a loosened detector or a swept-under-the-rug finding.

**Everything else independently re-verified, not just re-described**: the circuit breaker's state
machine (closed→open→cooldown→half-open→close-on-success) is textbook-correct, including that a
failed half-open trial correctly re-extends the open window; `record_failure()` confirmed to appear
at exactly the two real-API-failure call sites, never in a model-reasoning-failure handler; the
EWMA arithmetic in `test_drift.py` checked out by hand; `scripts/audit_calibration.py`'s live output
matched exactly; the Docker disclosure ("written and reviewed, not verified") was itself verified
honest by confirming Docker really isn't installed here; the ~185,000 tx/day throughput
extrapolation's arithmetic checks out and is correctly labeled as extrapolated, not measured; three
of the five screenshots were opened and cross-checked against `audit_calibration.py`'s own live
numbers, confirming they're real, current captures, not stale or fabricated.

Two low findings, both disclosed limitations rather than bugs: the circuit breaker's half-open state
can let more than one concurrent trial through under genuine concurrency (already named as an
accepted simplification in the module's own docstring), and the EWMA drift check's `target` is the
category's own current aggregate accuracy, recomputed every call — a long enough streak of wrong
decisions could itself drag the aggregate down over many cycles, narrowing the gap the detector
relies on. Not a bug, matches the documented design intent, just not covered by a test scenario that
mixes a shifting target with the calibrator's own aggregate — worth a note for a future round, not a
fix demanded now.

No critical or high findings. Test count confirmed 105/105, all diff scope matched what was
described going in, nothing unrelated slipped through.

### Two new pillars: fee-leak detection and ERP posting — and a real regulatory correction along the way

I was handed a detailed strategy document proposing a genuine repositioning: not just a
reconciliation copilot, but the layer that audits fees against the merchant's own contract and
posts resolved transactions straight into ERP-ready, GST/ITC-separated journal entries. Before
building any of it, I checked the document's own load-bearing claims rather than trusting them,
because the whole pitch rested on them: are the named competing Razorpay products (Recon,
Settlement Insights) real, and is the regulatory citation behind the flagship fee-leak pattern
actually current?

Both named products turned out to be real — Razorpay Recon (Dec 2024, AI-powered rule-based batch
matching across 200M+ transactions/month) and Settlement Insights (launched March 12, 2026 as part
of Agent Studio, a WhatsApp daily-summary agent — not quite the "Q&A dashboard" the document
described, closer to a plain summary push). But the regulatory citation was a real problem: the
document's Pattern 1 asserted that any MDR charged on UPI/RuPay debit is unconditionally illegal,
citing Section 10A of the Payment and Settlement Systems Act's zero-MDR mandate (in force since
January 2020). Parliament amended that Act on 4 August 2026 — three weeks before I checked this —
replacing the blanket prohibition with a government-notification framework. Shipping the blanket
claim would have gone stale the week this feature launched, in front of judges who would plausibly
know about a change to a six-year-old, high-profile payments law. I flagged this before writing any
code and got the call to build everything else in the document but fix that one framing.

**The fix, and why it's actually the more robust design, not just a workaround**: instead of
checking a fee against "what the law currently allows," the detector checks it against **this
merchant's own contracted rate** (`fee_schedule.py`'s existing `FEE_PCT`, which was already the
contracted-rate reference every clean transaction in this generator has used from day one). That's
correct regardless of how the regulatory notification framework evolves — a contract-vs-actual
comparison doesn't go stale the way a hardcoded legal assumption does.

**Fee leak detection** (`app/feeleak/detector.py`, `app/data_gen/generate.py`'s
`generate_fee_leak_batch`): a genuinely separate axis of analysis from reconciliation, not folded
into the matching engine's own categories. The generator's key design insight: a fee-leak
transaction must reconcile *perfectly cleanly* (ledger and settlement both consistently reflect the
actual, overcharged fee) — that's the real-world blind spot, since standard reconciliation only
checks whether the two sides agree with each other, never whether what they agree on is itself
correct. Two patterns, both with real synthetic examples and real tests: a blended-rate overcharge
(a flat rate applied instead of the instrument's own contracted one, most visible on UPI) and GST
computed on the gross amount instead of the fee. `test_zero_false_positives_against_every_existing_category`
is the test that actually matters most here — 260 ordinary transactions from the main/stress
batches, zero false positives, verified directly rather than assumed, since a detector that flags
correctly-charged transactions would undermine the whole "honest numbers" discipline this project
holds itself to everywhere else.

**ERP journal generation** (`app/erp/journal.py`, `app/erp/exporters.py`): turns a resolved
transaction's causal chain into balanced double-entry journal lines. Designed the balance to be
provable algebraically, not just empirically: Revenue always credits at the chain's own gross
captured amount; Bank/Fee/GST/Refund debit at their recorded amounts; and a Reconciliation Suspense
line absorbs exactly `-settlement_delta`, which I verified by hand holds for both positive and
negative delta before writing the test — meaning every entry balances by construction, proven
across all 8 real transaction categories in `test_journal.py`, not hand-picked clean ones. GST-on-fee
always posts to its own Input Tax Credit Receivable line, never merged into the fee expense — the
actual mechanism that makes ITC reclaim automatable instead of a manual bookkeeping exercise.

Three real export formats. Before writing the Tally XML exporter, I fetched Tally's own published
sample XML (help.tallysolutions.com/sample-xml/) rather than reconstruct the format from memory,
and it revealed a real, non-obvious detail I'd have gotten wrong otherwise: a debit line's `AMOUNT`
is negative and a credit line's is positive in Tally's own documented convention — the opposite of
what I'd have assumed. `test_journal.py` checks this sign convention explicitly, not just that the
XML parses. The Zoho Books CSV and generic CSV use a standard, defensible column shape that wasn't
independently verified against Zoho's current live template the same way — disclosed as such,
consistent with how every other unverified claim in this project has been handled.

Wired both into the pipeline (`pipeline.py`'s `run_batch` now returns `fee_leak_report` and
`total_itc_separated` on every `BatchRunResult`) and exposed via a new endpoint,
`GET /api/journal/export?format=tally|zoho|generic`, which regenerates the latest run's own chains
from its seed (the same pattern `api_run` already uses to recover ground truth) rather than
extending the atomicity-critical `_RunSnapshot`. One real design correction caught during this pass:
`total_itc_separated` needed to be its own field, computed from the WHOLE main batch's journal, not
reused from `fee_leak_report.total_gst_correction` (which is specifically the wrongly-computed-GST
correction found in the separate leak-review sample) — two genuinely different numbers that would
have been silently conflated if I hadn't caught it, with a dedicated test
(`test_total_itc_separated_is_real_and_distinct_from_the_fee_leak_correction`) protecting the
distinction going forward.

23 new backend tests (7 detector, 9 journal, 4 API-level, 3 pipeline-integration), all written
against real generated data, not synthetic hand-picked examples alone. Frontend work (two new
summary tiles, a Fee Leak Analysis view, an ERP Export view with real downloads) delegated to a
background agent while I did the backend/README work — reviewed its diff before trusting it, same
discipline as every other delegated piece this session.

README rewritten with the new capabilities woven through: a corrected opening (leads with the fee
audit + ERP posting story, not just reconciliation), a new "Where this fits in Razorpay's own
stack" section naming the real competing products and stating the regulatory correction plainly
rather than burying it, dedicated sections for both new pillars with real numbers (verified live,
not copied from the strategy document's own illustrative figures), two new points in "what makes
this different," and updated "what it doesn't do yet" scope notes (Tally XML verified against
documentation but not a live install; only 2 of the fee-leak taxonomy's patterns have real
synthetic examples, not all of them).

Test count now 128. Score to be recorded by the next audit round before any push.

### Round 17 (2026-08-25) — 91/100, another new high

I ran round 17 specifically to check the fee-leak/ERP work above before pushing it, with explicit
instructions to independently fact-check the regulatory claims itself — via its own web search, not
by trusting my account of what I'd found — since that was the single highest-stakes, most
reputationally-risky claim in the whole submission if it turned out wrong.

**Score: 91/100** (AI Judgment 19/20, Failure Recovery 18/20, Measured Accuracy 14/15,
Bounded&Gated 14/15, Throughput 9/10, Real Problem 10/10, Submission Readiness 9/10) — another new
all-time high, beating round 16's 87.

**The regulatory fact-check, confirmed independently rather than taken on my word**: it searched for
Razorpay Recon and Settlement Insights directly and confirmed both are real, launched when and as
described. It searched for the Payment and Settlement Systems Act amendment separately and found
multiple independent sources (Business Standard, Deccan Chronicle, TechTimes, government press
coverage) confirming Parliament passed the amendment to Section 10A on 4 August 2026, replacing the
blanket zero-MDR mandate with a government-notification framework — exactly what I'd found and
exactly what README.md states, "not overclaimed... and not underclaimed." It also checked whether
the fee-leak detector's redesign (checking the merchant's own contract instead of the law) was a
genuine architectural fix or just relabeling, by reading `detector.py` directly: confirmed the check
never references what's legally permitted at all, only what was contractually agreed — a
structurally different, durable check under any regulatory regime, not cosmetic.

**Everything else independently re-verified, not just re-described**: worked through the journal
balance algebra by hand and confirmed `debit_total - credit_total = settlement_delta + suspense = 0`
holds for any sign of `settlement_delta`, not just the cases I'd tested; fetched Tally's own
published sample XML directly and confirmed the sign convention `to_tally_xml` implements (and that
`test_journal.py` actually asserts, not just checks the XML parses) matches exactly; confirmed
`fee_leak_report.total_gst_correction` and `total_itc_separated` are genuinely different code paths
computing different things, and that `SummaryTiles.tsx`'s current code uses the correct one;
reproduced all three headline ₹ figures (₹2,634.50 / ₹23,158.96 / ₹2,198.42) by running the same
seed itself; confirmed `test_zero_false_positives_against_every_existing_category` really does use
main_n=200/stress_n=60 as claimed, not a thinner check; confirmed 128/128 tests pass and the diff
since round 16 matches exactly what was described, nothing unrelated slipped in.

No critical or high findings. One low-severity, honest observation: no dedicated failure-injection
test for the ERP export path (e.g. calibration-history state changing between a run and a later
export call) — a genuine but minor untested edge, not a discovered bug, noted for a future round
rather than blocking this one.

This clears the user's own stated bar (push once the score is above 85) — recording this as the
score before the push.

### A "final action plan" with a fabricated API endpoint, caught before I wrote a line of connector code

I was handed another external strategy document — this one framed as a response to specific judge
criticism, with a punch list including "the repo still has the old README (push it now)," a
Razorpay sandbox connector spec (with exact endpoints to call), a request to demonstrate 50k-scale
processing, a note that tool-call traces read as "buried," a Groq-key-rotation reminder, and a
paragraph tying this system to Razorpay's agentic-commerce roadmap. Same discipline as the last two
documents: checked the load-bearing claims before acting on any of them.

**The urgency premise was false.** `git log origin/main` was already at the exact commit I'd just
pushed — the README was current, not stale. Stated this plainly rather than acting on it.

**The sandbox connector spec named a real-looking but nonexistent endpoint.** `POST /v1/payments/
test_payment` — the call the document said would "simulate payment capture" — doesn't exist
anywhere in Razorpay's actual API documentation; I searched it directly and found nothing. Real
test payments in Razorpay's sandbox go through the Checkout.js browser flow (a mock bank page with
Success/Failure buttons, test card/UPI numbers), not a single server-to-server call. The document's
recon endpoint was also shaped wrong — `/v1/settlements/{id}/recon/combined` isn't real; the actual
endpoint is `/v1/settlements/recon/combined?year=&month=&day=`, a date-scoped query across all
settlements, not per-settlement-ID. Writing a connector against the endpoint as specified would
have been broken code calling a URL that 404s — worse than not having the feature, especially in
front of judges who work at the company whose API it claims to call. Flagged this before writing
any connector code, and since I have no real Razorpay test credentials to verify anything against
live, I'm not building it blind — waiting on the user to generate and share real test keys before
attempting this for real, rather than shipping unverified integration code with a straight face.

**The agentic-commerce claim checked out.** Razorpay and NPCI really did launch "Agentic Payments"
on Claude at the India AI Impact Summit, 20 February 2026, with Zomato, Swiggy, and Zepto live in
pilot — confirmed across multiple independent sources (Business Today, The Paypers, Razorpay's own
blog). Added the paragraph to "Where this fits," verified rather than assumed.

**What I actually built this round, all directly verifiable:**

- **50,000-transaction evidence run**, real: 50,000 transactions, ₹54,81,13,443.15 total value,
  processed end-to-end (matching + fee-leak review + journal generation) in 9.08 measured seconds —
  5,508 tx/sec, mock provider. Raw output committed at `docs/evidence/50k-batch-run-2026-08-25.json`.
  85.0% resolved without escalation; the remaining 15% (7,500 transactions) escalated honestly
  rather than auto-resolving, since this run used the mock provider and this project's own
  calibration gate never lets mock decisions auto-resolve regardless of accumulated history —
  exactly the property that should hold at scale, not something that quietly breaks under load.
- **Tool-call trace visibility, fixed for real.** The criticism was accurate: the trace was
  collapsed behind a click by default. Now the first (highest-value, least-certain) escalation's
  trace auto-expands the moment a genuinely new run lands — keyed off the same `escalations`
  reference-identity check `EscalationQueue.tsx` already uses to gate its reveal animation, so a
  threshold drag or a resolve never re-triggers or re-collapses it. Verified live: the trace shows
  real tool names/args/results with zero clicks. Retook `docs/screenshots/04-escalation-tool-trace.png`
  to show this.
- **Netlify config** (`netlify.toml`, repo root): deploys `frontend/` as a static build, pointed at
  a `VITE_API_BASE_URL` the user sets to wherever the backend actually runs. Documented plainly why
  the backend itself doesn't fit Netlify's model (stateful FastAPI + SQLite + narrator calls that
  can run minutes against a real provider) rather than pretending a full-stack Netlify deploy is a
  real option. Written and reviewed, not deployed to a live site in this session.
- **Groq key rotation**: still something only the user can actually do (console.groq.com access,
  not mine) — noted honestly rather than claimed as done. `.env`/`.env.example` hygiene already
  correct from earlier in the build.

### Round 18 (2026-08-25) — 90/100, the fabricated-endpoint catch confirmed correct

I ran round 18 specifically to check the response to the third strategy document — the false
urgency premise, the fabricated Razorpay endpoint, the 50k evidence, the tool-trace fix, and the
Netlify config — before pushing any of it, with explicit instructions to re-derive each claim
independently rather than trust my own account in BUILD_LOG.

**Score: 90/100** (AI Judgment 18.5/20, Failure Recovery 17.5/20, Measured Accuracy 14/15,
Bounded&Gated 13.5/15, Throughput 9/10, Real Problem 8.5/10, Submission Readiness 9/10) — smaller in
scope than rounds 16-17 by design (evidence-gathering and one real fix, not new subsystems), and
the score reflects that narrower scope rather than a regression: nothing backend-side was touched
(confirmed — 128/128 tests unchanged), and nothing was overclaimed.

**The fabricated-endpoint verdict, independently re-derived, not taken on my word**: it searched
Razorpay's real API docs itself and confirmed `POST /v1/payments/test_payment` doesn't exist
anywhere — real test payments go through the Checkout.js browser flow, not a single server call —
and that the real recon endpoint is `/v1/settlements/recon/combined?year=&month=&day=`, a
date-scoped query, not the per-settlement-ID path the strategy document specified. It also grepped
`backend/app/` directly and confirmed zero connector code exists — I genuinely didn't build the
feature rather than shipping a broken version of it, which it explicitly called "a legitimate
catch, not an excuse."

**Everything else independently re-verified**: confirmed `origin/main` really was already current
when this round started (the "push it now" urgency was false); searched and confirmed the
Zomato/Swiggy/Zepto agentic-payments claim independently, and that the README's "live in pilot"
wording matches reality rather than over- or under-claiming; opened the 50k evidence JSON and
confirmed every number quoted in the README matches exactly, then reproduced the run itself and got
matching transaction/escalation counts; read the `EscalationQueue.tsx` diff and confirmed the
auto-expand effect is correctly gated on the same reference-identity check the reveal animation
already uses, and that it doesn't re-trigger on a threshold drag or a resolve; confirmed `.env` was
never committed at any point in git history.

No critical or high findings. Two low findings, both honest, pre-existing gaps rather than new
problems: the 50k run only exercises the mock provider, so narrator throughput under a real LLM at
that scale remains unverified; and `elapsed_seconds`/tx-per-second are wall-clock, machine-dependent
numbers presented without an explicit variance caveat. Neither blocks anything.

This clears the same bar round 17 did — recording the score before the push.

---

## 2026-08-25 — Real Razorpay Test Mode connector: what real credentials actually revealed

The user provided real Razorpay test-mode credentials after FINAL_ACTION_PLAN.md's fabricated
`/v1/payments/test_payment` endpoint ruled out any shortcut. Secured them into `backend/.env`
(confirmed gitignored, never touched by git status throughout) and started from the three endpoints
already verified real: `POST /v1/orders`,
`GET /v1/payments`, `GET /v1/settlements`.

**Confirmed working immediately**: order creation, and both list endpoints (empty on a fresh
account — a real, correct response, not a stub). **Confirmed blocked**: S2S direct-card payment
requires PCI-DSS certification, not available here — ruled out cleanly rather than attempted anyway.

**The Checkout.js browser flow, driven with Playwright across 11 iterations**, got further than
expected: real order → checkout iframe (Test Mode banner visible) → phone entry (two "obviously
fake" numbers server-rejected; a plausible one accepted) → payment method selection → card entry →
name/email → save-card prompt → processing screen → a real `payment_id`, every time, even on
failure. But every attempt failed with `international_transaction_not_allowed` — first on the
generic Visa test number, then again on the Mastercard number a fresh search turned up as the
"real" domestic alternative. A screenshot of the payment-methods screen showed why chasing more card
numbers wasn't going to fix it: this account doesn't even list UPI as a payment option, only Cards,
Netbanking, Wallet, and Pay Later. That's an account activation-profile fact, not a card-BIN
guessing game — so the right move was to stop spending Playwright cycles on it, not find a twelfth
card number to try.

**Built the connector around what's actually real.** `app/connectors/razorpay_sandbox.py` maps
`POST /v1/orders`, `GET /v1/payments`, `GET /v1/settlements` onto this project's own
Order/Payment/Settlement/LedgerEntry schemas — `LedgerEntry` has no Razorpay API equivalent at all
(it's an internal "amount we expected," recorded locally the same way a real merchant would at
order-creation time, same as `_build_order_and_payment` does for synthetic data). Wrote 9 tests
against mocked `httpx.Client` — mocked only after the real shapes were captured by hand against the
live account, not guessed from docs — plus a `GET /api/sandbox/status` endpoint that makes real
calls so the connectivity claim is checkable, not just asserted.

**A live smoke test against the real endpoint found a real bug the mocks hadn't caught**: the actual
API returns `notes` as `[]`, not `{}`, when none are set on an order. `body.get("notes",
{}).get("merchant_id", ...)` crashed with `AttributeError: 'list' object has no attribute 'get'` —
a genuine gap between the assumed and real response shape, caught only because the endpoint was
actually run, not just tested against a hand-written mock. Fixed with an isinstance guard, added a
regression test reproducing the exact `[]` shape, re-ran the live endpoint and confirmed it now
returns `{"connected": true, "probe_order_id": "order_TU0OxUMtY3ESs2", "payments_on_account": 1,
"settlements_on_account": 0, ...}` — the `1` is real too, a failed (uncaptured) payment record left
over from the Checkout attempts above, not a captured one.

137/137 tests passing (128 + 9 new). README's honest-scope section, PROGRESS.md's connector line,
and this entry all describe the same thing the same way: real live wiring, a real account-level
finding about why no captured payment exists yet, and a real bug caught and fixed by actually running
the code against the actual API rather than trusting an assumed response shape.

---

## 2026-08-25 — My audit loop, round 19: a real, dormant correctness bug in the connector

Focused round on the just-built Razorpay Test Mode connector, before the first push containing it.
**Score: 82/100** (AI Judgment 17/20, Failure Recovery 17/20, Measured Accuracy 10/15, Bounded &
Gated 11/15, Throughput 9/10, Real Problem 9/10, Submission Readiness 9/10).

Independently re-verified against Razorpay's own docs (not just trusting my write-up) that
`fetch_settlements` mapped two fields — `payment_id` and `method` — that don't exist anywhere in the
real `/v1/settlements` response (confirmed real shape: `{id, entity, amount, status, fees, tax, utr,
created_at}`). The consequence: `_METHOD_TO_RAIL.get(item.get("method", ""), "upi")` silently
fabricated `rail="upi"` for every settlement regardless of the real rail, since the key it read never
exists. Currently dormant — this account has zero real settlements to exercise the path — but a real,
shipped correctness bug in code whose entire stated purpose is faithful field mapping. Worse, the
mock in `test_fetch_settlements_maps_a_real_settlement` had invented those same two fake keys, so the
test was validating the wrong assumption instead of catching it.

**Fixed the same way rounds 5-18 fixed every prior real finding**: verified the real shape myself via
WebSearch before touching code (confirmed the round's account independently), removed the
`_METHOD_TO_RAIL` lookup entirely (it implied translating a real field that doesn't exist), set
`payment_id`/`rail` to explicit documented placeholders instead of a guess, and rewrote the test
against the real response shape (no `payment_id`/`method` keys) with a comment explaining why the
old version had been silently wrong. Also flagged (LOW-MEDIUM, fixed with a docstring note rather
than new machinery): `GET /api/sandbox/status` creates a real order against the live account on every
call, unbounded — not currently polled by the frontend, so not an active problem, but documented as
"manual/occasional use only, not a health-check target" so it doesn't become one by accident later.

Independently confirmed clean: `backend/.env` never committed at any point in git history (`git log
--all -p | grep <key id>` — zero hits), 137/137 tests passing, no stale "128" current-state
references left anywhere. No overclaiming found in the docs' account of what's real vs. not.

This is the recorded pre-push score for the connector work — consistent with this project's standing
rule of running one focused round before a push and fixing what it finds rather than pushing past it.

---

## 2026-08-25 — Docker, actually verified this time, plus a real deployment bug it caught

Before pursuing a hosted deployment, went back to close a gap this project had disclosed honestly
since it was written: the Dockerfiles/docker-compose.yml were reviewed but never run against a real
Docker install, because this dev environment didn't have one. Installed Docker Desktop for real
(needed WSL2 enabled first, then an elevated `winget install`, then a reboot -- one winget attempt
silently "succeeded" without actually installing anything because the elevation prompt had no
interactive user to approve it; the real install only completed once run from an already-elevated
session with a human present to click through it).

**A careful static read of the Dockerfile before running anything caught a real bug on its own**: the
backend `CMD` hardcoded `--port 8000` in exec form, which can't read environment variables at all.
Render (and most PaaS Docker hosts) inject their own `PORT` env var and expect the container to bind
to it -- this would have silently deployed a container listening on the wrong port, unreachable from
outside. Fixed by switching to shell-form `CMD uvicorn app.main:app --host 0.0.0.0 --port
${PORT:-8000}`, which expands the env var if set and falls back to 8000 for plain `docker compose up`
where PORT is never set.

**Then actually verified it, not just reasoned about it**: `docker compose build` succeeded for both
images, `docker compose up` started both containers, `/api/health` responded for real, and a full
batch run was driven live through the Dockerized frontend against the Dockerized backend via
Playwright -- zero console errors, tiles/fee-leak analysis/calibration table/escalation queue all
populated with real data. This is the actual thing the README's own honest-scope section had been
disclosing as unverified; it now is.

Also lowered the dashboard's default batch size (`RunControls.tsx`, `mainN`/`stressN`) from 120/40 to
30/10. 120/40 was this project's own internal dev-testing size -- the exact size that took 11-70
minutes on Groq's free tier (see the earlier Ollama-pivot entry). Once this project gets a public
URL, anyone curious enough to pick "groq" from the provider dropdown without knowing that history
would get a bad first impression through no fault of their own. 30/10 still exercises every category
and finishes fast on every provider; verified live (Playwright, zero console errors, tiles populated
correctly) before committing. 137/137 backend tests still passing; frontend `npm run build` clean.

---

## 2026-08-25 — Actually deployed live: Render + Vercel, judges can use it now

Wanted judges to be able to use the real hosted app, not just read about it, which changed a few
earlier assumptions. Checked (not assumed) whether anything better than Groq existed for a cloud-hosted
real narrator: confirmed the earlier 10+-provider survey's conclusion still holds (every free hosted
LLM tier is rate-limited by design, [[provider_survey_llm_narrator]] in my own notes) and separately
confirmed Fly.io's free tier is gone as of 2026 (requires a card after a 2-hour trial) before ruling
it out as a self-hosting-Ollama option. Landed on: **Render** for the backend (free, no card, real
persistent-enough uptime with a keep-alive) with `LLM_PROVIDER=mock` as the deployed default and Groq
available as an explicit per-run opt-in from the dashboard's own provider dropdown — nobody gets a
slow run forced on them, and anyone curious can choose it themselves.

**Netlify didn't work out during setup** (not diagnosed further at the user's call) — switched to
**Vercel**. Vercel doesn't read `netlify.toml`, so added `frontend/vercel.json`
(`buildCommand`/`outputDirectory`/`vite` framework preset/SPA rewrite) as the equivalent, scoped via
Vercel's dashboard "Root Directory" set to `frontend`. First import attempt auto-detected the
repo's `backend/` as a second deployable service (Vercel's newer multi-service monorepo preset) —
correctly avoided letting it try to deploy the FastAPI backend too (same reason it never fit
Netlify: stateful, SQLite, long-running narrator calls) by pointing Root Directory straight at
`frontend` instead of accepting the auto-detected multi-service config.

**Installed Docker for real this session first**, closing a gap the README had disclosed honestly
since it was written ("reviewed but never run against a real Docker install"). Needed `wsl --install`
(admin, required a reboot) then `winget install --exact --id Docker.DockerDesktop --force` — the
first attempt, run from my own non-elevated shell, silently reported success without installing
anything (a UAC elevation prompt with no interactive user able to click it); the real install only
completed once the user ran the same command themselves from their own already-elevated PowerShell.
`docker compose build`/`up` then verified for real: both images built, both containers started,
`/api/health` responded, and a full batch run was driven live through the Dockerized frontend against
the Dockerized backend via Playwright — zero console errors.

**That verification caught a real, would-have-shipped deployment bug before it reached Render**:
`backend/Dockerfile`'s `CMD` hardcoded `--port 8000` in exec form, which can't read environment
variables. Render (and most PaaS Docker hosts) inject their own `PORT` and expect the container to
bind to it — confirmed via WebSearch before touching code, not assumed. Fixed to shell-form
`CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`. Also lowered the dashboard's default
batch size (`RunControls.tsx`) from 120/40 — this project's own internal dev-testing size, the exact
size that took 11-70 minutes on Groq's free tier — to 30/10, so a judge picking "groq" out of
curiosity doesn't get a bad first impression through no fault of their own; verified live via
Playwright before committing. Made CORS configurable via a new `ALLOWED_ORIGINS` env var
(`backend/app/main.py`) — it was hardcoded to `localhost:5173` only, which would have silently
blocked any deployed frontend from reaching the deployed backend.

**Deployed and verified live**: backend on Render at `razorpay-buildathon-a1p0.onrender.com` (health
endpoint and a real `POST /api/run` batch both verified directly via curl before the frontend was
even up), frontend on Vercel at `razorpay-buildathon-five.vercel.app`, `ALLOWED_ORIGINS` on Render
set to the Vercel URL. Drove the actual public site through a real batch run via Playwright — not
local dev servers, the real deployed URLs — zero console/network errors, every dashboard section
(tiles, fee-leak analysis, ERP export, calibration table, escalation queue) populated with real data,
matching the earlier local Docker verification's numbers. Set up a free UptimeRobot monitor pinging
`/api/health` every 5 minutes so Render's 15-minute idle sleep never triggers during a judging
window — a judge gets an instant response instead of a cold start, and calibration state stays
intact across visits instead of resetting on every restart.

Commits: `c6505ce` (Dockerfile port fix, batch-size defaults, Docker verification), `7574e3b`
(Vercel config). Each pushed with separate explicit user confirmation, same pattern as every prior
push this session.

---

## 2026-08-25 — My audit loop, round 20: a full judge's-eye pass on the live deployment

First round scoped as an actual judge would work, not a diff-focused check: read the README cold,
opened the real live URL in a headless browser and drove it, spot-checked headline numbers against
their source evidence, and specifically tried the one thing most likely to break for a real judge --
picking "groq" from the provider dropdown on the deployed site, where Render only has
`LLM_PROVIDER=mock` set and no `GROQ_API_KEY` configured at all. **Score: 88/100** (AI Judgment
17/20, Failure Recovery 18/20, Measured Accuracy 13/15, Bounded & Gated 14/15, Throughput 10/10,
Real Problem 9/10, Submission Readiness 8/10).

The Groq-with-no-key test confirmed the failure path holds under real conditions, not just a mocked
one: the batch completed, zero console/network errors, 4 transactions escalated cleanly. But the
escalation reasoning shown in that judge-facing field read `"Narrator crashed unexpectedly
(KeyError: 'GROQ_API_KEY'); escalating rather than losing the batch."` -- functionally correct
(fails safe, never crashes), but a raw Python exception leaking into the product surface instead of
a designed message. Root cause: `client = Groq(api_key=os.environ["GROQ_API_KEY"], ...)` sat outside
any try block inside `narrate_groq`, so a missing key fell all the way through to `narrate()`'s
generic orchestration-level backstop (round 8's fix) instead of being named specifically the way
every other known failure shape in this file already is. **Fixed by giving it the same treatment
as every other known shape**: wrapped the client construction in its own `try/except KeyError`
inside `narrate_groq`, returning a clean `"Groq is selected but GROQ_API_KEY isn't configured in
this environment; escalating rather than guessing."` via the function's existing `_fail_safe`
closure -- reusing the established pattern rather than inventing a new one. New regression test
(`test_narrate_groq_fails_safe_with_a_clean_message_when_no_api_key_is_configured`) written against
the unfixed code first to confirm it would have caught this.

Also caught, low severity: the test count had drifted to 137 in README/PROGRESS while the real
count was already 138 (a `pytest -q` run, not assumed) -- the same recurring failure class named
in this project's own docs since round 7-8. Fixed to 139 (the real count including this round's own
new test).

Independently verified clean, not just trusted from BUILD_LOG's own account: `git log --all -p`
across the whole history for `rzp_test`/`GROQ_API_KEY=gsk`/`key_secret` returns only test fixtures
(`monkeypatch.setenv(..., "rzp_test_fake")`), never a real credential; the Dockerfile's shell-form
`${PORT:-8000}` fix and the live `/api/health` GET+HEAD both-200 fix were both re-confirmed live
against the actual deployed URLs, not re-read from the prior BUILD_LOG entries alone. Every headline
number in the README (money story, fee-leak, 50k-scale) was independently reproduced from its
source evidence JSON to the decimal, not just trusted from prose.

No critical or high findings. This is the first round to genuinely evaluate the live, judge-facing
product end to end rather than the code that produces it -- the honest verdict: ready to submit as
it stands, with two small, now-fixed rough edges rather than anything that would mislead a judge or
break under real use.

---

## 2026-08-25 — A real external review caught a genuinely inflated headline number

A detailed, evidence-based external review (framed as a hiring judge's assessment) checked this
project by cloning it, running the test suite, reading the core modules, and checking the committed
evidence files against the code that produced them -- not a skim. Given this session's own standing
discipline of fact-checking every external document before acting on it (two of three prior
strategy documents this session contained real errors), every claim in the review was independently
re-verified before touching anything, not trusted on account of how detailed it sounded.

**The core claim, verified true.** The README's headline -- "the system safely auto-resolved
₹59,97,863.76 in netting-trap exceptions with zero human review" -- was `amount_total` from the
committed evidence JSON, and `calibrator.py`'s own code comment already said what that field is:
"total amount across ALL decisions (real + mock)." For netting_trap specifically: 36 real decisions
across only 15 *distinct* transactions, plus 444 mock decisions never shown in the headline. Every
re-scoring of the same handful of transactions across accumulated development history added that
transaction's rupee amount again. Reproduced independently: `generate(seed=42, main_n=120,
stress_n=0)`'s actual netting_trap set is 8 distinct transactions totaling exactly ₹1,27,500 -- the
review's own reproduction, confirmed to the rupee.

**The second claim, also verified true.** The README told a judge to run `python
scripts/audit_calibration.py` to independently verify the calibration numbers. `backend/data/*.db`
is correctly gitignored (it's the live app's mutable local state, not source) -- so on an actual
fresh clone, that command has nothing to read. Confirmed directly: on a clean sandbox with no local
history, it exits with a clear "no calibration history... run a batch first" message (not literally
the empty-table output the review's transcript showed, a minor discrepancy in exact wording, but the
substance -- a judge cannot reproduce the headline claim -- was fully correct).

**Checked, not assumed, whether the live dashboard had the same bug.** Grepped the frontend: only
`amount_at_risk` is ever rendered (`CalibrationPanel.tsx`), never `amount_total` directly. Since
`amount_at_risk = (1 - accuracy) * amount_total` and netting_trap's accuracy was 100% at the time,
`(1 - 1.0) * anything = 0` -- the live product was never actually showing an inflated number to a
user. But the formula itself was a latent bug: any category that ever auto-resolved at *less* than
100% real accuracy would have inherited the exact same inflation in a real, user-facing field.

**Fixed at the root, not patched at the README.** Added a genuinely correct `distinct_amount_total`
field to `CategoryCalibration` (`app/calibration/calibrator.py`) -- summed once per distinct
transaction_id, immune to re-scoring inflation by construction -- and changed `amount_at_risk`'s
formula to use it instead of `amount_total`. `amount_total` itself is kept (still legitimately useful
for "total value touched including mock," clearly commented now for what it is and isn't). New
regression test reproduces the exact mechanism: 20 distinct transactions each re-scored 3 times,
1 wrong, `amount_total` correctly inflates 3x while `distinct_amount_total`/`amount_at_risk` don't.

**Then went further than a formula fix, since the underlying evidence itself was the real problem.**
Wrote `scripts/generate_verified_evidence.py`, which builds a fresh, dedicated calibration history
from real Ollama batches at different seeds accumulated together -- the honest way to earn
auto-resolve trust (several genuinely different real-world batches, exactly how a real production
system would), not a shortcut. Ran it live: the first 4 batches reached 100% accuracy on
netting_trap across 29 distinct transactions and still hadn't cleared the 90% Wilson lower bound
(88.3%) -- small-sample conservatism working exactly as designed, even at perfect accuracy. A couple
of genuine real misclassifications along the way (a live model, not a scripted answer) pushed the
point estimate to 97-98% before enough further real evidence pulled the lower bound past 90% for
good, at 8 accumulated batches: netting_trap (n=59, distinct=59, 98.3% accuracy, lower bound 91.0%,
₹4,86,473.13 real distinct money) and duplicate_refund (n=37, distinct=37, 100% accuracy, lower
bound 90.6%, ₹1,52,312.37) both genuinely earned auto-resolve. The gate paying off is directly
visible within the last single run too: 18 narrated, only 7 escalated (all `genuine_error`, correctly
never-auto-resolving) -- 11 transactions resolved without a human touching them, live, in that run.

Committed the resulting `docs/evidence/verified_calibration_history.db` (small, dedicated,
NOT covered by the `backend/data/*.db` gitignore rule that correctly excludes live app state) --
`python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db` now
actually reproduces the headline claim on a genuinely fresh clone. README rewritten around the real
numbers throughout: the money-story headline, the "Calibrated autonomy" section (now honestly
describing an 8-batch accumulation with real setbacks along the way, not a clean one-shot win --
arguably a stronger story than the original), the reproducibility instructions, and the stress-test
figure (updated to the new evidence's own 40/40, also fixed from a lesser-verified 37/37). One
smaller finding from the same review, also fixed: fee-leak README copy said a review "found"
₹2,634.50 in overcharges -- accurate for the detection mechanism, but the specific injected examples
are checked against the same `FEE_PCT` table they were generated from (correct and necessary for
labeled synthetic test data, but "found" overstates it) -- reworded to lead with the real result
(zero false positives against 260 ordinary transactions) instead.

Not fixed, flagged instead: the review's point that the README has grown to ~7,300 words (up
further with this fix) and buries its best material past the fold. That's a real, correct
observation, but restructuring or cutting a document this many people have iterated on this
session is an editorial call for the project owner, not something to unilaterally execute mid-fix.

140/140 backend tests passing, frontend build clean.

---
