"""Shared fee/SLA reference data.

Deliberately importable by both the data generator (to inject realistic deltas) and the
narrator's `lookup_fee_schedule` / `check_sla_window` tools later — the tools
must look up the *same* ground truth the generator used, not a hardcoded copy, so a passing
tool call is a real check and not theater.
"""

from typing import Literal

Rail = Literal["upi", "card", "netbanking"]

FEE_PCT: dict[Rail, float] = {
    "upi": 0.003,
    "card": 0.02,
    "netbanking": 0.01,
}

GST_RATE = 0.18

BASE_SLA_DAYS: dict[Rail, int] = {
    "upi": 1,
    "card": 2,
    "netbanking": 3,  # netbanking SLA varies transaction-to-transaction; this is the nominal baseline
}

NETBANKING_SLA_RANGE = (2, 5)

# Beyond this many days, a settlement is no longer "normal variance" for the rail — it's a real
# timing-lag exception. Shared by the generator (which must inject delays that clearly cross this
# line) and the matching engine (which uses the same line to decide what's still tolerable).
SLA_TOLERANCE_DAYS: dict[Rail, int] = {
    "upi": BASE_SLA_DAYS["upi"] + 1,
    "card": BASE_SLA_DAYS["card"] + 1,
    "netbanking": NETBANKING_SLA_RANGE[1],
}


def fee_and_tax(rail: Rail, amount: int) -> tuple[int, int]:
    """Fee and GST-on-fee for an amount in the smallest currency unit. Rounds to nearest unit."""
    fee = round(amount * FEE_PCT[rail])
    tax = round(fee * GST_RATE)
    return fee, tax
