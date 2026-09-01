# Credit

Every method, algorithm or line of code in this project that came from somewhere else, with its source and licence. 

Two kinds of entry:

- **Method** — I read the paper or the library's approach and wrote my own implementation. No code copied. The citation is owed for the idea.
- **Code** — the source's own code runs here, as a dependency or vendored. The licence governs.

Everything below is currently **Method**. No third-party reconciliation, statistics or matching code
is vendored or linked. 

## Statistics

| What | Source | Licence | Type | Used in |
|---|---|---|---|---|
| Split conformal prediction, and the `1/(n+1)` over-coverage bound | Vovk, Gammerman & Shafer, *Algorithmic Learning in a Random World* (2005); Tibshirani, [Berkeley conformal lecture notes](https://www.stat.berkeley.edu/~ryantibs/statlearn-s23/lectures/conformal.pdf) | n/a (published theory) | Method | `app/forecast/calibrated_interval.py` |
| Smoothed conformal predictors (tie-breaking by uniform draw) | Vovk et al. (2005) | n/a | **Considered and declined** | see [LIMITATIONS.md](LIMITATIONS.md) |
| Wilson score interval | Wilson (1927) | n/a | Method | `app/calibration/wilson.py` |
| Exact McNemar test for paired comparisons | McNemar (1947) | n/a | Method | `app/calibration/significance.py` |
| Betting confidence sequences, valid under optional stopping | Waudby-Smith & Ramdas, *Estimating means of bounded random variables by betting* (2023); Howard, Ramdas, McAuliffe & Sekhon, *Time-uniform Chernoff bounds* (2021) | n/a | Method | `app/calibration/confidence_sequence.py` |
| Risk-coverage curves for selective prediction | El-Yaniv & Wiener (2010); Geifman & El-Yaniv (2017) | n/a | Method | `app/calibration/risk_coverage.py` |
| Semantic entropy for detecting unreliable model output | Farquhar, Kossen, Kuhn & Gal, *Nature* (2024) | n/a | Method | `experiments/semantic_entropy.py` |
| Fellegi-Sunter probabilistic record linkage | Fellegi & Sunter (1969) | n/a | Method | `app/resolver/fellegi_sunter.py` |
| Meet-in-the-middle subset sum | Horowitz & Sahni (1974) | n/a | Method | `app/data_gen/subset_sum.py` |

## Comparable projects surveyed

Read for design ideas. No code taken from any of them.

| Project | Licence | What it contributed |
|---|---|---|
| [juspay/hyperswitch](https://github.com/juspay/hyperswitch) | Apache-2.0 | Its two-leg reconciliation model (order→PSP at 1:1, PSP→bank at N:1) named a structure this project does not have: a bank statement shows one credit per settlement batch, not one row per transaction. Recorded in [LIMITATIONS.md](LIMITATIONS.md). |
| [moj-analytical-services/splink](https://github.com/moj-analytical-services/splink) | MIT | Its argument that fuzzy matching uses arbitrary weights while probabilistic linkage estimates them from data. Acted on: `app/resolver/fellegi_sunter.py` replaces the hand-chosen three-source weights with log-odds estimated from a calibration batch. Splink itself is not a dependency; the model is about sixty lines for this case, and this project ships without an ORM or a query engine. |
| [Manu6259/financial-reconciliation-agent](https://github.com/Manu6259/financial-reconciliation-agent) | not stated | Closest peer. Confirms the deterministic-first shape independently. |

## Not used, and why

Recording these so the choice is visible rather than looking like an oversight.

| Considered | Licence | Why not |
|---|---|---|
| [gostevehoward/confseq](https://github.com/gostevehoward/confseq) | MIT | Reference implementation by one of the papers' authors. Not linked: this repository needs one bounded-mean lower bound, not a library, and implementing it from the papers keeps the dependency list where it is. |
| [jakorostami/expectation](https://github.com/jakorostami/expectation) | **GPL-3.0** | Copyleft. Linking it would impose GPL on this repository. The confidence-sequence method is implemented from the papers instead. |
| [sebastienrousseau/bankstatementparser](https://github.com/sebastienrousseau/bankstatementparser) | **NOASSERTION** | GitHub cannot identify a standard licence, so the terms of reuse are unclear. |

## Data and standards

| What | Source |
|---|---|
| Fee, GST and SLA constants | Razorpay's published pricing and settlement documentation |
| API object shapes (`entity`, `utr`, `fee`/`tax`/`captured`) | Razorpay API reference |
| Tally XML export format | Tally's published sample documents. No TallyPrime licence was available to verify against a live install. |
| 123 real bank transaction descriptions, used to test `_name_similarity` | Bank Transactions Dataset, Mendeley Data, [DOI 10.17632/dnxtg6n4rv.1](https://doi.org/10.17632/dnxtg6n4rv.1). **CC BY 4.0** — extract redistributed under the same licence in `backend/data/external/real_bank_descriptions.json`, with attribution retained in the file. |
| Settlement narration format `NEFT CR: [bank] [UTR] RAZORPAY SETTLEMENT` | Razorpay settlements documentation |

Every transaction, amount and bank narration used in any published result is synthetic and generated
by `app/data_gen/`. No real merchant data is present in any of them.

The one exception is the extract above, and it is not merchant data: 123 anonymised retail
descriptions carrying no account number, name, amount or date. It is used only to test name
similarity against text a bank wrote rather than text I wrote, and no result in
[RESULTS.md](RESULTS.md) is scored on it. Its licence requires attribution, which is why it has a row
here and a header inside the file itself.
