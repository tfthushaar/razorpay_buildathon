# Results

Every number this project cites, with the command that reproduces it. Nothing here is retyped from
a dashboard — each row is a real run against this repo's own committed evidence or a fresh batch.

## Core reconciliation

| Claim | Number | Reproduce |
|---|---|---|
| Batch size | 50,000 (mock) / 120 (real Ollama) | [50k run](evidence/50k-batch-run-2026-08-25.json) |
| Match rate, real provider | 99.3% of settlement value, 7 escalations of 120 | see "The calibrated-autonomy result" below |
| Match rate, mock provider | 86.0% reconciled, 18 escalations of 120 — mock deliberately escalates every LLM-routed category rather than auto-resolve on unearned trust | `run_batch(seed=42, main_n=120, stress_n=40, provider='mock')` |
| Throughput | 5,508 tx/sec (mock, 50k scale) — 2.58 tx/sec (real LLM, measured) | [SETUP.md](SETUP.md) |
| `netting_trap` | 59 distinct real cases, 98.3% accuracy, 91.0% Wilson lower bound, ₹4,86,473.13 auto-resolved | `cd backend && python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db` |
| `duplicate_refund` | 37 distinct real cases, 100% accuracy | same command |
| `genuine_error` | 80.3% accuracy, stays escalated regardless — never auto-resolves by design | same command |

## The calibrated-autonomy result

After 8 real, honestly-accumulated Ollama batches, `netting_trap` cleared the 90% trust threshold
(95% Wilson lower bound 91.0%) and `duplicate_refund` did the same separately. `genuine_error` sat
at 80.3% across the same evidence and stayed escalated anyway — it never auto-resolves regardless of
measured accuracy, by construction, since a misclassification there should cost a human a glance, not
become a wrong autonomous action.

## Failure Recovery

| Experiment | Result | Reproduce |
|---|---|---|
| Time-to-revocation drill | 1 wrong decision (₹500) revoked a category's auto-resolve status after 40 clean decisions, even with the all-time aggregate still at 97.6% | `curl -X POST localhost:8000/api/drift/drill -H 'Content-Type: application/json' -d '{}'` |
| Regret in rupees | ₹0 realized regret across 8 real auto-resolved transactions in the committed evidence db — the realized cost of autonomy, not `amount_at_risk`'s forward-looking estimate | `GET /api/regret` reads whatever history has actually accumulated locally — 0/0 on a fresh clone until real batches run; the 8 above is `verified_calibration_history.db`'s own accumulated total |

## Cross-run memory for `recall_similar_resolutions`

Live-verified against this project's own accumulated `backend/data/audit_log.db` (gitignored, local
only): a brand-new run's first call for each category, before that run had narrated anything of its
own —

| Category | Prior count seen | Avg confidence |
|---|---|---|
| `genuine_error` | 612 | 0.315 |
| `netting_trap` | 834 | 0.856 |
| `duplicate_refund` | 428 | 0.904 |

Reproduce: run a batch twice against the same `AuditLogger` db path (`POST /api/run` twice against a
running server uses this automatically), then `GET /api/audit?run_id=<second_run_id>` and inspect any
narrated entry's `tool_calls_json` for the `recall_similar_resolutions` call.

## Fee-leak detection / tax-line matcher

| Claim | Number | Reproduce |
|---|---|---|
| Fee-leak review batch (seed=42, n=20) | 7 blended-rate overcharges, 7 GST-wrong-base, 6 GST-wrong-rate — ₹1,497.40 recoverable fees, ₹15,181.65 miscalculated tax | `run_fee_leak_detection(...)`, see `test_fee_leak.py` |
| False positive rate | 0 false positives across 260 ordinary transactions from the main/stress batches | `test_zero_false_positives_against_every_existing_category` |
| GSTR-2B match (seed=42, main_n=150) | 120 matched (₹2,740.19), 30 exceptions (₹444.17) across 3 disjoint mismatch kinds | `GET /api/gstr2b` |

## Forward cash forecaster

| Batch size | MAPE | Interval coverage |
|---|---|---|
| n=30 (dashboard default) | 9.1% | 93.3% |
| n=120 (API default) | 8.6% | 90.8% |
| n=160 | 4.1% | 90.6% |

The forecaster reuses the merchant's own known fee/SLA schedule (real reference data, not learned),
so it's exact to the paise on ~73% of transactions by construction (verified: 88/120 exact matches at
n=120). The reported MAPE/coverage come entirely from the ~27% with a refund, dispute, or timing
anomaly it structurally can't see in advance. Reproduce: `GET /api/forecast/backtest`.

**A genuinely-blind backtest, separately.** The numbers above share one schedule between the
predictor and the batch that scores it — a real gap, since a merchant's actual contracted rate or
real settlement timing can drift from what a platform's own schedule still assumes.
`GET /api/forecast/blind-backtest` scores the same predictor against a self-contained batch whose
real settlements are computed with a hidden, per-rail fee-rate/SLA-day drift (up to 15% / 2 days,
`app/forecast/blind_backtest.py`) the predictor never sees. This batch has no refunds or timing
anomalies at all — schedule drift is the *only* source of error here, unlike the non-blind number
above, whose error comes almost entirely from the ~27% with a refund/dispute/anomaly — so the two
numbers answer different questions, not the same one twice. Measured over seeds 1–20 at n=120:

| Metric | Mean | Range across seeds |
|---|---|---|
| MAPE | 0.11% | 0.02%–0.17% |
| Interval coverage | 56.5% | 3%–100% |

The amount forecast barely moves (a fee is a small fraction of settled value even with real-rate
drift), but interval coverage is highly sensitive to SLA drift specifically — a few days of real
timing drift can push the actual settlement date entirely outside the predictor's own narrow
tolerance window. Reproduce a single seed: `GET /api/forecast/blind-backtest?seed=42&n=120` (45.8%
coverage — seed 1 is the top of the 20-seed range at 100%, seed 4 the bottom at 3.3%; seed 42 is
picked here for being unremarkable, not for being representative of any particular tail).

## Where the rule beats the LLM, measured directly

`narrate_mock` — a 20-line rule with zero LLM calls — scores **100.0% across 519 real
narration-queue cases**, on all three categories the real narrator is reserved for:

```
cd backend && python scripts/measure_mock_narrator_accuracy.py
TOTAL: 519/519 = 100.0%
  duplicate_refund: 96/96 = 100.0%
  genuine_error: 117/117 = 100.0%
  netting_trap: 306/306 = 100.0%
```

The real Ollama/Groq narrator does not match this (98.3%/80.3% above). Cause, confirmed by reading
both sides: the same author wrote the generator's injectors and `check_batch_anomalies`'s detector,
so both share exact-match logic — there is no genuine ambiguity left for a classifier to resolve on
this task. The real-provider call earns autonomy here for reliability under conditions the rule never
faces (API failures, malformed tool arguments, a hallucinated id), not for resolving a case the rule
genuinely couldn't.

This happened three times — `netting_trap` fell to a 20-line rule, `multiway_netting_trap` to a hash
table, `narration_explained` to a keyword scan. Three times is not bad luck, and the next two sections
are what I did about it.

## Why every category kept collapsing into a rule

Settlement records are produced by deterministic processes, so their ground truth is arithmetically
derivable. For any classification task posed over them, a rule exists that wins. A fourth category
would have reproduced the pattern; the fix was to stop putting the model where a rule can stand.

So the pipeline is inverted (`app/resolver/`). The deterministic resolver runs **first** and keeps
everything it can explain on its own. What is left has exactly two shapes: `UNDER_DETERMINED` (it
found ≥2 arithmetically valid explanations and has no basis to choose) and `UNMATCHED` (it found
none). The model only ever sees those. That is a structural guarantee rather than a claim — a case a
rule could solve was taken *by* the rule, so it cannot be inside a model's accuracy figure inflating
it.

`UNDER_DETERMINED` is the load-bearing half, because it is the one that cannot be answered with "your
resolver just isn't good enough yet". A *stronger* resolver finds more valid decompositions, not
fewer. And the baseline stops being rhetorical: with k valid answers and no basis to prefer any,
blind choice scores exactly **1/k**.

**The obvious objection is that I manufactured the ambiguity with a tolerance knob.** Measured
directly, with the setting that is worst for the architecture — zero rounding noise, zero tolerance,
exact integer arithmetic:

| Rounding noise | Tolerance | Resolved | Under-determined | Unmatched | Median k | True answer recovered |
|---|---|---|---|---|---|---|
| 0 | 0 | 9 | **51** | 0 | 4 | 60/60 |
| 0 | 10 | 0 | 60 | 0 | 28 | 60/60 |
| 3 | 0 | 5 | 48 | 7 | 3 | 10/60 |
| 3 | 10 | 1 | 59 | 0 | 22 | 60/60 |

At exact match with no tolerance whatsoever, 51 of 60 compound cases are still under-determined.
Compositionality is what makes this problem under-determined; the tolerance only amplifies it. This
is a standing test (`test_compositionality_alone_makes_it_under_determined`), not a one-off.

The `true answer recovered` column is the one that makes everything below meaningful. If the
resolver's candidate set did not contain the truth, "the model chose wrong" and "the right answer was
never on the table" would be indistinguishable. It is also how I found a real bug: percentage
candidates were being computed off the post-fee hop instead of the captured amount, so the pool was
full of plausible numbers that were never the true ones (11/60). It now recovers 60/60, and that is
an assertion in the test suite rather than a note.

Reproduce: `cd backend && python scripts/generate_residual_evidence.py`. Raw evidence:
[`residual-architecture-2026-08-29.json`](evidence/residual-architecture-2026-08-29.json).

## Does a model read settlement advice better than a regex? Measured on its own.

This is the sharpest question in the whole design, so it is measured in isolation rather than inferred
from end-to-end accuracy. The keyword baseline is two separable stages: read the remittance advice
into assertions about what applied, then score every valid decomposition against them. Stage two is
bookkeeping a rule does perfectly. Stage one is reading comprehension over messy, negated,
tense-shifted bank text. Only stage one is compared here, against ground truth the generator records
itself (`advice_mentions`).

The rule is written to win: fragment splitting, cause keywords, and a 29-entry negation-cue list I
assembled *with full sight of the generator's own phrasing*. That last part is the problem, and it is
the same shared-author trap this project was already caught by once. So both conditions are reported:

| Reader | Phrasing the rule's author saw | Held-out phrasing | Gap |
|---|---|---|---|
| best keyword rule I could write | **95.2%** | 61.7% | **−33.6 pts** |
| `qwen2.5:7b-instruct` | 79.8% | 72.6% | −7.1 pts |
| `qwen2.5:14b-instruct` | 86.9% | **81.7%** | −5.2 pts |

(60 cases × 7 charge types = 420 judgements per cell.) Held-out phrasing keeps the cause-identifying
vocabulary recognisable — TDS, RSV, GST, MDR still appear, so the rule cannot fail merely by not
knowing a synonym — and changes only how *applied* versus *not applied* is expressed: abeyance,
rescinded, held over, zero-rated, struck off, stood down, lapsed, contra. A test asserts the held-out
bank contains none of the rule's own cues, or the gap would measure nothing.

**On phrasing its author saw, the rule wins comfortably. On phrasing he didn't, the ordering inverts
and the rule collapses.** Most of its apparent advantage was authorship rather than reading.

And the accuracy gap understates it, because the two failure modes are not interchangeable:

| Reader | Condition | Reads a denial as a confirmation | Misses a mention entirely |
|---|---|---|---|
| keyword rule | seen | 6 (1.4%) | 0 |
| keyword rule | **held-out** | **161 (38.3%)** | 0 |
| `qwen2.5:7b` | held-out | 15 (3.6%) | 69 |
| `qwen2.5:14b` | held-out | 14 (3.3%) | 47 |

On unfamiliar phrasing the rule asserts a charge the text explicitly *denies* in 38.3% of all
judgements — roughly eleven times either model's rate (3.6% and 3.3%). In a system that files recovery claims
against an acquirer, that is a false claim about money. The models' dominant error runs the other
way: they miss the mention, which leaves the component unexplained and escalates the case. Wrong, but
safe.

This is also where a bigger model genuinely helps (14b > 7b in both conditions), which is the exact
opposite of the tool-budget-constrained result further down — worth holding both, since "bigger is
better" is true of one of these tasks and false of the other.

Reproduce: `cd backend && python scripts/generate_reading_evidence.py`. Raw evidence:
[`advice-reading-2026-08-29.json`](evidence/advice-reading-2026-08-29.json).

## End to end on the residual — including the column that beats my own architecture

The reading result above is about one isolated step. This is the whole task: pick the true
decomposition out of Layer 0's valid ones. Every column chooses from the identical shuffled option
list, so the comparison is symmetric.

| Strategy | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| chance (mean 1/k) — computed, not argued | 6.3% | 6.1% | — |
| best keyword rule I could write | **42.4%** | 8.3% | **−34.0 pts** |
| model, whole option list (`qwen2.5:7b`) | 5.1% | 5.0% | −0.1 pts |
| model reader (`qwen2.5:7b`) | 25.4% | 20.0% | −5.4 pts |
| model reader (`qwen2.5:14b`) | 35.6% | 26.7% | −8.9 pts |
| **parsimony — free, and ignores the advice entirely** | 25.4% | **31.7%** | **+6.2 pts** |

(59–60 under-determined cases per condition; the true answer was inside the 40-option window in every
single one, so nothing here is capped by presentation.)

Three things in that table, and the third is the one I like least:

**The keyword rule collapses to near-chance.** 42.4% → 8.3%, against a 6.1% floor. On phrasing its
author never anticipated, the strongest rule I could write is barely distinguishable from guessing.

**Handing the model the whole option list doesn't work, and that was my bug, not the model's.** 5%
either way — indistinguishable from chance. Layer 0 has already done the arithmetic; asking the model
to re-derive a subset-sum over ~30 candidates is asking it to do the one thing it is worst at and the
resolver is best at. Splitting the job so the model only *reads* and the deterministic scorer does the
matching takes 7b from 5.1% to 25.4% on identical data. The weaker column is kept in the table rather
than quietly dropped.

**And the honest one: on held-out phrasing, the best strategy is a free heuristic that ignores the
text entirely.** "Always take the fewest-component explanation" scores 31.7%, beating every reader
including 14b at 26.7%. Reading the advice at 73–82% accuracy is *better than a broken rule* but not
good enough to beat simply preferring the simplest explanation. Parsimony even improves on held-out
phrasing (+6.2 pts), because it never depended on the text in the first place.

That last row is a real limit on the argument this project makes, so it leads here rather than
sitting in a footnote. The defensible claim is narrow: **a model reads this text better than a rule
does, and fails far more safely — but on this end-to-end task, at these model sizes, reading does not
yet pay for itself against a trivial structural prior.** Anyone hoping to find "the LLM beat the
rules" in this repo will not find it.

Reproduce: `cd backend && python scripts/generate_residual_evidence.py` (add
`--providers ollama_reader --model qwen2.5:14b-instruct` for the 14b row). Raw evidence:
[`residual-architecture-2026-08-29.json`](evidence/residual-architecture-2026-08-29.json),
[`residual-architecture-14b-2026-08-29.json`](evidence/residual-architecture-14b-2026-08-29.json).

## Three sources that disagree — and the one place the model beats the best rule outright

Everything above reconciles gateway data against itself, so the join is trivial and all the difficulty
sits in the arithmetic. This is the other half of real reconciliation: a settlement report, a bank
statement and an ERP ledger that never agreed (`app/data_gen/three_source.py`), joined on nothing
reliable — banks truncate the UTR to its last 6–8 characters, prefix it with a scheme code, render the
merchant name in their own house style, and slip the value date across a weekend.

It exists as a **check on the residual argument itself**. If under-determination only ever showed up in
compound settlement arithmetic, it would be fair to suspect the arithmetic was built to produce it.
This is a different problem, on different data, with a different rule.

The case that makes it hard is the one that happens constantly in any subscription business: **two
payouts to the same merchant, for the same amount, on the same day**. Merchant, amount and date all
stop discriminating, the truncated UTRs share a tail, and the only thing left is the settlement cycle
reference — which the bank carries in free text, wherever it likes, and a third of the time not at all.

Everything in the matcher is held identical across these three columns — same filters, same scoring
weights, same tie-breaking. The *only* difference is what decides "does this description state this
settlement's cycle?":

| | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| UTR + amount + date + name, no cycle parsing | 91.3% | 88.0% | −3.3 pts |
| **+ the best regex cycle parser I could write** | **98.7%** | 88.0% | **−10.7 pts** |
| **+ a model reading the same text** | 98.0% | **94.7%** | −3.3 pts |

(150 settlements against 180 bank rows; the true row was reachable in 150/150 for every column, so
nothing here is capped by filtering.)

On phrasing the parser's author saw, the regex wins — 98.7% against the model's 98.0%, a difference of
one match. On phrasing he didn't, **cycle parsing buys exactly nothing**: 88.0%, identical to not
parsing the cycle at all, because the regexes match zero descriptions. The model recovers **10 of the
18 matches** the regex loses, and cuts under-determined cases from 10 down to 2.

That is the first place in this entire project where the model beats the best rule I could write on
the *end-to-end* task rather than on an isolated sub-step — and it is exactly where the theory said it
should be. The difference from the compound-delta result above is instructive rather than
contradictory: there, reading competed against a strong structural prior (parsimony) that did most of
the work on its own; here the reading **is** the discriminator, because every structured field has
already been exhausted by construction. When the text is the only evidence left, reading it well is
worth 6.7 points; when it is one signal among several, it is not.

Held-out phrasing keeps the domain vocabulary intact (`SETTLEMENT RUN D DTD 13.03.2026`, `window D on
2026-03-13`, `processed in slot d of 2026-03-13`) and changes only the house style, so the regex is not
failing on an unknown word. Two standing tests assert that the held-out bank defeats the regex
completely and that the seen bank is fully parseable by it, or the comparison would measure nothing.

Reproduce: `cd backend && python scripts/generate_three_source_evidence.py`. Raw evidence:
[`three-source-2026-08-29.json`](evidence/three-source-2026-08-29.json).

## Cascade routing: built, measured, and it doesn't work — here's exactly why

The obvious next move is a cascade: free rule → 7b → 14b → human, each tier handling only what the
one below couldn't, reporting cost per *resolved* transaction rather than per call. I built it
(`app/resolver/cascade.py`), designed the escalation gates before seeing any of the numbers above,
and ran it on held-out phrasing:

| Tier | Absorbed | Correct | Accuracy | Sec/resolved |
|---|---|---|---|---|
| keyword rule (free) | 6 | 0 | **0.0%** | ~0 |
| `qwen2.5:7b-instruct` | 54 | 12 | 22.2% | 2.64s |
| `qwen2.5:14b-instruct` | **0** | — | — | — |
| escalate to human | 0 | — | — | — |

**20.0% end to end**, at 2.38s per case. That is worse than free parsimony (31.7%) and exactly equal
to just running the 7b reader on everything. The cascade added latency and bought nothing. Two
specific mistakes, both mine, and both worth more than a tuned result would have been:

**Tier 0 absorbed 6 cases and got 0 of them right.** Its gate was "did the advice pick a *unique*
winner" — and on held-out phrasing the rule reads confidently and wrongly, so a wrong unique reading
sails through. The tie count measures whether the text *discriminated*, not whether the reading was
*correct*. On familiar phrasing those two coincide, which is why the gate looked sound when I wrote
it; on unfamiliar phrasing they come apart completely.

**Tier 2 never fired at all**, because tier 1 verified on every single case. In choice mode a chosen
option is arithmetically valid *by construction*, so `verified` is always true and an
escalate-on-verification-failure gate can never trigger. The verifier is the right safety mechanism
and the wrong routing signal.

Which leaves the real finding, stated plainly: **I could not construct a useful escalation signal for
this task.** Self-reported confidence is uninformative (measured earlier in this project, and the
reason `_confidence_from_verification` discards it). Verification is trivially satisfied in choice
mode. Tie count measures the wrong thing. A cascade needs a signal that correlates with *correctness*,
and none of the three cheap candidates does. The module ships as measured, with this result, rather
than being tuned until the table looked better or quietly dropped for not working.

Reproduce: `cd backend && python scripts/generate_cascade_evidence.py`. Raw evidence:
[`cascade-routing-2026-08-29.json`](evidence/cascade-routing-2026-08-29.json).

## A task the rule provably can't do, shipped as a real product category

`check_batch_anomalies` only checks pairs — a netting pattern spanning 3+ transactions, where no
single pair cancels, is invisible to it by construction. `multiway_netting_trap`
(`app/data_gen/generate.py`, opt-in via `enable_multiway_netting`) brings this into the real decision
loop: `list_batch_deltas`/`verify_group_sum` wired into the production narrator's own `TOOL_SCHEMAS`,
gated behind calibration like every other category. Measured on real generated batches (150
transactions each, small dedicated groups — not the hand-built stress test below):

| Provider | Accuracy |
|---|---|
| mock | 0/42 — structural, confirmed empirically (still 100% on every other category in the same batches) |
| Ollama (`qwen2.5:7b-instruct`) | 5/7 |
| Groq (`openai/gpt-oss-20b`) | 6/7 |

Reproduce: `python scripts/measure_mock_narrator_accuracy_multiway.py`,
`python scripts/generate_multiway_netting_trap_production_evidence.py`. Raw evidence:
[`multiway-netting-trap-production-2026-08-29.json`](evidence/multiway-netting-trap-production-2026-08-29.json).

## The same task, at real settlement-batch scale (500-800 transactions)

The product category above uses small, dedicated groups by design (calibration needs many
independent small cases, not one giant puzzle — see [ARCHITECTURE.md](ARCHITECTURE.md)). A separate
experiment (`app/narrator/multiway_netting_scale_experiment.py`) tests the same underlying capability
at a scale a real high-volume merchant's settlement batch could actually have. The original
hypothesis was that raw context size would be the wall; what was actually measured is two different
failure modes on two different providers:

- **Ollama fails at every scale tested (20 through 760 transactions), 0/36 across the whole sweep** —
  and not from context overflow. The raw tool-call traces show it accumulating an ever-growing
  candidate list across rounds instead of searching small subsets systematically, confirmed as a
  reasoning-strategy limit (not a token-budget one) by Groq solving the identical n=20 case correctly
  on the first attempt.
- **Groq hits a real, literal wall — later, and as a hard error, though not the exact one first
  assumed.** Solves n=20 correctly (2/2), gives a mix of correct answers and empty/unparseable
  responses by n=100, then at n≥200 every call in this sweep returned a real `429` — but the error
  message itself (`"Rate limit reached... on tokens per day (TPD): Limit 200000, Used 199594..."`)
  shows this is the account's free-tier **daily token quota**, exhausted by this session's own
  cumulative Groq usage across every earlier phase, not a per-request context-size limit specific to
  large batches. A genuinely isolated n=400 call (fresh quota) does return `413 Request too large`
  for `openai/gpt-oss-20b` — confirmed directly in an earlier manual check — so the context-size wall
  is real too, just confounded with quota exhaustion in this particular sweep's own committed run.
  Disclosed as the honest, if messier, finding rather than smoothed into a single clean threshold.
- **A magnitude pre-filter does not cleanly rescue either failure mode**, measured directly: loose
  enough to rarely discard the real answer (10x tolerance), it barely narrows the candidate set
  against this experiment's own uniformly-distributed distractor deltas (494 of 499 shown at n=500).
  Tight enough to actually shrink the request (1.5x) pushes the real-answer discard rate over 40%.

Reproduce: `python scripts/generate_multiway_netting_scale_evidence.py`. Raw evidence:
[`multiway-netting-scale-experiment-2026-08-29.json`](evidence/multiway-netting-scale-experiment-2026-08-29.json).

## The strongest deterministic rule actually built for this task

Not a claim that a rule "could theoretically be extended" — real k-sum algorithms
(`app/narrator/multiway_netting_optimal_solver.py`): 2-sum via a hash pass (O(n)), 3-sum via sort +
two-pointer (O(n²)), 4-sum via meet-in-the-middle (O(n²)) — replacing brute force's O(n^k), correctness-
checked against the brute-force solver on identical inputs before any speed claim was trusted.

The first version of this table was wrong in a way worth stating plainly, because it flattered the
result. `build_scale_case`'s `group_size` counts the target transaction itself, so the sweep I ran
(`group_size=3`) meant only **two** other transactions had to cancel — a 2-sum. Every row of the
published timing table consequently read `2-sum-hash`: the 3-sum and 4-sum paths were built, tested,
and never once exercised by the evidence that described them. The sweep now runs 3/4/5, so all three
algorithms actually run.

| True group | n_total | Algorithm used | Optimal | Brute force |
|---|---|---|---|---|
| 2 others | 500 | 2-sum-hash | 0.00006s | 0.0226s |
| 2 others | 5,000 | 2-sum-hash | 0.00045s | skipped |
| 3 others | 500 | 3-sum-two-pointer | 0.00056s | 1.5405s |
| 4 others | 100 | 4-sum-meet-in-the-middle | 0.00146s | 0.4259s |

Speed was never the frontier, and with all three paths running the real frontier is far closer in
than what I published before. Disambiguation is the wall, and it arrives much earlier the larger the
true group is:

| True group | n=50 | n=100 | n=200 | n=500 | n=1,000 | n=1,500 | n=5,000 |
|---|---|---|---|---|---|---|---|
| 2 others | 100% | 100% | 100% | **96.7%** | 100% | 80% | 27% |
| 3 others | 100% | 96.7% | 73% | 30% | 10% | 3% | 0% |
| 4 others | 100% | 63% | **3%** | 0% | 0% | 0% | 0% |

(30 seeds per cell.) The 96.7% cell is one I previously published inside a blanket "100% across 30
seeds up to n_total=1000" — it is 29/30, and rounding it up into a neighbouring claim is exactly the
kind of thing this file exists to not do.

The mechanism is now measured rather than inferred. The solver stops at the first group that cancels,
so a **coincidental smaller** group pre-empts the real one: at a 4-member true group and n=5,000 that
happens on 30 of 30 seeds. Every "wrong" answer still genuinely cancels the target — it just isn't
the constructed one. So the honest headline is not "this rule works up to n=1500", it is: at a
genuinely multi-way group of four, the strongest rule I could write is already unreliable at **n=200**,
which is a perfectly ordinary settlement batch.

Reproduce: `python scripts/generate_multiway_netting_optimal_solver_evidence.py`. Raw evidence:
[`multiway-netting-optimal-solver-2026-08-29.json`](evidence/multiway-netting-optimal-solver-2026-08-29.json).

## Breaking the "shared author" problem

On `duplicate_refund`/`netting_trap`, mock scores 100% because the same author wrote both the
generator's injectors and `check_batch_anomalies`'s detector to the same exact-match definition — see
"Where the rule beats the LLM" below. Held-out near-miss variants
(`enable_held_out_variants`, `app/data_gen/generate.py`) are still genuinely the same true category,
perturbed by a small, disclosed epsilon the exact-match rule can never confirm:

| Provider | Accuracy |
|---|---|
| mock | 0/101 — expected, confirmed empirically |
| Ollama (`qwen2.5:7b-instruct`) | 0/21 |

Ollama does **not** generalize past the rule's brittleness here either — but the raw reasoning traces
show a more interesting failure than "can't do arithmetic": several traces correctly notice the
near-cancellation, then the model's own `verify_group_sum` call (a strict exact-zero check, correct
for `multiway_netting_trap`) reports the candidate doesn't cancel exactly, and the model — following
its own instruction to never assert an unverified explanation — appropriately declines rather than
guess. The same cautious tool-use discipline this project credits elsewhere works against success on
this specific task, a real tool-design tension, not a reasoning failure.

Reproduce: `python scripts/generate_held_out_variant_evidence.py`. Raw evidence:
[`held-out-variant-evidence-2026-08-29.json`](evidence/held-out-variant-evidence-2026-08-29.json).

## A category that genuinely requires reading, not a lookup

`narration_explained` (`enable_narration_explained`): a delta explained only by the settlement's own
free-text remarks field (`Settlement.bank_narration`, eight varied, realistically messy templates) —
never by any structured field or delta-arithmetic a rule could check at any scale, not even the
combinatorial `multiway_netting_trap` machinery.

| Provider | Accuracy |
|---|---|
| mock | 0/64 — never calls `read_bank_narration`, structural |
| Ollama (`qwen2.5:7b-instruct`) | **10/10** |

No tool-design tension here (unlike the held-out variants above) — reading comprehension over free
text has no strict-verification step to conflict with, so the model's own capability is free to work,
cleanly.

Reproduce: `python scripts/generate_narration_explained_evidence.py`. Raw evidence:
[`narration-explained-evidence-2026-08-29.json`](evidence/narration-explained-evidence-2026-08-29.json).

## Which model, measured — not an anecdote

Compares `qwen2.5:7b-instruct` against `qwen2.5:14b-instruct` (both confirmed pulled/running locally)
on the two categories actually shown to be hard above — deliberately not re-sweeping the easy
categories, where every model size is already expected to score ~100%.

| Category | 7b | 14b |
|---|---|---|
| `multiway_netting_trap` | 4/7 | **1/7** |
| `narration_explained` | 4/5 | 5/5 |

The larger model does *worse* on the tool-budget-constrained task: reading the raw traces, 14b
explores more per case (redundant `recall_similar_resolutions` calls, checking irrelevant tools) and
more often runs out of the same 6-round budget before converging. On the pure-reading task, with no
budget tension, the larger model's extra capacity has room to help. Reported as measured, not tuned —
the honest, apples-to-apples comparison under an identical budget is the finding, not a number to
optimize away.

Reproduce: `python scripts/generate_multi_model_evidence.py`. Raw evidence:
[`multi-model-evidence-2026-08-29.json`](evidence/multi-model-evidence-2026-08-29.json).

## Real load, measured against a live server

Not the in-process `TestClient` concurrency tests already use — real HTTP requests against a
genuinely running server (`scripts/load_test.py`), `POST /api/run`, 3 requests per worker at each
concurrency level:

| Concurrency | Requests | Succeeded | Errors | Mean latency |
|---|---|---|---|---|
| 1 | 3 | 3 | 0 | 2.157s |
| 8 | 24 | 24 | 0 | 2.726s |
| 32 | 96 | 96 | 0 | 4.750s |

100% success at every concurrency level tested, zero errors. Latency degrades gracefully (roughly
2.2x at 32x the concurrency), not catastrophically. This doesn't prove single-instance SQLite scales
indefinitely — it means "this architecture would fall over under real concurrent load" isn't
supported by what was actually measured in this range (see [LIMITATIONS.md](LIMITATIONS.md)).

Reproduce: start the server, then `python scripts/load_test.py`. Full table:
[`load-test-2026-08-29.txt`](evidence/load-test-2026-08-29.txt).

## The original hand-built experiment, kept for context

Before `multiway_netting_trap` shipped as a real category, this project measured the same underlying
task with a smaller, hand-built stress case (a target transaction plus 10 others — one real 2-member
group, 8 distractors — deltas varying per seed, no other subset coincidentally cancelling, verified
by brute force):

| Provider | Without a verification tool | With `verify_group_sum` |
|---|---|---|
| Groq (`openai/gpt-oss-20b`) | 1/8 (4/8 on a prior run — real sampling variance, both kept) | **8/8, both times run** |
| Ollama (`qwen2.5:7b-instruct`, local) | 0/8 | 1/8 |

The raw fractions overstate how much of this is reasoning failure — read all 32 raw responses, not
just the scores. **Groq without verification**: 4 of 8 turns returned empty/unparseable content (a
non-answer, not a wrong one); of the 4 that actually answered, 1 was correct. **With verification,
all 8 answered and all 8 were correct** — the tool didn't just raise accuracy, it eliminated the
non-answers entirely. **Ollama with verification**: 4 of 8 never converged within the tool-call
budget (the extra round-trip the tool adds gives a small model more chances to get stuck, not
fewer), 1 more returned unparseable content — leaving 3 that actually answered, of which 1 was
correct. Ollama's *non-verified* baseline had 0 empty responses in 8, but 2 were unparseable/garbled
(the same non-answer category as Groq's, above) and the other 6 answered and were simply wrong — a
different failure shape than the verified condition's non-convergence.

One specific Ollama failure is worth naming directly: asked to investigate `order_f9d807a89e11`,
the model queried the tool with the id `f9d807a89e11` (missing the `order_` prefix — its own
error, not a garbled tool result), got back a real, correct `{"error": "no transaction ... in this
batch"}`, and then answered *"this transaction is alone in its settlement batch"* — narrating a
tool error as a confirmed finding rather than recognizing the lookup had failed. This is the exact
failure mode the production narrator's schema/id validation exists to catch (see
[WHAT_BROKE.md](WHAT_BROKE.md)); this experiment's own harness has no equivalent guard, since it's
a one-off measurement, not shipped code.

Full raw evidence for all four conditions, all 8 seeds each:
[`multiway-netting-experiment-2026-08-28.json`](evidence/multiway-netting-experiment-2026-08-28.json).
Reproduce: `python scripts/generate_multiway_netting_evidence.py`.

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                                          # 337 tests
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
python scripts/measure_mock_narrator_accuracy.py
python scripts/measure_mock_narrator_accuracy_multiway.py
python scripts/generate_multiway_netting_trap_production_evidence.py
python scripts/generate_multiway_netting_optimal_solver_evidence.py
python scripts/generate_held_out_variant_evidence.py
python scripts/generate_narration_explained_evidence.py
```

Full reproduction notes, including why `backend/data/*.db` is gitignored and what to use instead:
see the comments in [`scripts/audit_calibration.py`](../backend/scripts/audit_calibration.py).
