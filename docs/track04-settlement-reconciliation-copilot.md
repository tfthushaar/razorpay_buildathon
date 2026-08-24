# Settlement Reconciliation Copilot with Calibrated Autonomy
### Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller

---

## 1. One-line pitch

An agent that reconciles merchant ledger data against Razorpay settlement data across multiple payment rails, narrates *exactly where* each mismatch broke in the transaction's causal chain, and only auto-resolves the exception categories it has proven itself accurate on — everything else escalates with a stated reason.

## 2. Why this idea, why this scope

Razorpay's own brief for Track 04 says: *"Throughput plus measured accuracy plus an honest exception list. One cherry-picked match proves nothing."* Their published judging criteria (from the wider Buildathon materials) weight two things explicitly:

- **AI Judgment** — was AI/agentic behavior used appropriately, not forced in?
- **Failure Recovery** — how did you identify runtime failures and engineer graceful fallbacks?

This build is scoped to answer both directly, with a number, not a claim. Everything included below either (a) is required to reconcile correctly, or (b) exists specifically to produce proof for one of these two criteria. Nothing is included purely for novelty.

**Deliberately left out of v1** (good ideas, but scope risk): cross-merchant generalization testing, format-agnostic ledger ingestion, systemic root-cause clustering across many merchants. Add only if the core is fully working with time to spare.

---

## 3. The core mechanism (what makes it different from a flat matcher)

Most reconciliation demos do flat row-to-row matching: ledger row ↔ settlement row, flag what doesn't match. This build does two things differently:

1. **Causal chain matching, not row matching.** Every transaction is modeled as a chain: `order → payment → capture → fee → refund(s) → settlement`. A mismatch is located at the *specific hop* where the number diverges, not just flagged as "doesn't match."
2. **Calibrated autonomy.** The system tracks its own historical accuracy *per exception category* against a held-out ground-truth answer key. Only categories where it has proven accuracy above a threshold get auto-resolved. Everything else escalates to a human, with a reason. This is a direct, demonstrable answer to "AI Judgment" and "bounded and gated" — not a claim in your README, a chart in your dashboard.
3. **Agentic narrator, not a one-shot classifier.** The discrepancy narrator doesn't just pattern-match and guess a category — it's a tool-calling loop that can look things up (fee schedule, SLA window, duplicate-refund registry, prior similar resolutions) before it commits to an answer, and the tool-call trace is shown alongside the verdict. A judge can *watch it decide to go check something* instead of guessing — that's a literal, provable answer to "was AI/agentic behavior used appropriately," not an architecture-diagram claim.
4. **Contestable, not just calibrated, autonomy.** The auto-resolve threshold is a live control in the dashboard, not a constant baked in before the demo. A judge can drag it and watch the auto-resolve/escalate split and the ₹-at-risk number change in real time. "Bounded and gated" becomes something they operate, not something they're told about.

Optional stretch (only after core is solid): **Merkle-tree style divergence search** — hash-chunk the ledger and settlement feed (by date range → rail → merchant → transaction), compare hash trees top-down, and only inspect branches that actually diverge. Lets you report a real systems number: *"compared 50,000 records using ~200 comparisons."* Borrowed from how Cassandra/DynamoDB do anti-entropy repair between replicas — genuinely cross-disciplinary, not just finance jargon.

---

## 4. Data model

Generate **synthetic data with a hidden ground-truth answer key** — this is what lets you report real precision/recall instead of a demo that just "looks right." Do this first; everything else depends on it.

### Entities

**Order**
- `order_id`, `merchant_id`, `amount`, `currency`, `created_at`, `rail` (upi / card / netbanking)

**Payment**
- `payment_id`, `entity: "payment"`, `order_id`, `status` (captured / failed / pending / partial), `captured` (bool), `captured_amount`, `fee_amount`, `tax_amount`, `gateway`, `captured_at`

**Refund**
- `refund_id`, `payment_id`, `amount`, `status`, `created_at`, `refund_type` (full / partial)

**Settlement**
- `settlement_id`, `entity: "settlement"`, `payment_id`, `settled_amount`, `settlement_batch_id`, `utr`, `rail`, `settled_at`, `sla_days` (UPI ≈ T+1, cards ≈ T+2, netbanking varies — model this explicitly, it's the reason reconciliation across rails is genuinely hard)

**Ledger entry** (the "merchant's books")
- `ledger_id`, `order_id`, `expected_amount`, `recorded_at`

**Ground truth (hidden from the matching logic, used only for scoring)**
- `transaction_id`, `true_label` (clean_match / timing_lag / fee_deduction / partial_refund / duplicate / currency_rounding / genuine_error), `injected_by_you: true/false`

> **Field-naming note:** mirror Razorpay's actual public API field names where you reasonably can (`utr` on Settlement, `fee`/`tax` on Payment, a `captured` boolean, an `entity` type tag are all real, well-known fields on their Payments/Settlements/Refunds APIs). Pull the exact current shapes from their published API docs rather than inventing your own — it costs nothing and reads as domain fluency to reviewers who know their own API by heart.

### Inject on purpose (this is your "Failure Recovery" story)
- A duplicate refund that could get double-resolved if the system isn't careful
- Two separate transactions that net to the same settlement amount (classic reconciliation trap — looks like a clean match, isn't)
- A settlement that's short by exactly a fee amount (should resolve automatically, high confidence)
- A genuinely ambiguous case with no clean explanation (should escalate, not guess)

### A separate 100%-adversarial stress batch
Beyond the main 50-200 transaction batch (mixed distribution below), generate a **second, smaller batch that is nothing but traps** — every transaction in it is one of the adversarial types above. Never blend it into the main batch's reported accuracy. Its only job is to produce one clean, quotable stat: *"47/50 adversarial cases correctly escalated or resolved, 0 wrongly auto-resolved."* This is what makes the "break it" pitch moment provably not cherry-picked — you're not fishing one good trap out of a mixed batch, you're reporting the score on a batch that is entirely traps.

---

## 5. System architecture

```
┌─────────────────────┐
│ Synthetic Data Gen   │  → orders, payments, refunds, settlements, ledger
│ (ground truth hidden)│     + adversarial cases + separate 100%-adversarial
└──────────┬───────────┘     stress batch, real-Razorpay-shaped field names
           │
┌──────────▼───────────┐
│ Causal Chain Builder  │  → stitches order→payment→fee→refund→settlement
│                       │     per transaction, across 3 rails
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ Matching Engine       │  → exact match pass first (ref/amount/date)
│ (+ optional Merkle    │     then structured diff on unresolved chains
│  divergence search)   │
└──────────┬───────────┘
           │
     ┌─────┴──────┐
     │            │
┌────▼───┐   ┌────▼─────────────────┐
│ Clean   │   │ Exceptions            │
│ matches │   │ → Agentic narrator    │  → calls lookup_fee_schedule /
└────┬───┘   │   (tool-calling LLM)  │     check_sla_window / check_duplicate_
     │       └────┬───────────────────┘     registry / recall_similar_resolutions
     │            │                          → category + confidence + reasoning
     │            │                          + tool-call trace, all strict JSON
     │     ┌──────▼──────────────────┐
     │     │ Calibration Layer        │  → per-category accuracy + Wilson CI
     │     │ (live threshold dial —   │     vs ground truth, tracked live;
     │     │  not a fixed constant)   │     ₹-at-risk computed per setting
     │     └──────┬───────────────────┘
     │            │
     │     ┌──────▼───────────────────┐
     │     │ Auto-resolve   OR         │
     │     │ Escalate — ranked by      │
     │     │ ₹ amount × ambiguity      │
     │     └──────┬────────────────────┘
     │            │              ┌──────────────────────────┐
     │            │◄─────────────┤ Human resolves escalation  │
     │            │  feedback    │ → outcome feeds back into   │
     │            │  loop        │   that category's accuracy  │
     │            │              └──────────────────────────┘
┌────▼────────────▼────────┐
│ Audit Logger               │  → every decision, timestamped, reasoning,
│                             │     tool-call trace, linked to source rows
└────────────┬────────────────┘
             │
┌────────────▼────────────────┐
│ Naive Baseline (parallel run) │  → exact-match-only, for comparison
└────────────┬────────────────┘
             │
┌────────────▼────────────────┐
│ React/TS Dashboard             │  → match rate, ₹ reconciled, prioritized
│                                 │     exception queue w/ drill-down to source
│                                 │     rows, live threshold dial, calibration
│                                 │     curve w/ CI, baseline delta, adversarial
│                                 │     stress-test scorecard
└─────────────────────────────────┘
```

---

## 6. Build components — detail

### 6.1 Synthetic data generator
- Python script. Generate a realistic batch (aim for 50–200 transactions minimum per the "50+ record batch" bar in the brief).
- Vary rails (UPI/card/netbanking) with different settlement SLAs.
- Inject known error types with a documented distribution (e.g. 60% clean, 25% explainable exceptions, 10% adversarial traps, 5% genuinely ambiguous).
- Store the ground-truth key separately — the matching engine must never read it directly, only be scored against it.

### 6.2 Causal chain builder
- For each `order_id`, join across payment, refund(s), and settlement tables into one structured object.
- Compute expected vs. actual amount at each hop.
- Flag the exact hop where cumulative expected ≠ actual first diverges.

### 6.3 Matching engine
- Pass 1: exact match on transaction reference + amount + date.
- Pass 2 (unresolved only): structured diff — is the delta explainable by a known fee schedule, a partial refund, or a timing lag within the rail's SLA window?
- Optional: Merkle-tree hash comparison as a pre-filter before per-transaction diffing, to make the "efficient divergence-finding" claim provable.

### 6.4 Agentic discrepancy narrator (tool-calling LLM)
- Input: the causal chain object for one unresolved transaction.
- This is a tool-calling loop, not a single completion. Give it a small, real tool set before it's allowed to answer:
  - `lookup_fee_schedule(rail, date)` — is the delta explained by a known fee?
  - `check_sla_window(rail, settled_at)` — is this just a timing lag within the rail's normal settlement window?
  - `check_duplicate_registry(refund_id)` — has this refund already been applied once?
  - `recall_similar_resolutions(category)` — a lightweight retrieval over the audit log's past resolutions (plain SQLite lookup, no vector DB needed at this scale) so it can reason "I've seen this shape before, resolved as X."
- Output: strict JSON — `{category, confidence, one_line_reasoning, tool_calls: [...]}`. The tool-call trace is stored and shown, not thrown away — it's the evidence that the model checked before it guessed.
- Prompt it to explain *which hop broke and by how much*, not just "mismatch detected."
- Example target output: `"₹4,200 order → ₹4,116 captured after 2% fee → ₹3,800 settled, missing ₹316. Rail=UPI. Checked fee schedule (matches known 2% UPI fee) and SLA window (outside normal T+1 lag). Likely a reversed partial refund not yet reflected in ledger."`

### 6.5 Calibration / auto-resolve layer
- Maintain a running accuracy score per exception `category`, checked against ground truth as the batch processes.
- Report accuracy with a **Wilson score confidence interval**, not a bare percentage — per-category sample sizes in a 50–200 record batch are small (e.g. N=14), and a flat "92%" with no interval invites a technical judge to pick it apart. Set the auto-resolve threshold against the **CI lower bound**, not the point estimate — e.g. "auto-resolve only if the 95% CI lower bound clears 90%."
- The threshold itself is a **live control in the dashboard**, not a constant fixed before the demo — moving it recomputes the auto-resolve/escalate split and the ₹-at-risk number instantly. This is cheap to make interactive: confidence and category are computed once per transaction during batch processing, so a threshold change is just a filter/re-aggregation over already-scored rows, not a re-run.
- Compute and show **₹-at-risk**: the expected ₹ amount that would be wrongly auto-resolved if the threshold were lowered, at the current setting and a few points around it. This reframes the story from "we're X% accurate" to "here's what a wrong gate would actually cost" — the way a real finance controller thinks, not how an ML demo thinks.
- **Human-feedback loop:** when a human resolves an escalated case (via the dashboard), record the outcome and fold it back into that category's running accuracy live. A category that starts below threshold can visibly cross it mid-demo as resolutions accumulate — calibration re-earning trust in front of the judges, not just a static number computed once offline.
- **Calibration accumulates across runs, not just within one batch (found during the build, not planned upfront — see BUILD_LOG.md):** at this spec's own suggested batch size (50-200 records), a narrator category typically gets only 3-12 samples per batch, split across duplicate_refund/netting_trap/genuine_error. The Wilson lower bound cannot mathematically clear a 90% threshold at that N even at 100% point accuracy — it needs roughly N=40 same-category confirmations. Reset per batch, "calibrated autonomy" would be true but never demonstrable: every narrator-classified transaction would escalate forever. The fix is also the more realistic design: a persistent `CalibrationHistory` accumulates scored decisions across every batch run *and* every human-confirmed escalation, so a category's trust builds the way a real system's would — earned over time, not re-derived from zero on every run. Frame this as a strength in the pitch, not a caveat: the system is appropriately conservative by default (a handful of same-batch successes should not unlock autonomy), and demonstrably capable of earning it as evidence accumulates.
- Log this as a table: category, batch accuracy (with CI and N), decision (auto-resolve / escalate), count. This table, rendered as a simple bar or line chart, remains your single most important dashboard element.

### 6.6 Escalation triage
- Don't dump escalated exceptions in arrival order. Rank them by **₹ amount × ambiguity** (e.g. inverse confidence), so the highest-value, least-certain cases surface first.
- This is a small addition but it's what turns "here's a list of things we didn't resolve" into "here's how a reconciliation ops team would actually work the queue" — a production-readiness signal, not just a research one.

### 6.7 Baseline comparison
- Run a naive exact-match-only reconciler on the same batch, no LLM, no chain logic.
- Report side by side: match rate, ₹ correctly reconciled, false-positive rate if applicable.
- One sentence you want to be able to say in your pitch: *"Our system correctly reconciled X% vs. Y% for naive exact-match, a Z% lift, with zero mismatched adversarial cases wrongly auto-resolved."*

### 6.8 Audit logger
- Every decision (matched / escalated / auto-resolved), with timestamp, the narrated reason, the tool-call trace, and a link back to the source ledger/settlement rows it was based on — written to a simple table (SQLite/Postgres row or JSON log — doesn't need to be fancy).
- This is your primary source material for the "what broke and how I recovered" narrative Razorpay explicitly asks for in the submission, and the row-level links let a reviewer click through and verify a decision instead of taking the narration on faith.

### 6.9 Adversarial stress-test scorecard
- Run the dedicated 100%-adversarial batch (§4) through the full pipeline separately from the main batch.
- Report one clean, quotable number: *"47/50 adversarial cases correctly escalated or resolved, 0 wrongly auto-resolved."* Give this its own scorecard tile on the dashboard — it's the number a judge remembers.

### 6.10 Dashboard (React/TS)
- Match rate %, ₹ reconciled, prioritized exception queue with narration and drill-down to source rows.
- Calibration chart: accuracy per category (with CI) vs. auto-resolve decision — driven by a **live, draggable threshold control**, not a static snapshot.
- ₹-at-risk readout that updates as the threshold moves.
- Baseline comparison chart.
- Adversarial stress-test scorecard, always visible, not buried in a tab.
- A path to feed a judge-submitted or randomly reshuffled transaction chain through the pipeline live — this is your "break it" demo moment for the pitch video, and it should be provably not scripted (random seed per run, or a live-upload field), not the same four hardcoded cases every time.

---

## 7. Tech stack

- **Backend:** Python + FastAPI (or Flask if faster to stand up)
- **Database:** SQLite for the hackathon (Postgres if you want it to look more production-grade)
- **LLM:** Ollama (local `qwen2.5:7b-instruct`, OpenAI-tool-call-shaped) for the discrepancy narrator and classifier — structured JSON output, low temperature, zero cost and zero rate limit since it runs on-machine. Groq (openai/gpt-oss-20b, hosted, same tool-call contract) kept as a second real option. Originally planned as the Claude API; switched mid-build for cost (free tier) to Groq, then a full free-tier-API survey (Cerebras, Gemini, DeepSeek, GLM, SambaNova, OpenRouter, GitHub Models, Mistral) found every hosted option rate-limited or credit-capped in a way that made a full batch take 11-70 minutes, so the narrator moved to local inference — see BUILD_LOG.md
- **Frontend:** React 19 + TypeScript (your existing strength from Vera ERP)
- **Data generation:** Python (pandas/faker-style synthetic generation, with a documented seed for reproducibility)
- **Retrieval for `recall_similar_resolutions`:** plain SQLite lookup over the audit log's past resolutions — no vector DB needed at this batch size

---

## 8. Build order (target: ~10–12 focused days)

| Days | Task |
|---|---|
| 1–2 | Synthetic data generator (real-Razorpay-shaped fields) + hidden ground truth + adversarial cases + separate 100%-adversarial stress batch. Get this right first — everything depends on it. |
| 3–4 | Causal chain builder + exact-match pass + naive baseline |
| 5–6 | Agentic discrepancy narrator: tool-calling loop (fee schedule, SLA window, duplicate registry, similar-resolution recall) + structured classification |
| 7 | Calibration layer: per-category accuracy + Wilson CI, live threshold dial, ₹-at-risk calculation, human-feedback loop |
| 8 | Audit logger (incl. tool-call traces + source-row links) + baseline comparison reporting + escalation triage ranking |
| 9–10 | React dashboard: match rate, prioritized exception queue w/ drill-down, live calibration dial, baseline delta, adversarial stress-test scorecard |
| 11 | End-to-end test on full batch + stress batch; verify adversarial cases handled correctly, not just present; rehearse the live/judge-submitted "break it" demo path |
| 12 | Record 5-min pitch video (the live threshold dial and a live-submitted transaction are the centerpiece), write architecture doc + README, polish repo |

**Cut order if behind schedule:** Merkle-tree divergence search first (differentiator, not a requirement) → then the human-feedback loop and escalation triage ranking (nice-to-have polish). **Never cut:** the calibration layer, baseline comparison, or audit log — those map directly to what's being judged.

---

## 9. What to measure and report (have these numbers ready, not approximate)

- Overall match rate (%) — your system vs. naive baseline
- ₹ amount correctly reconciled — your system vs. naive baseline
- Per-category accuracy **with N and a 95% confidence interval** (from calibration layer) — not a bare percentage
- ₹-at-risk at the chosen auto-resolve threshold, and how it moves if the threshold shifts ±5pp
- Auto-resolve rate vs. escalation rate, and why the threshold was set where it was (tie this to the CI lower bound, not the point estimate)
- Adversarial case outcomes on the **dedicated 100%-adversarial stress batch** — explicitly state: did the system correctly avoid wrongly auto-resolving the trap cases, as a batch-level score, not one cherry-picked example?
- Tool-call usage rate in the narrator — what fraction of exceptions triggered at least one lookup, as evidence the agentic behavior is real and not decorative
- If using Merkle-tree pre-filtering: number of comparisons made vs. brute-force baseline

---

## 10. Submission checklist (per Razorpay's stated requirements)

- [ ] Public GitHub repo, clean commit history, clear README
- [ ] 5-minute pitch video — lead with a live "break it" moment (feed an ambiguous/adversarial transaction on camera, show the system correctly escalate instead of guessing)
- [ ] Architecture explanation (this document adapted into your README/submission doc)
- [ ] Explicit "what broke during development and how I fixed it" narrative — pull this directly from your audit log and real debugging moments, don't invent one
- [ ] Reproducible setup instructions (someone should be able to clone and run it)
- [ ] Honest exception list — do not hide unresolved or ambiguous cases; showing them is the point

---

## 11. Judging criteria alignment (for your own reference while building)

| Razorpay criterion | How this build answers it |
|---|---|
| Measured accuracy, honest exceptions | Calibration layer + Wilson CI + ground-truth scoring + prioritized, visible exception list |
| Throughput | Batch processed end-to-end, reported as a number |
| AI Judgment | Agentic tool-calling narrator (looks things up instead of guessing) + calibrated auto-resolve, only acting where it's proven accurate |
| Bounded and gated | Live, judge-operable threshold dial + ₹-at-risk readout — never blind auto-resolve |
| Failure Recovery | Adversarial cases + a dedicated 100%-adversarial stress batch + audit log + human-feedback loop that visibly re-earns trust + unscripted/live pitch-video "break it" moment |
| Real problem, not cherry-picked | Baseline comparison + stress-batch scorecard prove the lift and the safety are real, not staged |
| Domain fluency | Data model mirrors Razorpay's real Payments/Settlements/Refunds API field shapes, not generic finance jargon |
