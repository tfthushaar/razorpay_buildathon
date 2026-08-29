# What broke, and how it was fixed

Nineteen incidents, chosen from a much longer chronological journal ([`BUILD_LOG.md`](../BUILD_LOG.md))
as the ones that actually changed how the system works, or how it was built. Same fixed format
throughout — symptom, root cause, fix, and the test that would now catch it.

A previous reviewer pointed out that this file had grown past a reasonable scan budget, and it has
grown further since, because more things broke and I would rather have them written down than have a
shorter document. The index below is the fix I'd defend: thirty seconds gets you the whole list, and
you can drop into whichever ones are actually worth your time.

| # | What broke | Where it bit |
|---|---|---|
| 1 | Published evidence described three algorithms while only ever running one | evidence integrity |
| 2 | A cascade router built on an escalation signal that can never fire | routing design |
| 3 | A candidate pool full of plausible numbers that never contained the true ones | resolver |
| 4 | My own prompt didn't implement my own architecture, and I blamed the model first | prompt design |
| 5 | The same unguarded-boundary pattern recurred five times across two subsystems | narrator + API |
| 6 | The flagship experiment's own methodology was weak, caught by re-reading its evidence | experiment design |
| 7 | A brute-force timing experiment's construction defeated what it was measuring | benchmarking |
| 8 | A live model got the arithmetic right and the category wrong; one sentence fixed it | prompt design |
| 9 | Mock decisions could ride on trust a real provider had earned | calibration gate |
| 10 | A test suite was silently erasing the live demo's accumulated history | test isolation |
| 11 | Re-running the same batch could manufacture "trust" with zero new evidence, twice | calibration |
| 12 | An architecture change was measured before being kept, and it made things worse | performance |
| 13 | Eleven rounds of fail-safe fixes assumed a failing call would raise something | timeouts |
| 14 | A regulatory citation had gone stale three weeks before I checked it | domain accuracy |
| 15 | An integration spec named a Razorpay endpoint that doesn't exist | API research |
| 16 | The real Razorpay connector crashed on a real API response shape | connector |
| 17 | One specific batch size silently generated one transaction too many | generator |
| 18 | A fix that looked done still regressed the behaviour it was meant to improve | verification |
| 19 | A hang that wasn't a hang — real rate-limiting, diagnosed from network state | diagnosis |

### Published evidence described three algorithms while only ever running one

**Symptom:** The timing table for the optimal multi-way netting solver reported
`optimal_algorithm: "2-sum-hash"` on every single row, at every batch size from 100 to 5,000. I had
written and tested three k-sum paths and published a table that claimed to compare them.

**Root cause:** `build_scale_case(group_size=N)` counts the **target transaction itself**, so my
`group_size=3` sweep meant only *two* other transactions had to cancel — a 2-sum, every time. The
3-sum and 4-sum paths were correct, tested, and never once exercised by the evidence describing them.

**Fix:** Sweep `group_size` 3/4/5 so all three run, and count separately when a coincidental
*smaller* group cancels before the true one. The corrected frontier is far worse than what I had
published: at a genuine four-member group the strongest rule I could write is unreliable by
**n=200**, not n=1,500. The same re-run corrected an `n=500` cell of 29/30 = **96.7%** that I had
published inside a blanket "100% across 30 seeds up to n=1000".

**Prevented:** the sweep now records `algorithm_used` and `true_group_members` per row, so a table
that silently exercises one code path can't be read as comparing three.

---

### I built a cascade on an escalation signal that can never fire

**Symptom:** Cascade routing (free rule → 7b → 14b → human) scored 20.0% end to end on held-out
phrasing — worse than free parsimony at 31.7%, and exactly equal to just running 7b on everything. The
14b tier absorbed zero cases. Tier 0 absorbed six and got none of them right.

**Root cause:** Two, both in gates I designed before seeing any numbers. The model tiers escalate on
*verification failure* — but in choice mode the model selects from options Layer 0 has already
validated, so a chosen option is arithmetically valid **by construction** and `verified` is always
true. The gate could not fire. Separately, tier 0's gate asked "did the advice pick a unique winner",
which measures whether the text *discriminated*, not whether the reading was *correct*. On familiar
phrasing those coincide, which is why it looked sound; on unfamiliar phrasing the rule reads
confidently and wrongly, and a wrong unique reading sails straight through.

**Fix:** None that works, and that is the finding. A cascade needs a signal correlated with
correctness. Self-reported confidence is uninformative (measured earlier here, and the reason
`_confidence_from_verification` discards it), verification is trivially satisfied, and tie count
measures the wrong quantity. The module ships as measured with this result in
[RESULTS.md](RESULTS.md) rather than tuned until the table improved.

**Prevented:** `test_cascade_tier0_absorbs_only_when_the_advice_actually_discriminated` asserts the
tie gate does *something* rather than absorbing everything — it did not, and could not, catch that
the thing it does is not the thing that matters.

---

### The resolver's candidate pool looked full of plausible numbers and never contained the true ones

**Symptom:** The new decomposition resolver produced large, sensible-looking candidate pools and
plenty of arithmetically valid answers. Reading them, nothing was obviously wrong.

**Root cause:** Every percentage-derived candidate (fee, TDS, rolling reserve) was computed off
`chain.hops[1].actual`, which is the **post-fee** amount, not the captured amount. Each candidate was
individually plausible; the set simply never contained the truth. No amount of looking at the pool
would have shown this.

**Fix:** Use `hops[0].actual`. What actually caught it was not inspection but a scoring question:
*does the resolver recover a decomposition I know to be true?* It recovered 11 of 60. After the fix,
60 of 60.

**Prevented:** `test_layer0_recovers_the_true_decomposition` — a standing assertion, because
"the model chose wrong" and "the right answer was never on the table" are indistinguishable without
it, and every accuracy number on the residual depends on the difference.

---

### My prompt didn't implement my own architecture, and I blamed the model first

**Symptom:** A live 7b run scored 0 of 6 on the residual. The failure messages showed it picking the
right candidate and transcribing the amount with the sign flipped, so I changed the interface to
select candidates by number. Still 0 of 6. Then it started picking a single candidate and stopping,
never attempting to compose.

**Root cause:** Two, and the second was mine. The sign flips were real — a fee charged *below* the
contracted rate contributes *positively* to the delta, which is genuinely counterintuitive. But the
deeper problem was that I handed the model the raw candidate pool, which asks it to solve subset-sum
in its head. Layer 0 had **already solved that**. The whole architecture says the model's job is to
choose among the resolver's valid answers, and my prompt didn't do that.

**Fix:** Present the enumerated valid decompositions and ask for a choice. Verification went to
59/59, because a chosen option is arithmetically valid by construction. A related self-inflicted
problem surfaced immediately after: presenting them in parsimony order put the true answer at
position 1 in 5 of 10 cases, so anything with a first-option bias scored well for reasons unrelated
to reading. Options are now deterministically shuffled, and "pick the most parsimonious" became its
own baseline column instead of a hidden advantage inside everyone else's score.

**Prevented:** `test_present_options_removes_positional_advantage`,
`test_candidate_selection_uses_the_pool_amount_not_a_retyped_one`.

---

### The same unguarded-boundary pattern recurred five times across two subsystems, before either was closed structurally

**Symptom:** Four separate rounds each found and fixed a different unguarded model-supplied value in
the narrator's tool-calling loop (a missing key, a wrong container type, an out-of-schema category, a
tool argument that wasn't valid JSON). A fifth shape — `tc.function` being `None`, plus a tool
receiving a JSON array instead of an object — surfaced in code the fourth fix had just touched. The
pattern then reappeared in a different subsystem: an escalation-lock fix protected one race in
`/api/escalations/resolve`, and a concurrent `/api/run` desynced a *different* field right next to it.

**Root cause:** Each narrator fix closed one specific exception shape, not the underlying pattern —
any model-supplied value can arrive malformed, and the next malformed shape is unforeseen by
definition. On the API side, `/api/run` committed three related fields as three separate, unlocked
writes; a lock around each site individually still leaves a gap for a future call site that doesn't
know to take it. Reproduced directly: `sys.setswitchinterval()`-amplified concurrency desynced state
on the first trial at 32 concurrent `/api/run` calls (8 concurrent — realistic load — never
reproduced it in 20 trials). A harsher test sampling `state` from an unlocked thread during 16
concurrent runs found **8,598 violations** against a lock-only version of the fix that looked correct
until measured this way.

**Fix:** Added `AttributeError` to both providers' tool-call exception handling, and wrapped
`narrate()`'s entire dispatch in one broad `except Exception` backstop. On the API side, replaced the
three separate fields with one frozen `_RunSnapshot` dataclass, committed via a single atomic
reference swap — structurally safe for any reader, not a fourth "add another lock" patch.

**Prevented:** `test_narrate_groq_fails_safe_on_tool_call_shapes_that_raise_attributeerror`,
`test_concurrent_runs_never_desync_the_three_state_fields`.

---

### The flagship experiment's own methodology was weak, and a self-directed re-read of its own evidence file caught it

**Symptom:** The multi-way netting experiment (the one case this project's rule-based matcher
provably can't solve) shipped a first result — Groq 8/8, Ollama 1/8 — that looked clean. A day later,
re-reading that same evidence file line by line turned up five real design problems, not framing
disagreements: the "8 seeds" were one hardcoded arithmetic puzzle relabeled (only ids varied); the
batch held exactly 3 transactions, so "the other 2" was the only candidate group, not a search; the
system prompt named the solving strategy outright; the grader passed on citing every id in sight; and
BUILD_LOG's own narrative for Ollama's failures ("giving up despite the tool result containing the
answer") didn't match a single one of the 8 raw responses it cited.

**Root cause:** Each flaw independently made the result easier to get than it should have been —
correlated samples, no distractors, a leaked strategy, and a trivially-satisfiable grader all bias in
the same direction, toward a better-looking number than the model's real capability supports.

**Fix:** Rebuilt the case generator with independently-random arithmetic per seed, 8 real distractor
transactions (verified by brute force that no other subset accidentally cancels), a prompt that
states only what the tool returns, and exact-match grading. Re-measured on the harder, honest version:
Groq dropped to 4/8, Ollama to 0/8 — a less impressive number, kept anyway, because the first
attempt's numbers were exactly as easy to get as the flaws made them. Groq's number later moved again,
legitimately: adding a `verify_group_sum` tool (a separate change, not a methodology rollback) took it
to 8/8, matching the original pre-fix fraction on paper but for the opposite reason — earned by
letting the model check its own hypothesis against real distractors, not by a leaked strategy and a
trivial grader. Current numbers, both conditions: [RESULTS.md](RESULTS.md).

**Prevented:** `test_different_seeds_produce_genuinely_different_arithmetic`,
`test_construction_raises_if_ever_ambiguous_rather_than_silently_shipping_a_bad_case`.

---

### A brute-force timing experiment's own construction defeated the thing it was measuring

**Symptom:** The real-settlement-batch-scale experiment's exhaustive solver was supposed to show
real wall-clock cost growing with transaction count. Instead, every measurement came back near-
instant regardless of `n_total` — a handful of milliseconds even at 800 transactions, which
contradicted the combinatorial growth the whole experiment existed to demonstrate.

**Root cause:** Construction always inserted the real cancelling group's members immediately after
the target, before any distractors. In the flat list the solver iterates with
`itertools.combinations`, the true answer sat at the very front of iteration order every time, found
on the first or second combination checked no matter how large the batch was. The timing measurement
was accidentally clocking "how fast is the first combination checked," not "how fast is the real
search" — caught by a dry run at `n_total=200` returning a combinations-checked count in the single
digits, which is what prompted reading the construction code directly rather than trusting the number.

**Fix:** Shuffle the batch's own transaction order (with the case's own seeded RNG, so results stay
reproducible) before it's ever exposed to a solver. Confirmed: real per-size timing now visibly
scales with `n_total` (733 combinations checked at n=50, 25,929 at n=300 — real combinatorial growth,
not a flat near-zero).

**Prevented:** No dedicated regression test for the ordering itself — the fix is structural, in
construction, not a separately-testable invariant. The guard that actually caught this was reading a
suspiciously-too-good number instead of trusting it.

---

### A live model got the arithmetic right and the category wrong — one sentence fixed it

**Symptom:** Wiring the multi-way netting capability into the real production narrator, a live Groq
run found the correct explaining pair and said so in its own reasoning text ("two other batch
transactions... which together cancel the delta") — then output the category `netting_trap`, not
`multiway_netting_trap`. The same mistake recurred on a second case in the same run: 3 of 7 correct,
worse than the underlying reasoning capability actually was.

**Root cause:** The system prompt described the new category's existence but never said explicitly
which output string a genuinely multi-transaction match should produce. The model, correctly
recognizing "a netting pattern" in the general sense, defaulted to the more familiar existing label
instead of the specific new one — a labeling gap, not a reasoning failure.

**Fix:** Added one explicit sentence: "If `verify_group_sum` confirms cancellation against a candidate
group of TWO OR MORE other transactions, the category is `multiway_netting_trap`, never
`netting_trap`." Re-ran the identical seeds live: 3/7 became 6/7 correct — the fix, not a re-roll.

**Prevented:** No dedicated unit test — a system-prompt wording change, verified live against a real
model the same way every other prompt-tuning finding in this project has been, re-confirmed via the
committed production evidence file.

---

### Mock decisions could ride on trust a real provider earned

**Symptom:** Calibrated auto-resolve tracks accuracy per category, gated on real-provider evidence
only. Once a category cleared the trust threshold, the gate checked only the category's name — not
whether *this specific decision* actually came from a real provider.

**Root cause:** `_final_decision()` checked `category in auto_resolve_categories` and stopped there —
a mock-mode guess (the UI's own default) in an already-trusted category would silently auto-resolve.

**Fix:** Added `narrator_provider != "mock"` to the gate itself, in `_final_decision()`
(`app/pipeline.py`). A category's trust no longer transfers to a decision that didn't earn it.

**Prevented:** `test_provider_gate_applies_per_decision_not_just_per_category`

---

### A test suite was silently erasing the live demo's own accumulated history

**Symptom:** `test_api.py` imported `app.main`'s live `calibration_history`/`audit_logger` singletons
directly and called `.clear()` on them in 4 of 5 stateful tests — the exact SQLite files a real
dashboard session persists to. Running `pytest` destroyed whatever accumulated history existed from
actual demo usage.

**Root cause:** Tests and the live app shared the same singleton objects and the same on-disk files
by construction. Found by inspecting the real databases directly: 39 accumulated `run_id`s, all
shaped like repeated test-batch sizes, with the actual real-provider evidence run's own `run_id`
entirely absent — the tests had already erased it once.

**Fix:** Added `tests/conftest.py`'s `isolated_app_state` fixture, monkeypatching
`app.main.audit_logger`/`calibration_history`/`state` to temp-file-backed instances per test. Every
stateful test in `test_api.py` uses it instead of touching the live singletons.

**Prevented:** The `isolated_app_state` fixture itself — verified by checking `backend/data/*.db`
file size and mtime before/after a full test run, unchanged.

---

### Re-running the same batch could manufacture "trust" with zero new evidence, twice

**Symptom:** Calibration requires enough *distinct* real-provider decisions before a category can
auto-resolve. Repeatedly running the same default seed re-observed the same small case set, inflating
the sample count with no new information — and the same gap surfaced again, one level up, in a
headline rupee figure quoted as "real money reconciled."

**Root cause:** `CalibrationHistory` never deduplicated by `transaction_id`. A Wilson interval alone
can't catch this: enough correlated trials eventually clear any fixed threshold with zero real
variety behind them — verified live, `duplicate_refund` reported `n=15` while seed 42 alone only ever
produces 4 distinct cases. The same gap let `amount_total` count the same transactions' amounts once
per re-scoring, not once per distinct transaction.

**Fix:** Added `MIN_DISTINCT_TRANSACTIONS_FOR_AUTO_RESOLVE`, gating on `distinct_transaction_count` in
addition to the Wilson-CI requirement. Added `distinct_amount_total` — first-occurrence-per-id,
computed in one pass — and rewrote every headline claim to cite it instead.

**Prevented:** `test_repeated_scoring_of_the_same_small_case_set_cannot_auto_resolve`,
`test_amount_total_and_amount_at_risk_are_not_inflated_by_repeated_rescoring`

---

### An architecture change was measured before being kept, and it made things worse

**Symptom:** Wanting to make narration faster under load, concurrent narrator dispatch was built — a
`ThreadPoolExecutor` running up to 4 narration calls in parallel, on the reasoning that each call is
I/O-bound, not CPU-bound.

**Root cause:** A single GPU-resident local model processes one request at a time regardless of
client-side dispatch — `ollama ps` showed no change in GPU-serialization behavior, so parallel
dispatch just meant idle threads waiting on the same serial queue. It also introduced a real
correctness cost: `recall_similar_resolutions` reads `context.audit_log`, appended to as each
narration completes — under concurrent dispatch, "prior resolutions so far" became order-dependent.

**Fix:** Measured on the identical seed and batch size: wall clock 156.1s, not faster than the
148.9–150.4s sequential baseline, and `genuine_error` accuracy dropped to 66.7% (6/9) from 85.7%
(6/7). No speed benefit and a real accuracy cost — reverted, not kept "just in case."

**Prevented:** Nothing to regression-test — reverted before shipping, not a bug caught after. The
discipline that caught it (measure an idea before believing it improved anything) is the actual guard
against re-attempting this blind.

---

### Eleven rounds of fail-safe fixes all assumed a failing call would raise something — none protected against a call that never returns

**Symptom:** Every prior fail-safe fix in the narrator loop protects against a call that raises — a
bad category, a malformed response, a `KeyError`. None protect against a call that simply hangs.

**Root cause:** `ollama.Client()`, constructed with no keyword arguments, resolves to `timeout=None`
— `httpx.Client()`'s own default is `Timeout(5.0)`, but the `ollama` package's constructor overrides
that to unbounded. A genuinely hung local model call would block `client.chat(...)` forever, and
every fail-safe already wired to catch this had nothing to ever catch, since no timeout could fire.
Enough hung requests would eventually exhaust FastAPI's whole threadpool, since every endpoint shares
it.

**Fix:** `Client(timeout=60.0)` in `narrate_ollama` — generous relative to the measured ~3s/txn
average, but finite. Made `narrate_groq`'s timeout explicit too, for the same reason.

**Prevented:** `test_narrate_ollama_constructs_its_client_with_a_finite_timeout`

---

### A regulatory citation behind the flagship fee-leak pattern had gone stale three weeks before it was checked

**Symptom:** A strategy document proposing the fee-leak detection feature asserted that any MDR
charged on UPI/RuPay debit is unconditionally illegal, citing Section 10A of the Payment and
Settlement Systems Act's zero-MDR mandate, in force since January 2020.

**Root cause:** Parliament amended that Act on 4 August 2026 — three weeks before this was checked —
replacing the blanket prohibition with a government-notification framework. Caught by checking the
document's own load-bearing claim independently before writing any code.

**Fix:** Instead of checking a fee against "what the law currently allows," the detector checks it
against **this merchant's own contracted rate** (`fee_schedule.py`'s `FEE_PCT`) — correct regardless
of how the regulatory framework evolves, a more robust design, not just a workaround.

**Prevented:** `test_zero_false_positives_against_every_existing_category` verifies the contract-based
detector; the regulatory framing itself has no test by nature — a documentation correction, verified
against the real Act text before any code shipped.

---

### An integration spec named a Razorpay endpoint that doesn't exist

**Symptom:** A document specifying a sandbox connector gave an exact endpoint —
`POST /v1/payments/test_payment` — to "simulate payment capture," alongside a per-settlement-ID recon
path, `/v1/settlements/{id}/recon/combined`.

**Root cause:** Neither endpoint exists in Razorpay's actual API documentation. Real test payments go
through the Checkout.js browser flow, not a server-to-server call; the real recon endpoint is a
date-scoped query, not a per-settlement-ID path. Writing a connector against the spec as given would
have been broken code calling a URL that 404s.

**Fix:** Checked the claim against Razorpay's real API docs before writing any connector code,
flagged it, and waited for real test credentials rather than shipping unverified integration code.
Independently re-derived in the next audit round, from scratch: same verdict, same two broken
endpoints.

**Prevented:** No test — caught before any code existed to test.
`app/connectors/razorpay_sandbox.py` was only written once real credentials existed.

---

### The real Razorpay connector crashed on a real API response shape

**Symptom:** `fetch_payments()` (the live Razorpay Test Mode connector) crashed against a genuine API
response.

**Root cause:** The code assumed `notes` was always an object (`{}`) when empty. The real API returns
`notes: []` (an empty list) when none are set — undocumented, only visible against a real account.

**Fix:** Added an `isinstance` guard before treating `notes` as a dict.

**Prevented:** `test_create_test_order_handles_notes_as_an_empty_list`

---

### One specific batch size silently generated one transaction too many

**Symptom:** A brute-force sweep of every accepted `main_n` (0–2000) found exactly one value,
`main_n=6`, where the generator silently produced 7 transactions.

**Root cause:** Three independently-rounded category shares could overshoot the requested total by
one, and the remaining category's count going negative was never clamped — `range(-1)` silently
yields zero iterations rather than raising, so the overshoot was never caught.

**Fix:** Any overflow is now absorbed into the clean-match share, so the batch total always equals
the requested `n` exactly, by construction.

**Prevented:** `test_main_batch_always_totals_exactly_the_requested_n_at_non_default_clean_ratios`

---

### A fix that looked done still regressed the exact behavior it was meant to improve, and only a live model call caught it

**Symptom:** Category discovery's proposals didn't cluster — 8 real proposals came back as 6 distinct
names, 5 singletons. The fix (threading `existing_proposals` into the prompt) passed every unit test
and clustered correctly under the deterministic mock provider. A live re-verification told a
different story: 0 named proposals out of 35 real `genuine_error` cases across 5 seeds — a silent,
complete collapse, not the improvement the fix was supposed to be.

**Root cause:** Bisected against the real model: the regression wasn't the instruction to reuse a
prior name, it was the mere *presence* of a "proposals already made" section in the prompt at all —
even an empty `"(none yet)"` placeholder was enough. `qwen2.5:7b-instruct` read the mention of a
reuse mechanism as a cue toward general caution, collapsing to `proposed_name: null` even on the
first case in a run, with nothing yet to reuse.

**Fix:** `_describe_prior_proposals` now returns `None`, not a placeholder string, when nothing named
exists yet, and `_describe_evidence` omits the section entirely in that case. Re-verified live:
naming rate restored, and clustering is grounded — cases sharing a real underlying hop converge on
one name, genuinely different cases still return `null`.

**Prevented:** `test_describe_evidence_omits_the_prior_proposals_block_entirely_when_nothing_named_exists`,
`test_describe_prior_proposals_is_none_with_no_prior_proposals`

---

### A hang that wasn't a hang — real rate-limiting, diagnosed by network state, not assumption

**Symptom:** An evidence-generation script that should have taken a few minutes ran for over 20
minutes with zero output. `tasklist`'s CPU-time column showed the process barely using any CPU
(consistent with waiting on I/O, not spinning), but `netstat` showed its two open connections sitting
in `CLOSE_WAIT` — the remote side had already closed them, and the process hadn't noticed.

**Root cause, investigated in the wrong order at first:** `CLOSE_WAIT` looked exactly like a genuine
client-side hang, a dead connection the code failed to detect. A follow-up isolated test — one bare
Groq API call — succeeded in half a second, which seemed to rule Groq out entirely. The real cause
only became clear from the account's own Groq dashboard: real, repeated HTTP 429s recurring every
7-8 minutes under sustained sequential load, invisible from a single isolated call or from local
process state alone — a fact no amount of `netstat`/`tasklist` introspection was going to surface on
its own.

**Fix:** Not a code bug to patch — a real, already-partially-documented constraint (BUILD_LOG's own
earlier entries note real Groq batches taking 11-70 minutes on the free tier). Evidence scripts that
call Groq now skip it by default (`--with-groq` to opt in deliberately), so a routine run doesn't
stall on a known external rate limit; Ollama and mock, both fast and free, carry the default evidence
in every new evidence script this pass.

**Prevented:** Nothing to regression-test — an external rate limit, not a code defect. The actual
guard is procedural, recorded here rather than in a test: check the provider's own dashboard before
concluding a stall is a code hang, and don't chase a phantom connection-handling bug when the real
cause is outside the process entirely.
