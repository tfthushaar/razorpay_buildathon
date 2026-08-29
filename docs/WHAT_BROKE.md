# What broke

Ten incidents that changed how the system works, newest first. Longer journal:
[`BUILD_LOG.md`](../BUILD_LOG.md).

---

## Published evidence described three algorithms while running one

Symptom. The timing table for the multi-way netting solver reported `optimal_algorithm: "2-sum-hash"`
on every row, at every size from 100 to 5,000.

Cause. `build_scale_case(group_size=N)` counts the target transaction itself. My `group_size=3` sweep
meant only two others had to cancel, which is a 2-sum every time. I had written and tested three
k-sum paths and published a table that exercised one.

Fix. Sweep 3/4/5, and count separately when a coincidental smaller group cancels first. The corrected
frontier is much worse: at a genuine four-member group the solver is unreliable by n=200, not n=1,500.
The same re-run corrected an n=500 cell of 29/30 = 96.7%, which I had published inside a blanket "100%
across 30 seeds up to n=1000". The sweep now records `algorithm_used` per row.

Found by: reading the committed evidence file after an external prompt to check it.

---

## A Groq column that measured nothing

Symptom. `gpt-oss-20b` scored byte-identical to the no-parsing baseline in both conditions: 91.3% and
88.0%, with zero discordant cases against the regex.

Cause. `cycle_reader.py` never called `load_dotenv`, so every Groq call raised `GroqError` on a
missing key. The retry wrapper caught it, saw no rate-limit string, and returned "no reading
available". A column of 300 silent failures looks exactly like a model that reads nothing.

Fix. Load the key and raise if it is absent, rather than degrade to None. Any non-rate-limit error now
propagates.

Found by: the column matching the baseline to three decimal places in both conditions. I was one edit
away from publishing "gpt-oss-20b buys nothing" as a finding about the model.

---

## A cascade built on an escalation signal that cannot fire

Symptom. Cascade routing scored 20.0%, worse than free parsimony, equal to running 7b on everything.
The 14b tier absorbed zero cases. Tier 0 absorbed six and got none right.

Cause. Two dead gates. The model tiers escalate on verification failure, but in choice mode the model
picks from options Layer 0 already validated, so `verified` is always true. Tier 0's gate asked
whether the advice picked a unique winner, which measures whether the text discriminated, not whether
the reading was correct.

Fix. None. No signal I tried correlates with correctness. Self-reported confidence is uninformative,
verification is trivially satisfied, tie count measures the wrong quantity.

Found by: running it.

---

## A 10/10 result that moved 16 points when n rose

Symptom. `narration_explained` published at Ollama 10/10, across 5 seeds.

Cause. Five seeds. 10/10 has a 95% Wilson lower bound of 72.2%.

Fix. Raised to 30 seeds: 175/209 = 83.7%, interval [78.1, 88.1]. Raising n moved the estimate down
16.3 points. The original figure was not imprecise, it was unrepresentative. Every accuracy figure in
[RESULTS.md](RESULTS.md) now carries an interval.

Found by: an external reviewer pointing at the lower bound.

---

## A negative result overstated against my own architecture

Symptom. I published "parsimony beats every reader including 14b" off 19/60 against 16/60.

Cause. A three-case difference read as a finding. These columns run over identical cases, so the
correct test is paired, not two independent intervals compared by eye.

Fix. Exact McNemar on the same cases: 7 discordant one way, 4 the other, p = 0.55. Parsimony is at
least as good; the difference is not distinguishable at n=60.
`app/calibration/significance.py` now tests every paired claim.

Found by: an external reviewer computing the bounds I had not published.

---

## A candidate pool that never contained the true answers

Symptom. The decomposition resolver produced large, plausible candidate pools. Reading them, nothing
looked wrong.

Cause. Every percentage candidate (fee, TDS, reserve) was computed off `chain.hops[1].actual`, the
post-fee amount, not the captured amount. Each candidate was individually plausible. The set never
contained the truth.

Fix. Use `hops[0].actual`. Recovery went from 11/60 to 60/60, and is now a standing assertion.
Otherwise "the model chose wrong" and "the right answer was never on the table" are indistinguishable.

Found by: asking whether the resolver recovers a decomposition already known to be true. Inspection
would never have caught it.

---

## A prompt that did not implement its own architecture

Symptom. A live 7b run scored 0 of 6. I switched to selection by index; still 0 of 6. Then it began
picking one candidate and stopping.

Cause. I handed the model the raw candidate pool, which asks it to solve subset-sum in its head. Layer
0 had already solved that.

Fix. Present the enumerated valid decompositions and ask for a choice. Verification went to 59/59. A
second problem surfaced immediately: presenting them in parsimony order put the true answer at
position 1 in 5 of 10 cases, so a first-option bias would score well for reasons unrelated to reading.
Options are now shuffled deterministically, and parsimony became its own baseline column.

Found by: reading the verifier's complaints instead of the accuracy number.

---

## The same unguarded-boundary pattern, five times, two subsystems

Symptom. Four rounds each fixed a different unguarded model-supplied value in the narrator's tool
loop. A fifth shape appeared in code the fourth fix had just touched. The pattern then reappeared in
the API's run state.

Cause. Each fix closed one exception shape, not the pattern. The next malformed shape is unforeseen by
definition. On the API side, three related fields were committed as three unlocked writes.

Fix. A broad `except Exception` backstop around the whole narrator dispatch, and one frozen
`_RunSnapshot` committed by a single atomic reference swap. A test sampling state from an unlocked
thread found 8,598 violations against a lock-only version that had looked correct.

Found by: a harsher concurrency test than the one that passed.

---

## Mock decisions riding on trust a real provider earned

Symptom. A category that earned auto-resolve from real evidence would auto-resolve a mock-mode guess
in that category.

Cause. The gate checked the category's accumulated history, not whether this decision came from a real
provider. Six consecutive mock runs crossed the 90% threshold with no LLM ever called.

Fix. `narrator_provider != "mock"` on the auto-resolve path. Mock decisions are tracked as `mock_n`
and never counted toward any gate.

Found by: an external audit, reproduced live.

---

## A hang that was rate-limiting, and eleven guards that could never fire

Symptom. A batch appeared to hang: `CLOSE_WAIT` connections, near-zero CPU. An isolated call succeeded
in 0.5s.

Cause. Repeated 429s from Groq's free tier, visible in the provider dashboard and in no local signal.
Separately, eleven rounds of fail-safe fixes all assumed a failing call raises. `ollama.Client()`
overrides httpx's default timeout to `None`, so a hung call never returned and every guard was
bypassed, including a retry that already listed `TimeoutException`.

Fix. `timeout=60.0` explicitly. Evidence scripts skip Groq by default, so a quota failure cannot be
recorded as a capability finding.

Found by: the user's Groq dashboard, after I had misdiagnosed it from network state.
