# What broke, and how it was fixed

Seven incidents, chosen from a much longer chronological journal ([`BUILD_LOG.md`](../BUILD_LOG.md))
as the ones that actually changed how the system works. Same fixed format throughout, so this stays
scannable instead of becoming a second wall of text.

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

### A narrator failure mode kept recurring in one new shape at a time

**Symptom:** Four separate rounds each found and fixed a different unguarded model-supplied value in
the narrator's tool-calling loop (a missing key, a wrong container type, an out-of-schema category, a
tool argument that wasn't valid JSON). A fifth shape — `tc.function` being `None`, and one tool
receiving a JSON array instead of an object — surfaced live, in code the fourth fix had just touched.

**Root cause:** Each fix closed one specific exception shape (`KeyError`, `TypeError`, a schema
mismatch) rather than the underlying pattern: any model-supplied value can arrive malformed, and the
next malformed shape is by definition unforeseen.

**Fix:** Added `AttributeError` to both providers' tool-call exception handling, and wrapped
`narrate()`'s entire dispatch in one broad `except Exception` backstop — the last line of defense for
whatever a provider-specific handler doesn't yet anticipate, so one transaction's crash can never take
an entire batch's results down with it.

**Prevented:** `test_narrate_groq_fails_safe_on_tool_call_shapes_that_raise_attributeerror`

---

### Re-running the same batch could manufacture "trust" with zero new evidence

**Symptom:** Calibration requires enough *distinct* real-provider decisions before a category can
auto-resolve. Repeatedly clicking "Run batch" on the same default seed re-observed the same small
case set, inflating the sample count without adding any new information.

**Root cause:** `CalibrationHistory` never deduplicated by `transaction_id`. A Wilson confidence
interval alone can't catch this: enough repeated (correlated) trials at a genuinely high per-case
accuracy eventually clears any fixed threshold, with zero real-world variety behind it. Verified live
in this project's own committed evidence: `duplicate_refund` reported `n=15`, but seed 42 alone only
ever produces 4 distinct cases.

**Fix:** Added `MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE`, gating on `distinct_transaction_count`
in addition to (not instead of) the existing Wilson-CI requirement.

**Prevented:** `test_repeated_scoring_of_the_same_small_case_set_cannot_auto_resolve`

---

### A headline rupee figure was inflated by the same re-scoring bug, one level up

**Symptom:** An external review of the README caught `amount_total` (a calibration figure quoted as
"real money reconciled") counting the same handful of transactions' amounts once per re-scoring
across many runs — not once per distinct transaction.

**Root cause:** The same missing deduplication as above, surfacing in a second, headline-facing
number after the first fix (the distinct-transaction floor) had already shipped.

**Fix:** Added `distinct_amount_total` — first-occurrence-per-transaction-id, computed in one pass,
not summed-then-deduplicated — and rewrote every headline claim to cite it instead of the inflatable
`amount_total`.

**Prevented:** `test_amount_total_and_amount_at_risk_are_not_inflated_by_repeated_rescoring`

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

### A concurrent run could pair one run's escalations with another run's ground truth

**Symptom:** Four prior rounds had each closed an "unguarded concurrent boundary" bug in a different
subsystem. A fifth instance sat in code the fourth fix had *just* touched: `/api/run` committed three
related fields (`latest_result`, `latest_escalations_by_id`, `latest_ground_truth`) as three separate,
unlocked writes.

**Root cause:** A concurrent `/api/run` call could overwrite the ground-truth field with a *different*
run's data in the gap between the three writes, silently stranding an entire run's escalation queue —
`/api/runs/latest` still showed them as live, but resolving one would 404 against ground truth that no
longer matched.

**Fix:** Replaced the three separate fields with one frozen `_RunSnapshot` dataclass, committed via a
single atomic reference swap — structurally safe for any reader, including a future one that doesn't
know to take a lock, not just the currently-known ones.

**Prevented:** `test_concurrent_runs_never_desync_the_three_state_fields`

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
