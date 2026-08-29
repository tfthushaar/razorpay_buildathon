# Results

Every number with the command that reproduces it. Strongest first, not chronological. Experiments
measuring the architecture this project replaced: [RESULTS_SUPERSEDED.md](RESULTS_SUPERSEDED.md).

Comparisons run over identical cases, so paired tests are used rather than a comparison of two
independent intervals. Independent intervals ignore the pairing and are conservative: the three-source
intervals below overlap while the paired test on the same cases is significant.
See `app/calibration/significance.py`.

## Reading remittance advice: model vs. regex

Measured in isolation rather than inferred from end-to-end accuracy. The keyword baseline is two
separable stages: read the advice into assertions, then score every valid decomposition against them.
Stage two is bookkeeping a rule does perfectly. Only stage one is compared here, against ground truth
the generator records itself.

The rule is written to win: fragment splitting, cause keywords, and a 29-entry negation-cue list
assembled with full sight of the generator's phrasing.

| Reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| best keyword rule | 95.2% [92.8, 96.9] | 61.7% [56.9, 66.2] | −33.6 |
| `qwen2.5:7b-instruct` | 79.8% [75.7, 83.3] | 72.6% [68.2, 76.7] | −7.1 |
| `qwen2.5:14b-instruct` | 86.9% [83.3, 89.8] | 81.7% [77.7, 85.1] | −5.2 |

60 cases × 7 charge types = 420 judgements per cell. 95% Wilson intervals. On held-out phrasing the
rule's interval does not overlap either model's.

Held-out phrasing keeps the cause vocabulary recognisable (TDS, RSV, GST, MDR all still appear) and
changes only how applied-versus-not-applied is expressed: abeyance, rescinded, held over, zero-rated,
struck off, stood down, lapsed, contra. A test asserts the held-out bank contains none of the rule's
own cues.

The two failure modes are not interchangeable:

| Reader | Condition | Reads a denial as a confirmation | Misses a mention |
|---|---|---|---|
| keyword rule | seen | 6 (1.4%) | 0 |
| keyword rule | held-out | 161 (**38.3%**) | 0 |
| `qwen2.5:7b` | held-out | 15 (3.6%) | 69 |
| `qwen2.5:14b` | held-out | 14 (3.3%) | 47 |

On unfamiliar phrasing the rule asserts a charge the text explicitly denies in 38.3% of judgements,
eleven times either model's rate. In a system that files recovery claims against an acquirer, that is
a false claim about money. The models miss the mention instead, which leaves the component unexplained
and escalates the case.

Reproduce: `python scripts/generate_reading_evidence.py`. Raw:
[`advice-reading-2026-08-29.json`](evidence/advice-reading-2026-08-29.json).

## Three-source matching

A settlement report, a bank statement and an ERP ledger that never agreed, joined on nothing reliable.
This checks the residual argument itself: if under-determination only appeared in compound arithmetic,
it would be fair to suspect the arithmetic was built to produce it.

The hard case is two payouts to the same merchant, same amount, same day. Merchant, amount and date
stop discriminating at once, the truncated UTRs share a tail, and only the free-text settlement cycle
remains. Everything in the matcher is held identical across these columns. Only the cycle reader
changes.

| Cycle reader | Seen phrasing | Held-out phrasing | Gap |
|---|---|---|---|
| none | 91.3% [85.7, 94.9] | 88.0% [81.8, 92.3] | −3.3 |
| best regex parser | 98.7% [95.3, 99.6] | 88.0% [81.8, 92.3] | −10.7 |
| `qwen2.5:7b-instruct` | 98.0% [94.3, 99.3] | 94.0% [89.0, 96.8] | −4.0 |

150 settlements against 180 bank rows. The true row was reachable in 150/150 for every column, so
nothing is capped by filtering.

On seen phrasing the regex wins by one match. On held-out phrasing it scores 88.0%, identical to not
parsing the cycle at all, because the regexes match zero descriptions. The model wins 13 paired cases
and loses 4, exact McNemar **p = 0.049**. Conceding 2 cases to the regex takes that to p = 0.33, so the
result is significant but not robust to a couple of mis-scored cases.

Reproduce: `python scripts/generate_three_source_evidence.py`. Raw:
[`three-source-2026-08-29.json`](evidence/three-source-2026-08-29.json).

## End to end on the residual

Layer 0 enumerates every arithmetically valid decomposition. All columns choose from the identical
shuffled option list.

| Strategy | Seen phrasing | Held-out phrasing |
|---|---|---|
| chance, computed as 1/k | 6.3% | 6.1% |
| best keyword rule | 42.4% [30.5, 55.2] | 8.3% [3.6, 18.1] |
| model, whole option list (7b) | 5.1% [1.7, 14.0] | 5.0% [1.7, 13.7] |
| model reader (7b) | 25.4% [15.9, 38.1] | 20.0% [11.8, 31.8] |
| model reader (14b) | 35.6% [24.6, 48.3] | 26.7% [17.1, 39.0] |
| parsimony, ignores the advice | 25.4% [15.9, 38.1] | 31.7% [21.3, 44.2] |

59 to 60 under-determined cases per condition. The true answer was inside the 40-option window in
every one.

Three findings.

The keyword rule collapses to near-chance on held-out phrasing: 8.3% against a 6.1% floor.

Handing the model the whole option list scores at chance. Layer 0 has already done the arithmetic, so
asking the model to re-derive a subset-sum over 30 candidates is the one thing it is worst at.
Splitting the job so the model only reads takes 7b from 5.1% to 25.4% on identical data.

On held-out phrasing, parsimony scores 31.7% against the 14b reader's 26.7%. An earlier version of
this file said parsimony beat every reader. The paired test on those same cases gives 7 discordant one
way and 4 the other, p = 0.55. Parsimony is at least as good, and the difference is not
distinguishable at n=60. What is clear is that reading did not help where it competes with a
structural prior.

Reproduce: `python scripts/generate_residual_evidence.py`. Raw:
[`residual-architecture-2026-08-29.json`](evidence/residual-architecture-2026-08-29.json),
[`residual-architecture-14b-2026-08-29.json`](evidence/residual-architecture-14b-2026-08-29.json).

## Why the ambiguity is not a tolerance knob

The obvious objection is that tolerance-based matching manufactured the under-determination. The row
worst for the architecture is zero rounding noise and zero tolerance, exact integer arithmetic.

| Noise | Tolerance | Resolved | Under-determined | Unmatched | Median k | True answer recovered |
|---|---|---|---|---|---|---|
| 0 | 0 | 9 | 51 | 0 | 4 | 60/60 |
| 0 | 10 | 0 | 60 | 0 | 28 | 60/60 |
| 3 | 0 | 5 | 48 | 7 | 3 | 10/60 |
| 3 | 10 | 1 | 59 | 0 | 22 | 60/60 |

At exact match with no tolerance, 51 of 60 compound cases are still under-determined. Compositionality
does that. Tolerance amplifies it. This is a standing test, `test_compositionality_alone_...`.

The recovery column is what makes the rest meaningful. If Layer 0's candidate set did not contain the
truth, "the model chose wrong" and "the right answer was never on the table" would be
indistinguishable. It also found a real bug: percentage candidates computed off the post-fee hop
instead of the captured amount, giving 11/60.

## Cascade routing

Free rule → 7b → 14b → human, each tier handling only what the tier below could not.

| Tier | Absorbed | Correct | Accuracy | Sec/resolved |
|---|---|---|---|---|
| keyword rule | 6 | 0 | 0.0% | ~0 |
| `qwen2.5:7b-instruct` | 54 | 12 | 22.2% | 2.64 |
| `qwen2.5:14b-instruct` | 0 | — | — | — |
| human | 0 | — | — | — |

20.0% end to end at 2.38s per case, worse than free parsimony and equal to running 7b on everything.
Two design errors, both mine. The model tiers escalate on verification failure, which in choice mode
can never happen, so the 14b tier never fired. Tier 0's gate measures whether the advice discriminated,
not whether the reading was correct.

No signal I tried correlates with correctness: self-reported confidence is uninformative, verification
is trivially satisfied, tie count measures the wrong quantity.

Reproduce: `python scripts/generate_cascade_evidence.py`. Raw:
[`cascade-routing-2026-08-29.json`](evidence/cascade-routing-2026-08-29.json).

## Core reconciliation

| Claim | Number |
|---|---|
| Match rate, real provider | 99.3% of settlement value, 7 escalations of 120 |
| Match rate, mock provider | 86.0%, 18 escalations of 120 |
| Throughput | 5,508 tx/sec mock at 50k scale; 2.58 tx/sec with a real LLM |
| `netting_trap` | 59 distinct real cases, 98.3%, Wilson lower bound 91.0% |
| `duplicate_refund` | 37 distinct real cases, 100% |
| `genuine_error` | 80.3%, never auto-resolves by design |
| Auto-resolved with no human review | ₹4,86,473.13 across 59 distinct cases |
| Adversarial stress batch | 40/40 handled, 0 wrongly auto-resolved |

Reproduce: `python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db`.

After 8 accumulated Ollama batches, `netting_trap` cleared the 90% trust threshold and
`duplicate_refund` did the same separately. `genuine_error` sat at 80.3% and stayed escalated, because
no accuracy figure makes auto-resolving an admittedly-unexplained case correct.

A 20-line rule with zero LLM calls scores 519/519 on those three categories. That is why the LLM's
value there is reliability under failure, not judgment.

## Everything else

| Result | Number | Reproduce |
|---|---|---|
| Time-to-revocation drill | 1 wrong decision revoked a category, aggregate still 97.6% | `curl -X POST localhost:8000/api/drift/drill -H 'Content-Type: application/json' -d '{}'` |
| Realized regret | ₹0 across 8 real auto-resolved transactions | `GET /api/regret` |
| Fee leak, seed 42 n=20 | ₹1,497.40 recoverable, ₹15,181.65 miscalculated tax, 0 false positives in 260 | `test_fee_leak.py` |
| GSTR-2B match | 120 matched, 30 exceptions across 3 disjoint kinds | `GET /api/gstr2b` |
| Forecaster, n=30 | 9.1% MAPE, 93.3% interval coverage | `GET /api/forecast/backtest` |
| Blind backtest, seeds 1–20 | MAPE 0.11%, coverage 56.5% (range 3%–100%) | `GET /api/forecast/blind-backtest` |
| Load test | 100% success to 32 concurrent, 2.157s to 4.750s mean | `python scripts/load_test.py` |
| Cross-run tool memory | 834 prior `netting_trap` resolutions recalled on a fresh run | `GET /api/audit?run_id=<id>` |

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                 # 348 tests
python scripts/generate_reading_evidence.py
python scripts/generate_three_source_evidence.py
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
```
