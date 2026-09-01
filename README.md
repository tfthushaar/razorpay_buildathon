# Settlement Reconciliation Copilot

Razorpay AI Buildathon 2026, Track 04.

## Who this is for

A merchant's finance analyst on the Tuesday after a settlement cycle. They have a Razorpay settlement
report, a bank statement, and their own ERP ledger, and the three do not agree. Their day goes on
deciding which mismatches are explained, which need a human, and which are money someone owes them.

This system does that triage. It closes 98.9% of a realistic 97%-clean batch deterministically (85.0%
at the demo's denser default), escalates the rest with the evidence attached, and refuses to
auto-resolve any category it has not measured itself accurate on. Today that means it refuses all of
them, which is the next section. It also audits the fee against the
merchant's contract, because a wrongly-charged fee reconciles perfectly.

Live: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)

## The gate says no

**98.9% of a realistic batch closes deterministically, by arithmetic, with no model and no gate
involved.** That is the product, and it needs no trust at all.

Separately, nothing has earned *model* autonomy — permission for a model to close a case without a
human — and that is a result rather than a gap.

The trust gate recomputed a Wilson lower bound after every batch and granted autonomy on the first
crossing. Wilson holds at a fixed n; re-checking and stopping when you like is optional stopping.
Tested against what the bound promises, P(bound > true accuracy) at most 5%, it delivered 9.72%,
10.12% and 8.77% at true accuracies of 88%, 90% and 92% — about twice its stated level, in the
direction that hands out autonomy nothing earned.

It now uses a confidence sequence, valid at every stopping time. Under it `netting_trap` scores 88.4%
and `duplicate_refund` 85.6% against a 90% bar, and both escalate. A payments company should want a
gate that refuses on 37 samples; a gate that has never refused has never been tested. At an 0.85 bar
the same evidence automates 59.3% of escalations at a 1.0% error rate, and that curve is in the
product rather than argued for in a document.

## A check Razorpay's own stack does not run

Reconciliation compares the settlement against the records. It never compares the fee against the
merchant's contract, so **a fee charged at the wrong rate reconciles cleanly forever**. Neither
Razorpay Recon nor Settlement Insights performs that check.

Three patterns ship: a card-grade rate applied to UPI or netbanking, GST computed on the gross
captured amount instead of on the fee, and a real GST slab applied instead of 18%.

| | |
|---|---|
| False positives | **0 across 51,000** ordinary transactions |
| Detection time | 0.06s |
| Per-transaction state | none — a pure arithmetic pass |

Zero false positives at that volume is what makes the check safe to run unattended, and the pass is
stateless, so scanning two hundred times more data costs nothing.

This is the one result here with no circularity in it. The amounts are synthetic; `FEE_PCT` and
`GST_RATE` are Razorpay's published contracted rates, and the same comparison runs unchanged against
a merchant's own. Separately, GST on the gateway fee is Input Tax Credit the merchant can claim, and
it is normally buried inside a single "gateway charges" ledger line where no accountant finds it —
the ERP export splits it onto its own ITC-eligible line. That is money already lost on transactions
that reconciled correctly.

## Where AI belongs

Every category I built for the model to handle fell to a rule I wrote afterwards. `netting_trap` to a
20-line check. `multiway_netting_trap` to a hash table. `narration_explained` to a keyword scan. Three
times is not bad luck. Settlement records come from deterministic processes, so their ground truth is
arithmetically derivable, and a rule always wins.

So I inverted the pipeline. The deterministic resolver runs first and keeps everything it can explain
alone. The model sees only what is left: two or more equally valid explanations, or none. A case a
rule could solve is taken by the rule, so it never sits inside a model's accuracy figure. With k valid
explanations, blind choice scores exactly 1/k, which makes the baseline computed.

The economics follow from the same fact. Chains and matching run at 20,513 tx/sec. A real model runs
at 2.58 tx/sec, about 8,000 times slower. At 100,000 transactions a day, 98,880 resolve
deterministically in about 5 seconds and the 1,120 reaching a model take 7 minutes. Running everything
through the model would take 10.8 hours.

![Escalation queue with tool-call trace expanded](docs/screenshots/04-escalation-tool-trace.png)
*A real escalated case, with the tool calls and results behind it.*

## Reading bank remittance advice

I wrote the strongest rule I could: fragment splitting, cause keywords, and a 29-entry negation-cue
list assembled with full sight of the generator's phrasing. Both readers were then tested on held-out
phrasing, with the domain vocabulary intact so the rule could not fail on a missing synonym.

| Reader | Phrasing its author saw | Held-out phrasing |
|---|---|---|
| best rule I could write | 95.2% [92.8, 96.9] | 61.7% [56.9, 66.2] |
| `qwen2.5:7b-instruct` | 79.8% [75.7, 83.3] | 72.6% [68.2, 76.7] |
| `qwen2.5:14b-instruct` | 86.9% [83.3, 89.8] | 81.7% [77.7, 85.1] |
| `openai/gpt-oss-20b` | 92.1% [89.2, 94.4] | 96.2% [93.9, 97.6] |

420 judgements per cell, and the intervals do not overlap on held-out phrasing. Most of the rule's
advantage was authorship, not reading. Two model families, so the finding is not about qwen.

**The honest limit of this one:** I wrote the rule and both phrase banks. So it shows that a rule
tuned to phrasing its author saw generalises worse than a model does to phrasing neither has seen. It
is not a claim about real bank text, and no absolute accuracy on production data follows from it. The
three-source and fee-leak results do not depend on it.

The accuracy gap understates it. On unfamiliar phrasing the rule reads a denial as a confirmation in
**38.3%** of judgements, asserting charges the text says were not applied. The models sit between 0.2%
and 3.6%, and their dominant error runs the other way: they miss the mention, so the case escalates.
Wrong, but safe.

## Where that pays, and where it does not

On the compound residual, a free heuristic that ignores the advice scores 31.7% against the 14b
reader's 26.7%, and the paired test gives p = 0.55. Reading did not help where it competes with a
structural prior.

On three-source matching every structured field is exhausted by construction, leaving only the
free-text settlement cycle. On held-out phrasing the best regex I could write buys **exactly nothing**
— 0 wins and 0 losses against not parsing the cycle at all, at seed 42 and at all ten seeds swept.
Four models were run across two families: qwen 7b scores 94.0%, and both `gpt-oss-20b` and
`gpt-oss-120b` reach **99.3%**, beating the regex on 21 paired cases and losing none. Re-weighting the
structured fields by log-odds estimated from data rather than by constants I chose lifts the baseline
to 92.0%, so the margin is measured against the strongest structured matcher rather than the weakest.

Scale buys nothing here in either family. The 120b matches the 20b case for case, and qwen's 14b reads
held-out phrasing *worse* than its 7b. Nor was one seed ever enough: across five draws the direction
holds every time but only one draw is significant alone, so the result rests on the pooled 56 wins to
23 at p = 0.0003 rather than on the single p = 0.049 published earlier.

The settlement Q&A agent splits the same way. Across nine questions with ground truth computed from
the batch, a keyword router scores 87.5% on my phrasing and **0.0%** on held-out phrasing; the model
goes 65.0% to 62.5%, and neither fabricated a transaction id.

Free text as one signal among several does not pay for itself. As the only evidence left it is worth
6 points, and the rule is worth zero.

## Forecasting

The forecaster declines cases where its own arithmetic does not apply, decided from Order, Payment and
Refund alone and never from a settlement that does not exist yet.

| Scored population | n | exact | median err | mean err |
|---|---|---|---|---|
| what it forecasts | 1,795 | **80.8%** | 0.00% | 3.18% |
| what it refuses | 205 | 0.0% | 77.85% | 107.22% |
| everything | 2,000 | 72.5% | 0.00% | 13.87% |

Five numbers because the mean sits far from the middle: 83% of it comes from five rows out of 1,795.

Date intervals are fitted on one batch and verified on twelve others. A calibrated 90% interval covers
93.8% of held-out settlements at 2.40 days wide, against the SLA window's 87.2% at 1.14 days. The SLA
window states no confidence level, so no coverage figure can falsify it. The calibrated one can be
checked, and it is conservative at every level from 50% to 99%.

## Verify it yourself

Nothing here needs an API key or a model. The mock provider is a real keyword rule rather than a
stub, so the deterministic half — which is 98.9% of a realistic batch — is fully exercised on a
machine with nothing installed but Python.

```bash
cd backend && pip install -r requirements.txt
python -m pytest tests/ -v                               # 585 tests, ~50s
python scripts/generate_generalization_evidence.py       # 0 wrong on shapes it was never built for
python scripts/generate_ablation_evidence.py             # what each tier is worth
python scripts/generate_sensitivity_evidence.py          # where the constants break
python scripts/audit_calibration.py --db ../docs/evidence/verified_calibration_history.db
```

The model columns are the exception and they say so: `generate_reading_evidence.py` needs Ollama,
`generate_three_source_evidence.py` needs Ollama or `--no-model`, and
`generate_three_source_second_family_evidence.py` needs a Groq key unless pointed at a local model
with `--provider ollama`. Every number they produce is
committed under [`docs/evidence/`](docs/evidence/), so you can check the claims against the raw runs
without reproducing them.

## What this can't do

I wrote both phrase banks, so the held-out phrasing is held out from the parser and not from me.
Cascade routing is built, measured at 20.0%, and does not work; it and the semantic-entropy signal
live in `backend/experiments/` rather than in the shipped tree, with their numbers in
[experiments/README.md](backend/experiments/README.md). Settlement is structurally unavailable in
Razorpay's test mode, so four of five causal-chain hops are real API objects and the fifth is
synthetic.

Full list: [LIMITATIONS.md](docs/LIMITATIONS.md).

## What broke

A hosted-model column once scored byte-identical to its baseline in both conditions. A missing API key
was raising on every call, the retry wrapper saw no rate-limit string, and it returned "no reading
available". Three hundred silent failures look exactly like a model that reads nothing, and I was one
edit from publishing "gpt-oss-20b buys nothing" as a fact about the model. The tell was the
three-decimal match.

Seventeen incidents with sourced attribution: [WHAT_BROKE.md](docs/WHAT_BROKE.md).

## Get it running

```bash
git clone https://github.com/tfthushaar/razorpay_buildathon.git && cd razorpay_buildathon
cd backend && python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000    # then: cd ../frontend && npm install && npm run dev
```

Full setup, providers, deployment: [SETUP.md](docs/SETUP.md).

## What to read, if you have fifteen minutes

This file, then [RESULTS.md](docs/RESULTS.md) for the numbers and
[LIMITATIONS.md](docs/LIMITATIONS.md) for what they do not support. That is the whole argument, about
twenty minutes.

Everything else is reference, opened when you want to argue with something specific:
[METHODS.md](docs/METHODS.md) for the derivations behind the statistics,
[WHAT_BROKE.md](docs/WHAT_BROKE.md) for seventeen incidents with sourced attribution,
[ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it fits together, and
[CREDITS.md](docs/CREDITS.md) for every borrowed method with its licence.

## Further reading

[Architecture](docs/ARCHITECTURE.md) · [Results](docs/RESULTS.md) · [Credit](docs/CREDITS.md) ·
[Superseded experiments](docs/RESULTS_SUPERSEDED.md) · [What broke](docs/WHAT_BROKE.md) ·
[Limitations](docs/LIMITATIONS.md) · [Positioning](docs/positioning.md) ·
[Screenshots](docs/screenshots.md) · [Evidence](docs/evidence/)

This was built with heavy use of an AI coding assistant across nine days, which is how one person
produced this much of it; every number in these docs is generated by a committed script and checkable
without taking my word for any of it. [BUILD_LOG.md](BUILD_LOG.md) is the unedited working journal,
4,357 lines of it, kept for provenance rather than offered as reading.
