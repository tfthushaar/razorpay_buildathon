# Settlement Reconciliation Copilot

Razorpay AI Buildathon 2026, Track 04 · **Live:
[razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)**

A merchant's finance analyst on the Tuesday after a settlement cycle. They have a Razorpay settlement
report, a bank statement and their own ERP ledger, and the three disagree. This does that triage: it
closes **98.9% of a realistic batch deterministically**, escalates the rest with the evidence
attached, refuses to auto-resolve any category it has not measured itself accurate on, and audits the
fee against the contract — because a wrongly-charged fee reconciles perfectly.

## Money the merchant is already losing

Reconciliation compares the settlement against the records. It never compares the fee against the
merchant's contract, so **a fee charged at the wrong rate reconciles cleanly forever**. Neither
Razorpay Recon nor Settlement Insights runs that check.

Three patterns ship: a card-grade rate applied to UPI or netbanking, GST computed on the gross
captured amount instead of on the fee, and a real GST slab applied instead of 18%.

| False positives | Detection | State |
|---|---|---|
| **0 across 51,000** ordinary transactions | 0.06s | none — a pure arithmetic pass |

Zero false positives at that volume is what makes it safe to run unattended, and a stateless pass
scales to two hundred times the data for free. `FEE_PCT` and `GST_RATE` are Razorpay's published
rates, so the same comparison runs unchanged against a merchant's own contract.

GST on the gateway fee is also Input Tax Credit the merchant can claim, normally buried in one
"gateway charges" ledger line where no accountant finds it. The ERP export splits it onto its own
ITC-eligible line — money already lost on transactions that reconciled correctly.

## Where AI belongs

Every category I built for a model to handle fell to a rule I wrote afterwards: `netting_trap` to a
20-line check, `multiway_netting_trap` to a hash table, `narration_explained` to a keyword scan. Three
times is a pattern. Settlement records come from deterministic processes, so their ground truth is
arithmetically derivable and a rule wins.

So I inverted the pipeline. The deterministic resolver runs first and keeps everything it can explain
alone. The model sees only the residual: two or more equally valid explanations, or none. A case a
rule could solve is taken by the rule, so it never inflates a model's accuracy figure. With k valid
explanations, blind choice scores exactly 1/k — the baseline is computed, not assumed.

The economics follow. Matching runs at 20,513 tx/sec against a model's 2.58, about 8,000x slower. At
100,000 transactions a day, 98,880 resolve in about 5 seconds and the 1,120 reaching a model take 7
minutes. Everything through the model would take 10.8 hours.

![Escalation queue with tool-call trace expanded](docs/screenshots/04-escalation-tool-trace.png)
*A real escalated case, with the tool calls and results behind it.*

## The gate says no

Nothing has earned model autonomy, and that is a result. The gate recomputed a Wilson lower bound
after every batch and granted autonomy on the first crossing — but Wilson holds at fixed n, and
re-checking until you like the answer is optional stopping. Tested against its own promise, that
P(bound > true accuracy) stays under 5%, it delivered **9.72%, 10.12% and 8.77%**: roughly twice its
stated level, in the direction that hands out autonomy nothing earned.

It now uses an anytime-valid confidence sequence. Under it `netting_trap` scores 88.4% and
`duplicate_refund` 85.6% against a 90% bar, and both escalate. A payments company should want a gate
that refuses; a gate that has never refused has never been tested. At an 0.85 bar the same evidence
automates 59.3% of escalations at a 1.0% error rate, and that curve ships in the product.

## What the evidence says

**Reading bank remittance advice.** I wrote the strongest rule I could — fragment splitting, cause
keywords, a 29-entry negation-cue list, all with full sight of the generator's phrasing — then tested
both readers on phrasing neither had seen.

| Reader | Phrasing its author saw | Held-out phrasing |
|---|---|---|
| best rule I could write | 95.2% [92.8, 96.9] | 61.7% [56.9, 66.2] |
| `qwen2.5:7b-instruct` | 79.8% [75.7, 83.3] | 72.6% [68.2, 76.7] |
| `openai/gpt-oss-20b` | 92.1% [89.2, 94.4] | 96.2% [93.9, 97.6] |

420 judgements per cell; the held-out intervals do not overlap. Worse than the gap: on unfamiliar
phrasing the rule reads a denial as a **confirmation in 38.3%** of judgements, asserting charges the
text says were never applied. The models sit between 0.2% and 3.6%, and they fail the safe way — they
miss the mention, so the case escalates.

**Three-source matching**, where every structured field is exhausted by construction and only the
free-text settlement cycle remains:

| Cycle reader | Held-out phrasing | Paired vs the regex |
|---|---|---|
| best regex I could write | 85.3% | **0 wins, 0 losses** vs not parsing at all |
| `qwen2.5:7b-instruct` | 94.0% | 20W / 7L, p = 0.019 |
| `gpt-oss-20b` and `gpt-oss-120b` | **99.3%** | **21W / 0L**, p < 0.000001 |

Four models across two families. Scale buys nothing in either: the 120b matches the 20b case for
case, and qwen's 14b reads *worse* than its 7b. One seed was never enough either — across five draws
the direction holds every time but only one draw is significant alone, so this rests on the pooled 56
wins to 23 at p = 0.0003, not on the single p = 0.049 I published earlier.

**Forecasting.** The forecaster declines cases its arithmetic does not cover, scoring 80.8% exact on
the 1,795 it accepts against 0.0% on the 205 it refuses. A calibrated 90% date interval covers 93.8%
of held-out settlements at 2.40 days wide, against the SLA window's 87.2%.

## What this can't do

I wrote both phrase banks, so held-out phrasing is held out from the parser and not from me. Cascade
routing is built, measured at 20.0%, and does not work. Settlement is structurally unavailable in
Razorpay's test mode, so four of five causal-chain hops are real API objects and the fifth is
synthetic. Full list: [LIMITATIONS.md](docs/LIMITATIONS.md).

A hosted-model column once scored byte-identical to its baseline because a missing API key was failing
silently on every call, and I was one edit from publishing "gpt-oss-20b buys nothing" as a fact about
the model. Seventeen incidents with sourced attribution: [WHAT_BROKE.md](docs/WHAT_BROKE.md).

## Verify it yourself

No API key and no model needed. The mock provider is a real keyword rule, so the deterministic half —
98.9% of a realistic batch — is fully exercised on a machine with nothing but Python.

```bash
cd backend && pip install -r requirements.txt
python -m pytest tests/ -v                            # 585 tests, ~50s
python scripts/generate_generalization_evidence.py    # 0 wrong on shapes it was never built for
python scripts/generate_ablation_evidence.py          # what each tier is worth
python scripts/generate_sensitivity_evidence.py       # where the constants break
python -m uvicorn app.main:app --port 8000            # then: cd ../frontend && npm i && npm run dev
```

Only the model columns need more: Ollama, or a Groq key for the hosted ones. Every number they produce
is committed under [`docs/evidence/`](docs/evidence/), checkable against the raw runs without
reproducing them.

## Further reading

[RESULTS.md](docs/RESULTS.md) for the numbers · [LIMITATIONS.md](docs/LIMITATIONS.md) for what they do
not support · [METHODS.md](docs/METHODS.md) for the statistics ·
[ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CREDITS.md](docs/CREDITS.md) for every borrowed method and
its licence · [SETUP.md](docs/SETUP.md) · [Screenshots](docs/screenshots.md)

Built with heavy use of an AI coding assistant over nine days, which is how one person produced this
much. Every number is generated by a committed script; [BUILD_LOG.md](BUILD_LOG.md) is the unedited
working journal, kept for provenance rather than offered as reading.
