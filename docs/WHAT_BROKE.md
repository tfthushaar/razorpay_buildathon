# What broke

Seventeen incidents that changed how the system works, newest first. Longer journal:
[`BUILD_LOG.md`](../BUILD_LOG.md).

---

## A 10,000x speedup on a function that runs on ten items

Symptom. None. That is the problem.

Cause. Reading the literature on reconciliation matching turned up Horowitz and Sahni's
meet-in-the-middle, and `subset_sum.py` was an O(n^4) loop over `itertools.combinations`. I measured
the brute force at 25.3s for n=200 and roughly 70 hours projected at n=2,000, wrote the O(n^2)
version, tested it for exact equivalence, and measured it at 0.012s and 23.7s. Then I profiled the
call sites, which is what I should have done first. There are two, and they pass pools of five and
ten items. The one place n is genuinely large, the scale experiment, does not call this function at
all; it has its own k-sum solver.

Fix. Kept, because it is equivalence-tested against the original and removes a cliff if a caller ever
passes a real batch, but the module docstring now says plainly that it earns no speed claim. The
speedup is real in a microbenchmark and worth nothing here.

Found by: profiling after the rewrite instead of before it.

---

## A benchmark that graded the agent against an answer key it cannot see

Symptom. The new Q&A benchmark put the model at 33% on seen phrasing and 23% on held-out, with three
of six questions scoring 0/5 for every provider in every condition.

Cause. Two faults, and the first was in my scoring. "How many reconciled cleanly" was graded against
the generator's `clean_match` label (72 on seed 1) when the observable answer is delta == 0 (95). "How
many could not be accounted for" was graded against `genuine_error` (6) when the observable answer is
what reaches human review (18). Those labels live in the answer key, so two of six questions were
unanswerable by construction and the agent was marked wrong for them.

The second fault was real: every tool was scoped to one transaction, one date or the anomaly check, so
"how many transactions are in this run" had no tool behind it.

Fix. Both ground truths now score against observables, `summarise_batch` covers the aggregate
questions, and the mock router gets matching keywords so the baseline stays the strongest rule I can
write. Re-measured, the rule scores 83.3% on my phrasing and 0.0% on held-out; the model 63.3% and
50.0%.

Found by: a per-question breakdown, after the aggregate number alone suggested a weak model.

---

## An absolute error that could be reduced by being more wrong

Symptom. Two implementations of the same measure reported different MAPE for the same batch.

Cause. `run_backtest` guarded on `actual != 0` and then divided by `actual`. Six settlements per
2,000-transaction batch are over-netted to a negative amount, so those terms came out negative and
pulled down the mean of a quantity defined as absolute. Worse, the surviving figure was not a central
tendency at all: 83% of it came from five rows out of 1,795, and the median was 0.000000.

Fix. Settlements at or below zero are counted, not divided by. Amount accuracy is published as five
numbers -- exact rate, median, mean, p95, worst -- through one shared `ape_panel` both callers use.

Found by: plotting the error distribution before trying to improve it.

---

## Published evidence described three algorithms while running one

Symptom. The timing table for the multi-way netting solver reported `optimal_algorithm: "2-sum-hash"`
on every row, at every size from 100 to 5,000.

Cause. `build_scale_case(group_size=N)` counts the target transaction itself. My `group_size=3` sweep
meant only two others had to cancel, which is a 2-sum every time. I had written and tested three
k-sum paths and published a table that exercised one.

Fix. Sweep 3/4/5, and count separately when a coincidental smaller group cancels first. The corrected
frontier is much worse: at a genuine four-member group the solver is unreliable by n=200 rather than
n=1,500.
The same re-run corrected an n=500 cell of 29/30 = 96.7%, which I had published inside a blanket "100%
across 30 seeds up to n=1000". The sweep now records `algorithm_used` per row.

Found by: reading the committed evidence file after an external prompt to check it.

---

## A throughput figure that improved by changing definition

Symptom. A published throughput number went from 5,508 tx/sec to 20,953 tx/sec between two passes,
with the old row deleted and no note, then promoted to the README's lead economic argument with no
reproduce command and no committed evidence.

Cause. The metric changed scope. The old figure timed `run_batch`'s instrumented region; the new one
timed chains and matching alone. Both were true measurements of different things. The new figures also
came from single unrepeated runs: medians over 3 repeats are 17,424 and 20,513, or 17% and 25% below
what I published. Underneath sat a third error. The BUILD_LOG entry for the original run described the
9.08s as covering "matching + fee-leak review + journal generation", and `run_batch` stops its timer
before both. That description had been wrong since the day it was written, which is what let the later
comparison look reasonable.

Fix. `scripts/benchmark_throughput.py` times every component separately so the scopes sum. It records
the hardware, because a reader measuring 1.8× lower needs to tell whether that is their CPU or my
arithmetic. Evidence committed, reproduce command added, scope stated per row.

Found by: an external reviewer getting 11,427 tx/sec against my 20,953, with nothing in the repo to
reconcile it against.

---

## A Groq column that measured nothing

Symptom. `gpt-oss-20b` scored byte-identical to the no-parsing baseline in both conditions: 91.3% and
88.0%, with zero discordant cases against the regex.

Cause. `cycle_reader.py` never called `load_dotenv`, so every Groq call raised `GroqError` on a
missing key. The retry wrapper caught it, saw no rate-limit string, and returned "no reading
available". A column of 300 silent failures looks exactly like a model that reads nothing.

Fix. Load the key and raise if it is absent, instead of degrading to None. Any non-rate-limit error now
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

Cause. Each fix closed one exception shape and left the pattern open. The next malformed shape is unforeseen by
definition. On the API side, three related fields were committed as three unlocked writes.

Fix. A broad `except Exception` backstop around the whole narrator dispatch, and one frozen
`_RunSnapshot` committed by a single atomic reference swap. A test sampling state from an unlocked
thread found 8,598 violations against a lock-only version that had looked correct.

Found by: a harsher concurrency test than the one that passed.

---

## Mock decisions riding on trust a real provider earned

Symptom. A category that earned auto-resolve from real evidence would auto-resolve a mock-mode guess
in that category.

Cause. The gate checked the category's accumulated history and never whether this decision came from a real
provider. Six consecutive mock runs crossed the 90% threshold with no LLM ever called.

Fix. `narrator_provider != "mock"` on the auto-resolve path. Mock decisions are tracked as `mock_n`
and never counted toward any gate.

Found by: an external review, then reproduced live.

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

---

## A controlled comparison that was two different random batches

Symptom. Adding Razorpay's documented narration format to the bank-description generator moved the
three-source baseline from 132/150 to 135/150. I reported that as the format making matching easier
and was one step from publishing it.

Cause. The new template carries a bank name, and I drew it with `self.rng.choice(_BANKS)` — the same
stream every other decision in the generator draws from. One extra draw per row shifted everything
downstream, so which settlements collided, which got twins, and which carried a cycle reference at
all were all re-rolled. Before and after were not the same batch under one change; they were two
different random batches.

Fix. Cosmetic draws take their own stream, seeded separately, so adding one cannot perturb the rest.
Re-run as a distributional A/B across ten seeds, the documented format moves the baseline the other
way: -1.4 of 150 on average, harder on 6 of 10 seeds. The number I had was not just imprecise, it
pointed the wrong way.

Found by: checking why a change I expected to be neutral had moved a number at all.

---

## Twins that nothing could tell apart, in a benchmark about telling twins apart

Symptom. Isolating the RNG stream broke a generator test that had passed for weeks: a group of four
identical twins held only three distinct cycle references.

Cause. The whole point of an identical twin is that merchant, amount and date stop discriminating and
only the free-text cycle separates the pair. The generator drew the twin's cycle slot and re-drew it
while it matched *the settlement it was cloned from* — and checked nothing else. Two twins of the same
group could take the same slot, producing settlements identical on every field including the one that
exists to separate them. No reader could be scored fairly on those, because nothing could read them.

Fix. A twin now takes a slot no same-key settlement already holds, and skips rather than inventing one
when the day's cycles are full. It was 30 rows across ten seeds and 2% of all errors — small enough to
have survived this long, and wrong regardless of size.

Found by: a test I had not changed, failing on a change I thought was cosmetic.

---

## A published p-value that was a lucky draw

Symptom. The three-source headline was exact McNemar **p = 0.049**: the model reader beating the best
regex I could write on 13 paired cases against 4. Significant, and one discordant case from not being.

Cause. One batch at one seed. A sweep of the deterministic columns across ten seeds showed the case
set moving between 128 and 138 correct out of 150 — a spread of ten, far wider than the margin the
p-value rested on. Nothing in the published evidence said whether that result was a property of the
readers or of the draw.

Fix. `scripts/generate_three_source_seed_stability_evidence.py` re-runs the comparison across five
independent draws and publishes all of them. The direction holds at every seed, 0 losses and 0 ties,
but **only 1 of 5 seeds is significant on its own**. Pooled over 750 settlements the result is 56 wins
to 23, p = 0.0003, and survives conceding two cases at p = 0.0015. The effect is real and the
single-seed evidence for it was not adequate: four of five draws would have read as no result.

Found by: distrusting a p-value that sat that close to its own threshold.
