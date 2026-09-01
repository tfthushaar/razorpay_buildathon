"""Our name matcher, tested against real bank text nobody in this project wrote.

Every other number here is measured against data this repository generates, which is the structural
limit LIMITATIONS opens with. This is the one test whose input came from outside: real, anonymised
UK retail bank transaction descriptions from a published CC BY 4.0 corpus.

    Bank Transactions Dataset, Mendeley Data, DOI 10.17632/dnxtg6n4rv.1, CC BY 4.0

WHAT IT DOES AND DOES NOT CLOSE. It tests one component, `_name_similarity`, against real renderings
of real merchant names -- the truncation and abbreviation a bank actually applies. It does NOT close
the reconciliation loop: the corpus is retail card and direct-debit activity, so it carries no UTRs,
no settlement references and no remittance advice. Only 62 of its 6,567 descriptions contain a
six-digit run at all. Claiming this validates three-source matching would be worse than having no
external data.

`data/external/real_bank_descriptions.json` holds a redistributable extract: descriptions only, no
amounts, dates or balances.
"""

import json
from pathlib import Path

import pytest

from app.resolver.entity_resolution import _name_similarity
from app.resolver.fellegi_sunter import NAME_CLOSE_THRESHOLD

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "external" / "real_bank_descriptions.json"

# Real brand, and the way this bank actually rendered it. The mangling is the point: truncation at a
# field boundary, slash abbreviation, appended store numbers, missing spaces.
REAL_RENDERINGS = [
    ("SAINSBURYS", "SAINSBURYS S/MKTS"),
    ("LIDL", "LIDL GB  NOTTINGHA"),
    ("TESCO", "TESCO STORE 3033"),
    ("POUNDLAND", "POUNDLAND LTD 1718"),
    ("CAPEWELL WINDOW CLEANING", "CAPEWELL WINDOW CL"),
    ("COOPERATIVE", "LNK COOPERATIVE PL"),
    ("AUDIBLE", "Audible UK"),
    ("TRADING212", "TRADING212UK"),
]


@pytest.fixture(scope="module")
def corpus():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["licence"] == "CC BY 4.0"
    return data


def test_the_matcher_recognises_real_bank_renderings():
    """8/8 at the shipped threshold, on names a bank mangled rather than names I mangled."""
    scored = [(brand, _name_similarity(brand, rendered)) for brand, rendered in REAL_RENDERINGS]
    failures = [(b, s) for b, s in scored if s < NAME_CLOSE_THRESHOLD]
    assert not failures, f"below the {NAME_CLOSE_THRESHOLD} threshold on real text: {failures}"


def test_it_does_not_match_unrelated_real_merchants(corpus):
    """The negative control, and the one that would catch a similarity function that says yes to
    everything. Scored against real descriptions rather than ones I invented to fail."""
    false_positives = [d for d in corpus["descriptions"] if _name_similarity("SAINSBURYS", d) >= NAME_CLOSE_THRESHOLD]
    assert false_positives == [] or all("SAINSBURY" in d.upper() for d in false_positives), false_positives


def test_the_corpus_is_real_and_attributed(corpus):
    assert corpus["doi"] == "10.17632/dnxtg6n4rv.1"
    assert corpus["n_in_source"] > 6000
    assert len(corpus["descriptions"]) >= 100


def test_the_corpus_cannot_validate_settlement_matching(corpus):
    """Asserting the limit, so nobody later mistakes this for evidence about three-source matching.

    A settlement narration carries a payout reference. This corpus is retail activity and does not.
    """
    import re

    with_reference = [d for d in corpus["descriptions"] if re.search(r"\d{6,}", d)]
    assert len(with_reference) / len(corpus["descriptions"]) < 0.05, (
        "this corpus now contains reference-bearing descriptions, so the limit stated in this "
        "module's docstring needs re-checking"
    )


def test_real_field_widths_are_narrower_than_ours(corpus):
    """A measured difference worth keeping visible rather than quietly matching.

    This bank truncates its retail feed at 18 characters. Razorpay's own documented settlement
    narration is about 45. Both are real, they disagree, and our generator follows the settlement
    one -- so this asserts the gap exists rather than pretending it does not.
    """
    assert corpus["field_width"]["max_observed"] <= 25
    assert corpus["field_width"]["rows_at_18_chars"] > 1000
