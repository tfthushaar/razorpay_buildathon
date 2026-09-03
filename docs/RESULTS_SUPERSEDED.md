# Superseded experiments

These are the experiments that led to the inversion. They measure an architecture this project no
longer uses: one category per mechanism, with the model asked for a label. Kept because the path
matters, and because each one is the reason the next was built.

Current results: [RESULTS.md](RESULTS.md). Architecture: [ARCHITECTURE.md](ARCHITECTURE.md).

## Multi-way netting as a shipped category

`check_batch_anomalies` only checks pairs. A delta explained by a group of three or more transactions
collectively cancelling it is invisible to a pairwise check by construction.

| Provider | Accuracy |
|---|---|
| mock | 0/42, structural |
| Ollama `qwen2.5:7b-instruct` | 5/7 |
| Groq `openai/gpt-oss-20b` | 6/7 |

Reproduce: `python scripts/generate_multiway_netting_trap_production_evidence.py`. Raw:
[`multiway-netting-trap-production-2026-08-29.json`](evidence/multiway-netting-trap-production-2026-08-29.json).

## The same task at settlement-batch scale

500 to 800 transactions in one batch. Two different failure modes on two providers.

Ollama fails at every scale tested, 20 through 760 transactions (0/36). Not context overflow: it
accumulates an ever-growing candidate list across tool-call rounds instead of searching small subsets
systematically.

Groq solves n=20 and degrades quickly. By n≥200 every call in the committed sweep returned 429.
Reading the error text shows the account's free-tier daily token quota (200,000/day), exhausted by
cumulative use across the session, which is confounded with the per-request
context wall confirmed separately.

A magnitude pre-filter rescues neither. Loose enough to rarely discard the real answer, it barely
narrows a large request. Tight enough to shrink one, it discards the real answer over 40% of the time.

Reproduce: `python scripts/generate_multiway_netting_scale_evidence.py`. Raw:
[`multiway-netting-scale-experiment-2026-08-29.json`](evidence/multiway-netting-scale-experiment-2026-08-29.json).

## The strongest deterministic solver, and its real frontier

Real k-sum algorithms (`app/narrator/multiway_netting_optimal_solver.py`): 2-sum by hash pass, 3-sum
by sort and two-pointer, 4-sum by meet-in-the-middle.

| True group | n=100 | n=200 | n=500 | n=1,000 | n=1,500 | n=5,000 |
|---|---|---|---|---|---|---|
| 2 others | 100% | 100% | 96.7% | 100% | 80% | 27% |
| 3 others | 96.7% | 73% | 30% | 10% | 3% | 0% |
| 4 others | 63% | 3% | 0% | 0% | 0% | 0% |

30 seeds per cell. Compute is never the limit: under 2ms at n=5,000. Disambiguation is. The solver
stops at the first group that cancels, so a coincidental smaller group pre-empts the real one, and the
larger the true group the sooner that happens. At a four-member group and n=5,000 that occurs on 30 of
30 seeds.

An earlier version of this table reported `2-sum-hash` on every row and a frontier of n=1,500. See
[WHAT_BROKE.md](../WHAT_BROKE.md).

Reproduce: `python scripts/generate_multiway_netting_optimal_solver_evidence.py`. Raw:
[`multiway-netting-optimal-solver-2026-08-29.json`](evidence/multiway-netting-optimal-solver-2026-08-29.json).

## Held-out near-miss variants

Perturbed `duplicate_refund` and `netting_trap` cases, still the same true category, with an epsilon
the exact-match rule can never confirm. Built to break the shared-author problem.

| Provider | Accuracy |
|---|---|
| mock | 0/101 |
| Ollama `qwen2.5:7b-instruct` | 0/21 |

Ollama does not generalise past the rule's brittleness either, and the traces show why. Several
correctly notice the near-cancellation. Then `verify_group_sum`, a strict exact-zero check that is
correct for `multiway_netting_trap`, reports the candidate does not cancel. The model follows its own
instruction never to assert an unverified explanation and declines. The cautious tool-use discipline
credited elsewhere works against success here. That is a tool-design tension rather than a reasoning failure.

Reproduce: `python scripts/generate_held_out_variant_evidence.py`. Raw:
[`held-out-variant-evidence-2026-08-29.json`](evidence/held-out-variant-evidence-2026-08-29.json).

## `narration_explained` as its own category

A delta explained only by the settlement's free-text remarks field, never by any structured field or
delta arithmetic.

| Provider | Accuracy | 95% Wilson |
|---|---|---|
| mock | 0/143, never calls `read_bank_narration` | [0.0, 2.6] |
| Ollama `qwen2.5:7b-instruct` | 175/209 = 83.7% | [78.1, 88.1] |

The 83.7% replaces a 10/10 measured across 5 seeds. Raising the sample to 30 seeds moved the estimate
down 16.3 points. All 34 misses are the model answering `genuine_error`, so those escalate.

Under the current architecture the narration is an evidence channel feeding a decomposition rather than a
category of its own.

Reproduce: `python scripts/generate_narration_explained_evidence.py`. Raw:
[`narration-explained-evidence-2026-08-29.json`](evidence/narration-explained-evidence-2026-08-29.json).

## Model size on a tool-budget-constrained task

| Category | 7b | 14b |
|---|---|---|
| `multiway_netting_trap` | 4/7 | 1/7 |
| `narration_explained` | 4/5 | 5/5 |

The larger model scores worse where a fixed tool-call round budget applies. It explores more per case,
makes redundant calls, and more often exhausts the budget before converging. On a pure reading task
with no budget tension it scores slightly better. Bigger does not substitute for measuring the task.

`gpt-oss-120b` was never included. No verified hosted or local path was confirmed in this environment.

Reproduce: `python scripts/generate_multi_model_evidence.py`. Raw:
[`multi-model-evidence-2026-08-29.json`](evidence/multi-model-evidence-2026-08-29.json).

## The original hand-built experiment

Eight hand-constructed multi-way netting cases, before any of the above existed.

The first result (Groq 8/8, Ollama 1/8) had a leaked strategy in the prompt and a grader satisfied by
any mention of the right ids. Corrected, Groq needed a `verify_group_sum` tool to reach 8/8 again.
Ollama solved 1 of 8, with 4 runs never converging on any answer. One Ollama run hallucinated a
transaction id, received a real tool error, and narrated that error as a confirmed finding.

Reproduce: `python scripts/generate_multiway_netting_evidence.py`. Raw:
[`multiway-netting-experiment-2026-08-28.json`](evidence/multiway-netting-experiment-2026-08-28.json).

## Cascade routing

Free rule → 7b → 14b → human, each tier handling only what the tier below could not.

| Tier | Absorbed | Correct | Accuracy | Sec/resolved |
|---|---|---|---|---|
| keyword rule | 6 | 0 | 0.0% | ~0 |
| `qwen2.5:7b-instruct` | 54 | 12 | 22.2% | 2.64 |
| `qwen2.5:14b-instruct` | 0 | n/a | n/a | n/a |
| human | 0 | n/a | n/a | n/a |

20.0% end to end at 2.38s per case, worse than free parsimony and equal to running 7b on everything.
Two design errors, both mine. The model tiers escalate on verification failure, which in choice mode
can never happen, so the 14b tier never fired. Tier 0's gate measures whether the advice discriminated,
not whether the reading was correct.

No signal I tried correlates with correctness: self-reported confidence is uninformative, verification
is trivially satisfied, tie count measures the wrong quantity.

Reproduce: `python scripts/generate_cascade_evidence.py`. Raw:
[`cascade-routing-2026-08-29.json`](evidence/cascade-routing-2026-08-29.json).
