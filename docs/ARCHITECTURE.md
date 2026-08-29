# Architecture

An agent that reconciles merchant ledger data against Razorpay settlement data across payment
rails, narrates exactly where each mismatch broke in the transaction's causal chain, and
auto-resolves only the exception categories it has measured itself accurate on — everything else
escalates with a stated reason. Numbers for every claim below: [RESULTS.md](RESULTS.md). Real bugs
found while building this: [WHAT_BROKE.md](WHAT_BROKE.md).

## What makes this different from a flat matcher

1. **Causal chain matching, not row matching.** Every transaction is `order → payment → capture →
   fee → refund(s) → settlement`. A mismatch is located at the specific hop where the number
   diverges, not just flagged as "doesn't match."
2. **Calibrated autonomy.** Historical accuracy is tracked per exception category against a
   held-out ground-truth key. Only categories with measured accuracy above a threshold (checked
   against a Wilson confidence interval's lower bound, not the raw percentage) auto-resolve.
   Everything else escalates.
3. **Agentic narrator, not a one-shot classifier.** The discrepancy narrator is a tool-calling loop
   — it looks things up (fee schedule, SLA window, batch cross-reference, prior resolutions) before
   committing to an answer, and the tool-call trace is shown alongside the verdict.
4. **Contestable autonomy.** The auto-resolve threshold is a live dashboard control, not a constant
   baked in before a demo — dragging it recomputes the auto-resolve/escalate split and the ₹-at-risk
   number instantly, since confidence and category are already scored per transaction.

## Data model

Field names mirror Razorpay's real public API shapes (`utr` on Settlement, `fee`/`tax`/`captured`
on Payment, an `entity` type tag) rather than generic finance jargon.

- **Order**: `order_id`, `merchant_id`, `amount`, `currency`, `created_at`, `rail` (upi/card/netbanking)
- **Payment**: `payment_id`, `order_id`, `status`, `captured`, `captured_amount`, `fee_amount`,
  `tax_amount`, `gateway`, `captured_at`
- **Refund**: `refund_id`, `payment_id`, `amount`, `refund_type` (full/partial)
- **Settlement**: `settlement_id`, `payment_id`, `settled_amount`, `settlement_batch_id`, `utr`,
  `rail`, `settled_at`, `sla_days` (UPI ≈ T+1, cards ≈ T+2, netbanking varies — modeled explicitly,
  since settlement timing across rails is where a lot of real reconciliation difficulty lives)
- **Ledger entry**: the merchant's own books — `order_id`, `expected_amount`, `recorded_at`
- **Ground truth**: hidden from the matching/narrator/calibration logic entirely, used only to
  score decisions — `transaction_id`, `true_label`, `injected_by_you`

The synthetic generator injects known patterns on purpose — a duplicate refund applied twice, two
transactions that net to zero at the batch level but are each individually wrong, a settlement short
by exactly a fee amount, a genuinely unexplainable case — plus a separate, 100%-adversarial stress
batch (never blended into the main batch's reported accuracy) whose only job is one clean,
unimpeachable score: every case in it either escalated or resolved correctly, never wrongly
auto-resolved.

## System architecture

```
Synthetic Data Gen (ground truth hidden)
        │  orders, payments, refunds, settlements, ledger + adversarial cases + stress batch
        ▼
Causal Chain Builder
        │  order → payment → fee → refund → settlement, per transaction, per rail
        ▼
Matching Engine
        │  Pass 1: exact match. Pass 2: structured diff (fee schedule / SLA / rounding)
        ▼
   ┌────┴─────┐
   │          │
Clean     Exceptions → Agentic Narrator (tool-calling LLM)
matches         │        category + confidence + reasoning + tool-call trace
                ▼
         Calibration Layer (live threshold dial, Wilson CI, ₹-at-risk)
                │
                ▼
      Auto-resolve  OR  Escalate (ranked by ₹ amount × ambiguity)
                │              ▲
                │        human resolves → feeds back into that
                │        category's accuracy
                ▼
         Audit Logger (every decision, tool trace, source-row links)
                │
                ▼
         React/TS Dashboard
```

Deterministic Pass 1/2 resolves the large majority of a batch with zero LLM calls — that's the
design, not a shortfall. The narrator's original three categories don't need real judgment, and its
own accuracy on that classification task is measured honestly, not assumed. Two later categories,
`multiway_netting_trap` and `narration_explained`, genuinely do — see [RESULTS.md](RESULTS.md) for
what the rule alone already gets right, where the LLM's real value actually is, and "Where genuine
judgment lives" below for what that looks like once it's a shipped category, not an anecdote.

## Core components

**Matching engine.** Pass 1 is exact match on reference + amount + date. Pass 2 (unresolved only) is
a structured diff — is the delta explained by the known fee schedule, a partial refund, or a timing
lag within the rail's own SLA window? A Merkle-tree divergence pre-filter (`app/matching/
merkle_prefilter.py`) was built and benchmarked at realistic scale (50,000 records), then honestly
left out of the default path: its own hashing cost exceeded what it saved, since every transaction
still needs a full causal chain built regardless. Kept as a tested capability for the case it
actually helps — ledger and settlement data living in separate services, where the pre-filter avoids
the fetch, not just an already-cheap in-memory comparison.

**Agentic narrator** (`app/narrator/`). A tool-calling loop, not a single completion, over six
tools: `lookup_fee_schedule`, `check_sla_window`, `check_batch_anomalies` (duplicate-refund and
netting-trap detection, consolidated into one tool once both turned out to need the same
cross-transaction lookup), `recall_similar_resolutions`, and — brought in once `multiway_netting_trap`
and `narration_explained` shipped as real categories — `list_batch_deltas`/`verify_group_sum` (a group
of other transactions that collectively cancels a delta) and `read_bank_narration` (the settlement's
own free-text remarks field). `recall_similar_resolutions` persists across runs: when
`build_tool_context` is given the same `AuditLogger` the run will log its own decisions to, it first
seeds the in-run audit log with every categorized decision that logger has ever recorded, so a
brand-new run's very first transaction already has real cross-run memory, not just whatever
accumulates within that one run (see [RESULTS.md](RESULTS.md) for live-measured numbers, and
[LIMITATIONS.md](LIMITATIONS.md) for what this doesn't cover). Output is strict JSON: category,
confidence, one-line reasoning, and the full tool-call trace. Three backends behind one entry point:
`mock` (zero-cost, calls the same real tools, fixed-rule synthesis), `ollama` (local, zero cost, zero
rate limit, the recommended default), and `groq` (hosted, kept as a second real option).

**Calibration layer** (`app/calibration/`). Per-category accuracy against ground truth, reported
with a Wilson score interval, gated on the CI *lower bound*. Two additional, deliberately
conservative gates, both found necessary during the build: history accumulates across runs and human
confirmations (a single 50-200 record batch mathematically cannot clear a 90% bound at realistic
per-category sample sizes), and a `distinct_transaction_count` floor prevents the same small case set
re-observed across repeated runs from counting as new evidence. An EWMA drift check watches *recent*
decisions specifically, so a category regressing right now gets caught even while its all-time
average still looks fine — a controlled drill measuring exactly how fast this actually revokes trust
is in [RESULTS.md](RESULTS.md). Mock-provider decisions are tracked but never count toward any of
these gates — mock is a zero-cost stand-in for pipeline testing, not AI judgment.

**Escalation triage.** Ranked by ₹ amount × ambiguity (inverse confidence), not arrival order.

**Baseline comparison.** A naive exact-match-only reconciler runs on the same batch, no chain logic,
no LLM, reported side by side with the full system's own result.

**Audit logger.** Every decision — matched, escalated, auto-resolved — with timestamp, reasoning,
tool-call trace, and a link back to the source ledger/settlement rows, so a reviewer can click
through and verify a decision instead of taking the narration on faith.

## Where genuine judgment lives in the product

On the original three categories, the deterministic rule already matches the LLM exactly (see
[RESULTS.md](RESULTS.md)) — no genuine ambiguity is left for a classifier to resolve, so the LLM's
value there is reliability under failure, not judgment. Two categories exist specifically because the
rule structurally cannot resolve them at any scale, brought in from side experiments into the real,
calibration-gated decision loop, not left beside the product:

**`multiway_netting_trap`.** `check_batch_anomalies` only ever checks pairs — a discrepancy explained
only by a *group* of 3+ other transactions collectively cancelling it is invisible to a pairwise
check by construction. `app/data_gen/generate.py`'s `_gen_multiway_netting_trap` injects a real,
brute-force-verified-unique group (opt-in, `enable_multiway_netting`, default off so existing
evidence stays valid); `list_batch_deltas`/`verify_group_sum` let a real provider search for and
verify a hypothesis. `mock` fails structurally (never calls either tool); real providers solve most
of it. A separate, harder experiment (`app/narrator/multiway_netting_scale_experiment.py`) tests the
same task at real settlement-batch scale (hundreds of transactions) and finds two distinct failure
modes, not a clean degradation curve — see [RESULTS.md](RESULTS.md). A third module
(`app/narrator/multiway_netting_optimal_solver.py`) builds the strongest deterministic rule actually
worth building for this task — real O(n)/O(n²) k-sum algorithms, not brute force — and finds the
honest frontier is disambiguation at scale, not compute time.

**`narration_explained`.** A delta explained only by the settlement's own free-text remarks field
(`Settlement.bank_narration`, new field, eight varied realistic templates) — never by any structured
field or delta-arithmetic a rule could check at any scale, not even the combinatorial machinery
above. `read_bank_narration` exposes the raw text; `mock` never calls it, structurally; a real
provider reads it cleanly (see [RESULTS.md](RESULTS.md)) — no tool-design tension here, unlike the
held-out variants below, since reading comprehension has no strict-verification step to conflict with.

**Held-out near-miss variants**, separately, target the "shared author" problem directly:
`enable_held_out_variants` perturbs `duplicate_refund`/`netting_trap` cases by a small, disclosed
epsilon `check_batch_anomalies`'s exact-match check can never confirm, while the true category stays
the same. Neither `mock` nor the recommended local model solves these — reading the real reasoning
traces shows why in [RESULTS.md](RESULTS.md), a genuine tool-design tension rather than a reasoning
gap.

None of these are auto-adopted by fiat — every one earns auto-resolve through `CalibrationHistory`'s
own accumulated real evidence exactly like the original three, and `multiway_netting_trap` may never
clear that bar under the zero-cost `mock` default given it fails there by construction. That's the
disclosed, expected shape of a category the rule genuinely cannot touch, not something worked around.

## Beyond reconciliation: the other three Track 04 directions

Track 04 names four example directions. Multi-source reconciliation above is this project's primary
identity; the other three are also built — see [RESULTS.md](RESULTS.md) for every number.

**Tax-line matcher.** Three fee-leak patterns (`app/feeleak/detector.py`): a blended/flat rate applied
instead of an instrument's own contracted rate, GST computed on the gross amount instead of the
gateway fee, and GST computed on the correct base but at the wrong rate (a real other GST slab
mistakenly applied instead of 18%) — all invisible to standard reconciliation, since a transaction can
reconcile perfectly while still being charged an inconsistent fee. Checked against the merchant's own
contracted rate,
not a blanket legal claim about UPI MDR — Section 10A of the Payment and Settlement Systems Act (the
zero-MDR mandate) was amended on 4 August 2026, replacing the blanket prohibition with a
government-notification framework, three weeks before this was written. Completed by
`app/erp/gstr2b.py`, matching the
merchant's own ITC ledger against a *simulated* GSTR-2B counterpart (real structure, verified before
building; the counterpart is simulated because a real reconciliation needs an independent second
side, and this project only has its own books).

**ERP posting.** `app/erp/journal.py` turns a resolved transaction into a balanced double-entry
journal — GST-on-fee always a separate ITC-eligible line from the fee itself, a Reconciliation
Suspense line that's algebraically zero (and omitted) for anything fully explained, and a real,
correctly-sized number for anything that isn't. A transaction still in the escalation queue posts
`finalized: false` with a pending-review note, never a silently-forced entry. Exports to Tally XML
(structure verified against Tally's own published sample docs), Zoho Books CSV, and a generic CSV.

**Forward cash forecaster.** `app/forecast/predictor.py` predicts settlement date and net amount for
a payment that hasn't settled yet, using only the order, the payment, and the merchant's own known
fee/SLA schedule — never a Settlement record, which doesn't exist yet for this to be a genuine
prediction rather than a lookup. Backtested two ways: against a batch's own real settlements (which
share the predictor's own schedule), and, separately, genuinely blind (`app/forecast/blind_backtest.py`)
against a batch whose real settlements were computed with a hidden schedule drift the predictor never
sees — see [RESULTS.md](RESULTS.md) and [LIMITATIONS.md](LIMITATIONS.md) for exactly what each one
does and doesn't measure.

**Settlement Q&A agent.** `app/qa/agent.py` is a second, separate agentic loop — free-text questions
over a whole batch, not one transaction, answered by real tool calls with the trace shown. Its own
system prompt and answer contract are different from the narrator's; the two don't share code beyond
the provider-dispatch/circuit-breaker/retry machinery.

**Category discovery.** `app/narrator/discovery.py` — when the narrator would otherwise stop at
`genuine_error`, one additional model call proposes a named, evidence-grounded hypothesis instead.
Never auto-adopted into the real category taxonomy; shown as "unreviewed" for a human to confirm.

**Regret in rupees.** `app/calibration/regret.py` replays accumulated history chronologically to
compute the *realized* cost of autonomy — only decisions actually auto-resolved while their category
was already qualified, that were actually wrong — distinct from `amount_at_risk`'s forward-looking
estimate.

## The real Razorpay connector

`app/connectors/razorpay_sandbox.py` makes live calls against the actual Razorpay Test Mode API, not
simulated ones. Four of five causal-chain hops are real API objects on a live account: a real order, a
real captured payment (Netbanking; Cards rejected as international, UPI not offered — a real
account-level finding), real non-null fee/tax fields, and a real partial refund. The fifth hop,
settlement, doesn't exist here because it structurally can't: verified against Razorpay's own
documentation that test-mode payments are excluded from the real settlement pipeline, on any account,
permanently. The synthetic generator covers the settlement leg alone, for exactly this reason.

**Webhook receiver.** `app/webhooks/razorpay.py`, `POST /api/webhooks/razorpay` — real HMAC-SHA256
signature verification and real `settlement.processed` event parsing, both checked against
Razorpay's own current docs before writing this, not guessed. Refuses to run unverified (401 on a
missing/wrong signature, 500 if no webhook secret is configured), constant-time comparison on the
security-critical check. Verifies and parses; doesn't pretend a settlement-only event can
reconstruct a full causal chain on its own — see [LIMITATIONS.md](LIMITATIONS.md) for that boundary.

## Tech stack

Python + FastAPI, SQLite, React 19 + TypeScript. LLM: Ollama (local `qwen2.5:7b-instruct`) as the
zero-cost, zero-rate-limit default, Groq (`openai/gpt-oss-20b`) as a second hosted option — chosen
after a full free-tier survey (Cerebras, Gemini, DeepSeek, GLM, SambaNova, OpenRouter, GitHub Models,
Mistral) found every other hosted option rate- or credit-capped too tightly for a real batch.
`qwen2.5:14b-instruct` was pulled and measured too, specifically to make "which model" a real
comparison rather than an anecdote — it does not universally beat the smaller default (see
[RESULTS.md](RESULTS.md)). `gpt-oss-120b` was never included in any comparison in this project — no
verified hosted or local path was confirmed available, and no claim is made that it was tested.
