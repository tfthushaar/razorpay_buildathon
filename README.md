# Settlement Reconciliation Copilot

Razorpay AI Buildathon 2026, Track 04.

## Who this is for

A merchant's finance analyst on the Tuesday after a settlement cycle. They have a Razorpay settlement
report, a bank statement, and their own ERP ledger, and the three do not agree. Their day is spent
deciding which mismatches are explained, which need a human, and which are money someone owes them.

This system does that triage. It closes 98.9% of a realistic 97%-clean batch deterministically (85.0%
at the demo's deliberately denser default), escalates what genuinely needs judgment with the evidence
attached, and refuses to auto-resolve anything it has not measured itself accurate on. It also audits
the fee against the merchant's contract, which reconciliation never does, because a wrongly-charged
fee reconciles perfectly.

Live: [razorpay-buildathon-five.vercel.app](https://razorpay-buildathon-five.vercel.app)

## Where AI belongs, measured rather than asserted

Every category I built for the model to handle fell to a rule I wrote afterwards. `netting_trap` to a
20-line check. `multiway_netting_trap` to a hash table. `narration_explained` to a keyword scan. Three
times is not bad luck. Settlement records come from deterministic processes, so their ground truth is
arithmetically derivable, and a rule always wins.

So I inverted the pipeline. The deterministic resolver runs first and keeps everything it can explain
alone. The model sees only what is left: cases where the resolver found two or more equally valid
explanations, or none. A case a rule could solve is taken by the rule, so it cannot sit inside a
model's accuracy figure. With k valid explanations, blind choice scores exactly 1/k, so the baseline
is computed rather than argued.

The economics follow from the same fact. Chains and matching run at 20,513 tx/sec on a realistic
97%-clean batch. A real model runs at 2.58 tx/sec, about 8,000 times slower. At 100,000 transactions a
day, 98,880 resolve deterministically in about 5 seconds and the 1,120 reaching a model take 7
minutes. Running everything through the model would take 10.8 hours. The resolver is what makes both
the economics and the accuracy figures work.

![Escalation queue with tool-call trace expanded](docs/screenshots/04-escalation-tool-trace.png)
*A real escalated case, with the tool calls and results behind it.*

## Reading bank remittance advice

I wrote the strongest rule I could: fragment splitting, cause keywords, and a 29-entry negation-cue
list assembled with full sight of the generator's phrasing. Then I tested both readers on held-out
phrasing the cue list has never seen, with the domain vocabulary intact so the rule could not fail on
a missing synonym.

| Reader | Phrasing its author saw | Held-out phrasing |
|---|---|---|
| best rule I could write | 95.2% [92.8, 96.9] | 61.7% [56.9, 66.2] |
| `qwen2.5:7b-instruct` | 79.8% [75.7, 83.3] | 72.6% [68.2, 76.7] |
| `qwen2.5:14b-instruct` | 86.9% [83.3, 89.8] | 81.7% [77.7, 85.1] |

420 judgements per cell. The intervals do not overlap on held-out phrasing. Most of the rule's
advantage was authorship, not reading.

The accuracy gap understates it. On unfamiliar phrasing the rule reads a denial as a confirmation in
**38.3%** of judgements, asserting charges the text says were not applied. Both models sit at 3.3% to
3.6%, and their dominant error runs the other way: they miss the mention, so the case escalates.
Wrong, but safe.

## Where that does and does not pay

On the compound residual, a free heuristic that ignores the advice scores 31.7% against the 14b
reader's 26.7%. The paired test gives p = 0.55, so parsimony is at least as good. Reading did not help
where it competes with a structural prior.

On three-source matching, every structured field is exhausted by construction and only the free-text
settlement cycle remains. Same matcher, same weights; only the cycle reader changes. On held-out
phrasing the regex scores 88.0%, identical to not parsing at all. The local model scores 94.0%,
winning 13 paired cases and losing 4 (exact McNemar p = 0.049).

When free text is one signal among several, the model does not pay for itself. When it is the only
evidence left, it is worth 6 points and the rule is worth zero.

## Money the merchant is already losing

Reconciliation compares the settlement against the records. It never compares the fee against the
merchant's contract, so a fee charged at the wrong rate reconciles cleanly forever. Neither Razorpay
Recon nor Settlement Insights performs that check.

Three patterns ship: a card-grade rate applied to UPI, GST computed on the gross amount instead of the
fee, and a wrong GST slab. False positive rate is **0 across 51,000** ordinary transactions, in 0.06s.
That is what makes it safe to run unattended.

GST on the gateway fee is Input Tax Credit the merchant can claim, normally buried in one "gateway
charges" ledger line. The ERP export splits it onto its own line per transaction. Not an exception to
investigate; money lost on transactions that reconciled correctly.

Full numbers with reproduce commands: [RESULTS.md](docs/RESULTS.md).

## Verify it yourself

```bash
cd backend && python -m pytest tests/ -v                 # 350 tests — needs nothing but Python
python scripts/generate_reading_evidence.py              # the table above  (needs Ollama)
python scripts/generate_three_source_evidence.py         # the McNemar result (needs Ollama)
```

## What this can't do

The held-out phrase banks are held out from the parser, not from me. I wrote both. Cascade routing is
built, measured at 20.0%, and does not work. Three-source matching runs from its own script and tests,
not from the shipped batch loop. Settlement is structurally unavailable in Razorpay's test mode, so
four of five causal-chain hops are real API objects and the fifth is synthetic.

Full list: [LIMITATIONS.md](docs/LIMITATIONS.md).

## What broke

A hosted-model column scored byte-identical to its baseline in both conditions. A missing API key was
raising on every call, the retry wrapper saw no rate-limit string, and it returned "no reading
available". Three hundred silent failures look exactly like a model that reads nothing. I was one edit
from publishing "gpt-oss-20b buys nothing" as a fact about the model. The tell was the three-decimal
match to the baseline.

A throughput figure improved 3.8× between passes because the metric changed scope, not speed. I
deleted the old row without saying so, promoted the new one to this README, and shipped it with no
reproduce command and no evidence file. The new figures were also single unrepeated runs, and the
medians are 17% and 25% lower.

Also: a timing table comparing three algorithms that only ever ran one, and a result I overstated
against my own architecture off a three-case difference.

Eleven incidents with sourced attribution: [WHAT_BROKE.md](docs/WHAT_BROKE.md).

## Get it running

```bash
git clone https://github.com/tfthushaar/razorpay_buildathon.git && cd razorpay_buildathon
cd backend && python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000    # then: cd ../frontend && npm install && npm run dev
```

Full setup, providers, deployment: [SETUP.md](docs/SETUP.md).

## Further reading

[Architecture](docs/ARCHITECTURE.md) · [Results](docs/RESULTS.md) ·
[Superseded experiments](docs/RESULTS_SUPERSEDED.md) · [What broke](docs/WHAT_BROKE.md) ·
[Limitations](docs/LIMITATIONS.md) · [Positioning](docs/positioning.md) ·
[Screenshots](docs/screenshots.md) · [Evidence](docs/evidence/) · [Build log](BUILD_LOG.md)
