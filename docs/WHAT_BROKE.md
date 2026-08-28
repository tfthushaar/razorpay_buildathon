# What broke, and how it was fixed

Eleven incidents, chosen from a much longer chronological journal ([`BUILD_LOG.md`](../BUILD_LOG.md))
as the ones that actually changed how the system works, or how it was built. Same fixed format
throughout, so this stays scannable instead of becoming a second wall of text.

### The same unguarded-boundary pattern recurred five times across two subsystems, before either was closed structurally

**Symptom:** Four separate rounds each found and fixed a different unguarded model-supplied value in
the narrator's tool-calling loop (a missing key, a wrong container type, an out-of-schema category, a
tool argument that wasn't valid JSON). A fifth shape — `tc.function` being `None`, and one tool
receiving a JSON array instead of an object — surfaced live, in code the fourth fix had just touched.
The same pattern then reappeared in a different subsystem entirely: an escalation-lock fix protected
one race in `/api/escalations/resolve`, and a concurrent `/api/run` desynced a *different* field right
next to it, in code that same prior fix had just touched.

**Root cause:** Each narrator fix closed one specific exception shape rather than the underlying
pattern — any model-supplied value can arrive malformed, and the next malformed shape is by
definition unforeseen. On the API side, `/api/run` committed three related fields
(`latest_result`, `latest_escalations_by_id`, `latest_ground_truth`) as three separate, unlocked
writes; a lock around each read/write site individually still leaves a gap for any future call site
that doesn't know to take it. Reproduced directly, not assumed: `sys.setswitchinterval()` amplified
thread scheduling until 32 concurrent `/api/run` calls desynced state on the first trial (8 concurrent
— realistic load — never reproduced it in 20 trials, calibrated honestly rather than overstated). A
harsher test sampling `state` from an unlocked background thread during 16 concurrent runs found
**8,598 violations** against a lock-only version of the fix that looked correct until measured this way.

**Fix:** Added `AttributeError` to both providers' tool-call exception handling, and wrapped
`narrate()`'s entire dispatch in one broad `except Exception` backstop — the last line of defense for
whatever a provider-specific handler doesn't yet anticipate. On the API side, replaced the three
separate fields with one frozen `_RunSnapshot` dataclass, committed via a single atomic reference
swap — structurally safe for any reader, including a future one that doesn't know to take a lock, not
a fourth "add another lock" patch.

**Prevented:** `test_narrate_groq_fails_safe_on_tool_call_shapes_that_raise_attributeerror`,
`test_concurrent_runs_never_desync_the_three_state_fields`.

---

### Mock decisions could ride on trust a real provider earned

**Symptom:** Calibrated auto-resolve tracks accuracy per category, gated on real-provider evidence
only. But once a category legitimately cleared the trust threshold, the gate checked only the
category's name — not whether *this specific decision* actually came from a real provider.

**Root cause:** `_final_decision()` checked `category in auto_resolve_categories` and stopped there.
A mock-mode guess (the UI's own default) in an already-trusted category would silently auto-resolve,
falsifying "only auto-resolves what it's proven itself accurate on" at the per-decision level, through
entirely ordinary use — not an edge case.

**Fix:** Added `narrator_provider != "mock"` to the gate itself, in `_final_decision()`
(`app/pipeline.py`). A category's trust no longer transfers to a decision that didn't earn it.

**Prevented:** `test_provider_gate_applies_per_decision_not_just_per_category`

---

### A test suite was silently erasing the live demo's own accumulated history

**Symptom:** `test_api.py` imported `app.main`'s live `calibration_history`/`audit_logger` singletons
directly and called `.clear()` on them in 4 of 5 stateful tests — the exact SQLite files a real
dashboard session persists to. Running `pytest` (the README's own documented verification step)
destroyed whatever accumulated trust or audit history existed from actual demo usage.

**Root cause:** Nothing isolated test state from production state — tests and the live app shared the
same singleton objects and the same on-disk files by construction, not by mistake in any one test.
Found by inspecting the real databases directly: 39 accumulated `run_id`s, all shaped like repeated
test-batch sizes, with the actual real-provider evidence run's own `run_id` entirely absent — the
tests had already erased it once, silently, before anyone noticed.

**Fix:** Added `tests/conftest.py`'s `isolated_app_state` fixture, which monkeypatches
`app.main.audit_logger`/`calibration_history`/`state` to temp-file-backed instances for the duration
of each test, torn down after. Every stateful test in `test_api.py` uses it instead of touching the
live singletons.

**Prevented:** The `isolated_app_state` fixture itself (`tests/conftest.py`) — every stateful test in
`test_api.py` now depends on it; verified by checking `backend/data/*.db` file size and mtime
before/after a full test run, unchanged.

---

### Re-running the same batch could manufacture "trust" with zero new evidence, twice

**Symptom:** Calibration requires enough *distinct* real-provider decisions before a category can
auto-resolve. Repeatedly clicking "Run batch" on the same default seed re-observed the same small
case set, inflating the sample count without adding any new information — and the same missing
deduplication then surfaced a second time, one level up, in a headline rupee figure quoted as "real
money reconciled."

**Root cause:** `CalibrationHistory` never deduplicated by `transaction_id`. A Wilson confidence
interval alone can't catch this: enough repeated (correlated) trials at a genuinely high per-case
accuracy eventually clears any fixed threshold, with zero real-world variety behind it. Verified live
in this project's own committed evidence: `duplicate_refund` reported `n=15`, but seed 42 alone only
ever produces 4 distinct cases. The same gap then let `amount_total` count the same handful of
transactions' amounts once per re-scoring across many runs, not once per distinct transaction.

**Fix:** Added `MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE`, gating on `distinct_transaction_count` in
addition to (not instead of) the existing Wilson-CI requirement. Added `distinct_amount_total` —
first-occurrence-per-transaction-id, computed in one pass — and rewrote every headline claim to cite
it instead of the inflatable `amount_total`.

**Prevented:** `test_repeated_scoring_of_the_same_small_case_set_cannot_auto_resolve`,
`test_amount_total_and_amount_at_risk_are_not_inflated_by_repeated_rescoring`

---

### An architecture change was measured before being kept, and it made things worse

**Symptom:** Wanting to make narration faster under load, concurrent narrator dispatch was built — a
`ThreadPoolExecutor` running up to 4 narration calls in parallel, on the reasoning that each call is
I/O-bound, not CPU-bound.

**Root cause:** A single GPU-resident local model instance processes one request at a time regardless
of how many are dispatched client-side — `ollama ps` during the run showed no change in
GPU-serialization behavior, so parallel dispatch just meant idle Python threads waiting on the same
serial queue. Worse, it introduced a real correctness cost: `recall_similar_resolutions` reads
`context.audit_log`, appended to as each narration completes — under concurrent dispatch, "prior
resolutions so far in this run" became genuinely order-dependent, changing what evidence the model
saw for borderline classifications between runs of the identical input.

**Fix:** Measured before keeping it, on the identical seed and batch size: wall clock **156.1s, not
faster** than the 148.9–150.4s sequential baseline, and `genuine_error` accuracy dropped to 66.7%
(6/9) from 85.7% (6/7). No speed benefit and a real accuracy cost is not a trade worth making —
reverted, not kept "just in case."

**Prevented:** Nothing to regression-test — this was reverted before shipping, not a bug caught after.
The discipline that caught it (measure an idea before believing it improved anything, applied to a
change made by the same person who proposed it) is the actual guard against re-attempting this blind;
recorded here so a future session doesn't rediscover the same dead end.

---

### Eleven rounds of fail-safe fixes all assumed a failing call would raise something — none protected against a call that never returns

**Symptom:** Every prior fail-safe fix in this project's narrator loop protects against a call that
raises — a bad category, a malformed response, an unknown provider, a `KeyError`. None of them protect
against a call that simply hangs.

**Root cause:** `ollama.Client()`, constructed with no keyword arguments, resolves to `timeout=None`.
Verified precisely, not inferred: `httpx.Client()`'s own bare default is a sane `Timeout(timeout=5.0)`,
but the `ollama` package's constructor explicitly overrides that to unbounded. A genuinely hung local
model call — a GPU driver stall, a generation loop that never terminates — would block
`client.chat(...)` forever, and every fail-safe already wired to catch exactly this kind of failure
had nothing to ever catch, because no timeout could fire to raise it. Enough hung requests would
eventually exhaust FastAPI's whole threadpool, since every endpoint shares it — a genuine
availability risk for the entire application on the recommended default provider.

**Fix:** `Client(timeout=60.0)` in `narrate_ollama` — generous relative to the measured ~3s/txn
average, but finite, so a real hang now fails safe within a bounded time instead of tying up a thread
indefinitely. Made `narrate_groq`'s timeout explicit too, for the same documented-not-implicit reason,
though its SDK default was already sane.

**Prevented:** `test_narrate_ollama_constructs_its_client_with_a_finite_timeout`

---

### A regulatory citation behind the flagship fee-leak pattern had gone stale three weeks before it was checked

**Symptom:** A strategy document proposing the fee-leak detection feature asserted that any MDR
charged on UPI/RuPay debit is unconditionally illegal, citing Section 10A of the Payment and
Settlement Systems Act's zero-MDR mandate, in force since January 2020.

**Root cause:** Parliament amended that Act on 4 August 2026 — three weeks before this was checked —
replacing the blanket prohibition with a government-notification framework. Shipping the blanket
claim would have gone stale the week the feature launched, in front of judges who would plausibly know
about a change to a six-year-old, high-profile payments law. Caught by checking the document's own
load-bearing claim independently before writing any code, not by trusting it.

**Fix:** Instead of checking a fee against "what the law currently allows," the detector checks it
against **this merchant's own contracted rate** (`fee_schedule.py`'s `FEE_PCT`) — correct regardless
of how the regulatory notification framework evolves, a structurally more robust design than the
original framing, not just a workaround for the stale citation.

**Prevented:** `test_zero_false_positives_against_every_existing_category` (260 ordinary transactions,
zero false positives) verifies the contract-based detector itself; the regulatory framing change has
no test by nature — it was a documentation/positioning correction, verified against the real Act text
directly before any code shipped.

---

### An integration spec named a Razorpay endpoint that doesn't exist

**Symptom:** A document specifying a sandbox connector gave an exact endpoint —
`POST /v1/payments/test_payment` — to "simulate payment capture," alongside a per-settlement-ID recon
path, `/v1/settlements/{id}/recon/combined`.

**Root cause:** Neither endpoint exists in Razorpay's actual API documentation. Real test payments in
Razorpay's sandbox go through the Checkout.js browser flow (a mock bank page with Success/Failure
buttons), not a single server-to-server call; the real recon endpoint is a date-scoped query,
`/v1/settlements/recon/combined?year=&month=&day=`, not a per-settlement-ID path. Writing a connector
against the spec as given would have been broken code calling a URL that 404s — in front of judges
who work at the company whose API it claims to call.

**Fix:** Checked the document's own load-bearing claim against Razorpay's real API docs before
writing any connector code, flagged it, and waited for real test credentials rather than shipping
unverified integration code with a straight face. Independently re-derived in the very next audit
round, from scratch, with no memory of the first check: same verdict, same two broken endpoints.

**Prevented:** No test — caught before any code existed to test. `app/connectors/razorpay_sandbox.py`
was only written once real credentials existed, against the real, verified endpoints.

---

### The real Razorpay connector crashed on a real API response shape

**Symptom:** `fetch_payments()` (the live Razorpay Test Mode connector) crashed against a genuine API
response.

**Root cause:** The code assumed `notes` was always an object (`{}`) when empty, calling
`.get("notes", {}).get(...)`. The real API returns `notes: []` (an empty list) when none are set —
undocumented in a way that only showed up against a real account, not a mocked one.

**Fix:** Added an `isinstance` guard before treating `notes` as a dict.

**Prevented:** `test_create_test_order_handles_notes_as_an_empty_list`

---

### One specific batch size silently generated one transaction too many

**Symptom:** A brute-force sweep of every accepted `main_n` (0–2000) found exactly one value,
`main_n=6`, where the generator silently produced 7 transactions.

**Root cause:** Three independently-rounded category shares (`round(6*0.60) + round(6*0.25) +
round(6*0.10) = 4+2+1 = 7`) could overshoot the requested total by one, and the remaining
"ambiguous" category's count going negative was never clamped — `range(-1)` in Python silently
yields zero iterations rather than raising, so the overshoot was never caught.

**Fix:** Any overflow is now absorbed into the clean-match share, so the batch total always equals
the requested `n` exactly, by construction — correct for every `n`, not patched for the one value
found.

**Prevented:** `test_main_batch_always_totals_exactly_the_requested_n_at_non_default_clean_ratios`

---

### A fix that looked done still regressed the exact behavior it was meant to improve, and only a live model call caught it

**Symptom:** Category discovery's proposals didn't cluster — 8 real proposals from a live run came
back as 6 distinct names, 5 of them singletons, no memory of a matching prior case in the same batch.
The fix (threading `existing_proposals` into the prompt) passed every unit test and clustered
correctly under the deterministic mock provider. A live re-verification against the real local model
told a different story: 0 named proposals out of 35 real `genuine_error` cases across 5 seeds — a
silent, complete collapse, not the improvement the fix was supposed to be.

**Root cause:** Bisected by hand against the real model, not guessed at: the regression wasn't the
instruction to reuse a prior name, it was the mere *presence* of a "proposals already made" section in
the prompt at all — even an empty `"(none yet)"` placeholder was enough. `qwen2.5:7b-instruct` read
the mention of a reuse mechanism as a cue toward general caution, not just about reuse specifically,
collapsing to `proposed_name: null` even on the very first case in a run, with nothing yet to reuse.

**Fix:** `_describe_prior_proposals` now returns `None`, not a placeholder string, when nothing named
exists yet, and `_describe_evidence` omits the entire section from the prompt in that case — the
block must be genuinely absent for a run's first proposal, not merely empty. Re-verified live after
the fix: naming rate restored, and clustering is grounded rather than indiscriminate — cases sharing a
real underlying hop converge on one name, genuinely different or uncertain cases still return `null`.

**Prevented:** `test_describe_evidence_omits_the_prior_proposals_block_entirely_when_nothing_named_exists`,
`test_describe_prior_proposals_is_none_with_no_prior_proposals`
