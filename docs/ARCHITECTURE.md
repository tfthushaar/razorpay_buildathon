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
design, not a shortfall. The narrator is reserved for the three categories that need real judgment,
and its own accuracy on that classification task is measured honestly, not assumed — see
[RESULTS.md](RESULTS.md) for what the rule alone already gets right, and where the LLM's real value
actually is.

## Core components

**Matching engine.** Pass 1 is exact match on reference + amount + date. Pass 2 (unresolved only) is
a structured diff — is the delta explained by the known fee schedule, a partial refund, or a timing
lag within the rail's own SLA window? A Merkle-tree divergence pre-filter (`app/matching/
merkle_prefilter.py`) was built and benchmarked at realistic scale (50,000 records), then honestly
left out of the default path: its own hashing cost exceeded what it saved, since every transaction
still needs a full causal chain built regardless. Kept as a tested capability for the case it
actually helps — ledger and settlement data living in separate services, where the pre-filter avoids
the fetch, not just an already-cheap in-memory comparison.

**Agentic narrator** (`app/narrator/`). A tool-calling loop, not a single completion, over four
tools: `lookup_fee_schedule`, `check_sla_window`, `check_batch_anomalies` (duplicate-refund and
netting-trap detection, consolidated into one tool once both turned out to need the same
cross-transaction lookup), and `recall_similar_resolutions` (in-memory over the current run's own
audit log, not persisted across runs). Output is strict JSON: category, confidence, one-line
reasoning, and the full tool-call trace. Three backends behind one entry point: `mock` (zero-cost, calls the same
real tools, fixed-rule synthesis), `ollama` (local, zero cost, zero rate limit, the recommended
default), and `groq` (hosted, kept as a second real option).

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
prediction rather than a lookup. Backtested against a batch's own real settlements, honestly: see
[RESULTS.md](RESULTS.md) and [LIMITATIONS.md](LIMITATIONS.md) for exactly what the reported accuracy
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

## Tech stack

Python + FastAPI, SQLite, React 19 + TypeScript. LLM: Ollama (local `qwen2.5:7b-instruct`) as the
zero-cost, zero-rate-limit default, Groq (`openai/gpt-oss-20b`) as a second hosted option — chosen
after a full free-tier survey (Cerebras, Gemini, DeepSeek, GLM, SambaNova, OpenRouter, GitHub Models,
Mistral) found every other hosted option rate- or credit-capped too tightly for a real batch.
