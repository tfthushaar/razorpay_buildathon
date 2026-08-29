# Settlement Reconciliation Copilot

**Razorpay AI Buildathon 2026 — Track 04.** Reconciles merchant ledger data against Razorpay
settlement data, narrates which hop in a transaction's causal chain broke, and auto-resolves only
what it has measured itself accurate on.

**Live**: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)

![Escalation queue with tool-call trace expanded](docs/screenshots/04-escalation-tool-trace.png)
*A real escalated case — the `check_batch_anomalies` / `check_sla_window` /
`recall_similar_resolutions` tool calls and results behind it.*

## The result

| Metric | Result | Reproduce |
|---|---|---|
| Match rate | 99.3% of settlement value, real provider, 7 escalations of 120 | `cd backend && python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db` |
| Throughput | 2.58 tx/sec (real LLM, measured) — 5,508 tx/sec (mock, 50k scale) | [SETUP.md](docs/SETUP.md) |
| Per-category accuracy | `netting_trap` 98.3% (91.0% Wilson lower bound), `duplicate_refund` 100%, `genuine_error` 80.3% (never auto-resolves) | same command |
| Adversarial stress batch | 40/40 correctly handled, 0 wrongly auto-resolved | [raw output](docs/evidence/verified-ollama-run-2026-08-25.json) |
| Auto-resolved with zero human review | ₹4,86,473.13, 59 distinct real cases | same command |
| `multiway_netting_trap` — genuine judgment, real category | rule 0/42 (structural), Ollama 5/7, Groq 6/7 | `python scripts/generate_multiway_netting_trap_production_evidence.py` |
| Reading bank advice, held-out phrasing | best rule 61.7% (−33.6 pts), `qwen2.5:14b` 81.7% (−5.2) — and the rule reads a denial as a confirmation 38.3% of the time | `python scripts/generate_reading_evidence.py` |
| Strongest rule's real frontier | at a genuine 4-member netting group it's unreliable by **n=200**, not n=1,500 | `python scripts/generate_multiway_netting_optimal_solver_evidence.py` |
| Three-source entity resolution, held-out phrasing | best regex cycle parser 88.0% (buys nothing over no parsing), model reading the same text **94.7%** | `python scripts/generate_three_source_evidence.py` |

Full numbers, every claim in this file: [RESULTS.md](docs/RESULTS.md).

## How it works

1. **Causal chain, not row matching.** Every transaction is `order → payment → fee → refund(s) →
   settlement`; a mismatch is located at the specific hop that diverges.
2. **Autonomy that's earned and revocable.** Per-category accuracy is tracked against a Wilson
   lower bound, and an EWMA drift check pulls a category back to escalating the moment its recent
   decisions regress, even while its all-time average still looks fine. A controlled drill found
   1 wrong decision was enough to revoke a category's trust, with the aggregate still at 97.6%.
3. **Audits the fee, not just the reconciliation.** A transaction can reconcile perfectly while
   still being charged a fee inconsistent with the merchant's contract — only comparing against the
   contracted rate catches it. Completed by a match against a simulated GSTR-2B.

A second agentic loop answers free-text questions over a batch; one more model call proposes a named
hypothesis instead of stopping at `genuine_error`; a forecaster predicts settlement date and net
amount before a payment settles. Detail: [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Where the LLM sits, including where it doesn't

85% of a batch resolves deterministically, zero LLM calls. And for a long stretch of this build, every
category I added for the model to handle eventually fell to a rule I wrote afterwards — `netting_trap`
to a 20-line check, `multiway_netting_trap` to a hash table, `narration_explained` to a keyword scan.
Three times is not bad luck. Settlement records come from deterministic processes, so their ground
truth is arithmetically derivable, so for any classification task posed over them a rule exists that
wins.

So the pipeline is inverted now. The deterministic resolver runs **first** and keeps everything it can
explain on its own; the model only ever sees what's left, which has two shapes: the resolver found
**two or more equally valid explanations** and has no basis to choose, or it found **none**. That's a
structural guarantee, not a claim — a case a rule could solve was taken by the rule, so it can't be
inside a model's accuracy number inflating it. With k valid explanations, blind choice scores exactly
1/k, so the baseline is computed rather than argued.

The obvious objection is that I manufactured the ambiguity with a tolerance knob. At **zero** rounding
noise and **zero** tolerance — exact integer arithmetic — 51 of 60 compound cases are still
under-determined. Compositionality does that, not the tolerance.

The model's output changed with it: not a label (precisely what a lookup table produces) but a
decomposition that must sum to the observed delta and cite real objects whose properties support the
amounts claimed. Both are deterministic checks, and a failed check hands back the specific complaint
for another attempt — so confidence becomes *survived n verification rounds* instead of a number the
model asserts about itself.

**The result that settles where AI belongs here.** I wrote the strongest rule I could for reading bank
remittance advice — fragment splitting, cause keywords, a 29-entry negation-cue list assembled with
full sight of the generator's own phrasing. It reads at **95.2%**, beating `qwen2.5:7b` (79.8%) and
`14b` (86.9%). Then I tested on held-out phrasing the cue list has never seen, keeping the domain
vocabulary recognisable so it couldn't fail on a missing synonym:

| Reader | Phrasing its author saw | Held-out phrasing | Gap |
|---|---|---|---|
| best rule I could write | **95.2%** | 61.7% | **−33.6 pts** |
| `qwen2.5:7b-instruct` | 79.8% | 72.6% | −7.1 pts |
| `qwen2.5:14b-instruct` | 86.9% | **81.7%** | −5.2 pts |

The ordering inverts. Most of the rule's advantage was authorship, not reading. And the accuracy gap
understates it: on unfamiliar phrasing the rule reads a **denial as a confirmation** in 38.3% of
judgements — asserting charges the text explicitly says were *not* applied, which in a system that
files recovery claims is a false claim about money. Both models sit at 3.3–3.6%, and their dominant
error runs the safe way (missing a mention, so the case escalates).

**The result that cuts against that.** End-to-end on the compound-delta residual, the strategy that
wins on held-out phrasing is a free heuristic that ignores the advice entirely — "always take the
fewest-component explanation" scores **31.7%**, beating the 14b reader at 26.7% and burying the
collapsed keyword rule at 8.3%. There, reading competes against a strong structural prior that already
does most of the work, and it loses.

**And the one place the model wins outright.** Real reconciliation isn't gateway data matched against
itself — it's three sources that never agreed: the settlement report, the bank statement, and the
merchant's ERP ledger, joined on nothing reliable. The hard case is the one every subscription
business produces constantly: *two payouts to the same merchant, same amount, same day*. Merchant,
amount and date all stop discriminating, the truncated UTRs share a tail, and the only thing left is
the settlement cycle — which the bank writes in free text, wherever it likes, a third of the time not
at all. Same matcher, same filters, same weights; the only thing swapped is what reads the cycle:

| | Seen phrasing | Held-out | Gap |
|---|---|---|---|
| no cycle parsing at all | 91.3% | 88.0% | −3.3 |
| best regex cycle parser I could write | **98.7%** | 88.0% (buys *nothing*) | −10.7 |
| a model reading the same text | 98.0% | **94.7%** | −3.3 |

The model recovers **10 of the 18 matches** the regex loses and cuts unresolved cases from 10 to 2.

Put together, the claim I'll defend is specific rather than triumphant: **when free text is one signal
among several, a model doesn't pay for itself — and when the structured fields are exhausted and the
text is the only evidence left, it's worth 6.7 points and the rule is worth zero.** Full numbers,
every raw evidence file: [RESULTS.md](docs/RESULTS.md).

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                 # 337 tests
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
python scripts/measure_mock_narrator_accuracy.py
```

All three work on a genuinely fresh clone. Full reproduction notes: [RESULTS.md](docs/RESULTS.md).

## What this can't do

- Not horizontally scaled — one FastAPI instance, SQLite. A real load test found no acute problem up
  to 32 concurrent requests (100% success, latency degrading gracefully); Postgres/worker-pool
  narration remain deferred as unmeasured-as-necessary, not built speculatively.
- Settlement is structurally unavailable in Razorpay's test mode, on any account — 4 of 5
  causal-chain hops are real API objects; the fifth is synthetic for exactly this reason.
- The forecaster is exact by construction on ~73% of transactions (the merchant's own known fee/SLA
  schedule, not a learned model). A separate, genuinely-blind backtest against a hidden schedule
  drift shows why that matters: amount error stays under 0.2%, but date-window coverage swings
  3%–100% seed to seed.
- `multiway_netting_trap` breaks down at real settlement-batch scale (500+ transactions) — two
  different failure modes on two different providers, neither fixed by a magnitude pre-filter.
- The "held-out" advice phrasing is held out from the *parser*, not from me — I wrote both phrase
  banks. It's a real test of the rule, not a test against real bank text.
- On familiar phrasing the best rule I could write still beats the local model on the residual. The
  model's advantage here is specifically generalisation and failure mode, not raw accuracy.
- The real Razorpay webhook receiver verifies and parses; it can't reconcile a settlement-only event
  on its own — the order/payment/ledger side lives in the merchant's own separate integration.

Full list: [LIMITATIONS.md](docs/LIMITATIONS.md).

## What broke

- A category's earned trust could be ridden by a mock-mode guess in the same category.
- The same unguarded-concurrent-write bug shape recurred five times, across the narrator and then
  the API's own run state.
- An external review caught a headline rupee figure inflated by re-scoring the same transactions
  across runs.
- The flagship multi-way netting experiment's own first result (Groq 8/8) turned out to have a
  leaked strategy and a trivially-satisfiable grader; corrected, Groq needed a verification tool to
  earn 8/8 again — honestly, this time.
- Wiring that same category into the real product, a live Groq run got the arithmetic right and the
  category wrong; one added sentence in the system prompt took it from 3/7 correct to 6/7.
- I published a timing table comparing three k-sum algorithms that had only ever run one of them,
  because a `group_size` parameter counts the target transaction itself.
- My new resolver's candidate pool looked entirely plausible and never contained the true answers —
  every percentage was computed off the wrong hop. Only a scoring question caught it, not reading.
- My own prompt didn't implement my own architecture: I handed the model a subset-sum problem Layer 0
  had already solved, then presented the options in an order that leaked the answer through position.
- I built a cascade router on an escalation signal that can never fire — in choice mode a verified
  answer is verified by construction, so the gate was structurally dead.

Nineteen incidents, fixed format: [WHAT_BROKE.md](docs/WHAT_BROKE.md).

## Get it running

```bash
git clone https://github.com/tfthushaar/razorpay_buildathon.git && cd razorpay_buildathon/backend
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt   # Windows: .venv\Scripts\activate
python -m uvicorn app.main:app --reload --port 8000   # then: cd ../frontend && npm install && npm run dev
```

Full setup, real LLM providers, Docker, deployment: [docs/SETUP.md](docs/SETUP.md).

## Further reading

[Architecture](docs/ARCHITECTURE.md) · [Results](docs/RESULTS.md) ·
[What broke](docs/WHAT_BROKE.md) · [Limitations](docs/LIMITATIONS.md) ·
[Where this fits in Razorpay's own stack](docs/positioning.md) ·
[Screenshots](docs/screenshots.md) · [Evidence](docs/evidence/)

[BUILD_LOG.md](BUILD_LOG.md) — raw chronological journal, ~41,600 words, kept as an appendix.
