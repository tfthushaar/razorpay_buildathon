# experiments/

Code that was built, measured, and does not ship. It lives outside `app/` so the shipped tree does
not contain a router that is known not to work.

Nothing here is imported by `app/main.py` or the batch pipeline. Each module is exercised by its own
evidence script and its own tests, and each has a published number.

| Module | What it is | Measured | Why it is here |
|---|---|---|---|
| `cascade.py` | Free rule → 7b → 14b → human, each tier handling only what the tier below could not | **20.0%** end to end at 2.4s per case, worse than a free parsimony heuristic | Both escalation gates are wrong. The model tiers escalate on verification failure, which in choice mode never happens, so the 14b tier absorbed zero cases. Tier 0's gate measures whether the advice discriminated, not whether the reading was correct. |
| `semantic_entropy.py` | A fourth escalation signal: resample the reader and measure how much it disagrees with itself | AUROC **0.633**, permutation **p = 0.0505** on 59 cases | Points the right way and is not distinguishable from chance at this n, and 0.633 is short of what a gate needs regardless. Suggestive, not established. |

Both are kept rather than deleted because a measured failure is a result. Deleting them would leave
[LIMITATIONS.md](../../docs/LIMITATIONS.md) asserting that cascade routing does not work with nothing
in the repository to check that against.

Derivations and the full numbers: [METHODS.md](../../docs/METHODS.md).
