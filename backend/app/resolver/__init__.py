"""Layer 0: the deterministic resolver, and the residual it hands upward.

This package is the architectural answer to a pattern that repeated three times in this project:
every classification category built over structured financial data eventually collapsed to a rule
(`netting_trap` to a 20-line check, `multiway_netting_trap` to a hash table, `narration_explained` to
a keyword scan). That is not bad luck three times -- it is what structured financial data *is*.
Settlement records are produced by deterministic processes, so their ground truth is arithmetically
derivable, so for any classification task posed over them a rule exists that wins.

The fix is not a fourth category. It is to stop putting the model where a rule can stand.

So the relationship is inverted. The deterministic resolver runs FIRST and takes everything it can.
The model is handed only what is left, and "what is left" has exactly two shapes:

  UNDER_DETERMINED  the resolver found >= 2 arithmetically valid explanations and cannot choose
  UNMATCHED         the resolver found 0

Of those two, UNDER_DETERMINED is the load-bearing one, and it is worth being precise about why.
UNMATCHED is a weak claim -- a reader can always answer "your resolver just isn't good enough yet,"
and they might be right. UNDER_DETERMINED cannot be answered that way: the resolver did its job
perfectly and returned k answers that each sum to the observed delta within tolerance and each cite
real objects. Choosing among k arithmetically-correct answers is definitionally not an arithmetic
problem, and no better resolver changes that -- a *stronger* resolver finds MORE valid
decompositions, not fewer. See app/resolver/resolver.py.

This also means the honest baseline on the residual is computable rather than argued: on an
UNDER_DETERMINED case with k valid decompositions, blind choice scores exactly 1/k. Every accuracy
number this project reports on the residual is reported against that 1/k line, not against a
strawman. See docs/RESULTS.md.

Nothing built earlier is discarded by this -- check_batch_anomalies, the k-sum solvers, the fee
recomputation, the narration read all become candidate *generators* inside Layer 0 and run first, at
full deterministic speed. They were never the wrong code; they were in the wrong position.
"""

from app.resolver.causes import (
    CAUSE_TYPES,
    CauseCandidate,
    CauseType,
    Decomposition,
    decomposition_total,
)
from app.resolver.resolver import ResolverOutput, ResolverStatus, resolve

__all__ = [
    "CAUSE_TYPES",
    "CauseCandidate",
    "CauseType",
    "Decomposition",
    "ResolverOutput",
    "ResolverStatus",
    "decomposition_total",
    "resolve",
]
