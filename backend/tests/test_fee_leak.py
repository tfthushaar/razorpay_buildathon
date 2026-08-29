"""Tests for the fee leak detector (app/feeleak/detector.py).

Two things need proving: (1) it actually catches the two synthetic leak patterns with the correct
pattern classification and the correct rupee amounts, computed by hand, not just "found something";
and (2) -- the more important property -- it produces ZERO false positives against every existing
category the main generator produces (clean_match through genuine_error). A fee-leak detector that
flags ordinary, correctly-charged transactions as leaks would be worse than useless: it would
directly undermine the honest-numbers discipline the rest of this project holds itself to.
"""

from app.data_gen.fee_schedule import FEE_PCT, GST_RATE
from app.data_gen.generate import generate, generate_fee_leak_batch
from app.feeleak.detector import run_fee_leak_detection


def test_blended_rate_overcharge_is_caught_with_the_correct_amount():
    batch = generate_fee_leak_batch(seed=1, n=20)
    report = run_fee_leak_detection(batch.orders, batch.payments)
    blended = [f for f in report.findings if f.pattern == "blended_rate_overcharge"]
    assert blended, "should have found at least one blended-rate overcharge in 20 fee-leak examples"

    payments_by_order = {p.order_id: p for p in batch.payments}
    orders_by_id = {o.order_id: o for o in batch.orders}
    for f in blended:
        order = orders_by_id[f.transaction_id]
        payment = payments_by_order[f.transaction_id]
        expected_contracted_fee = round(order.amount * FEE_PCT[order.rail])
        assert f.contracted_fee == expected_contracted_fee
        assert f.actual_fee == payment.fee_amount
        assert f.fee_variance == payment.fee_amount - expected_contracted_fee
        assert f.fee_variance > 0, "this pattern is specifically an overcharge"
        assert f.total_impact == f.fee_variance + f.gst_variance


def test_gst_wrong_base_is_caught_with_the_correct_amount():
    batch = generate_fee_leak_batch(seed=1, n=20)
    report = run_fee_leak_detection(batch.orders, batch.payments)
    gst_findings = [f for f in report.findings if f.pattern == "gst_wrong_base"]
    assert gst_findings, "should have found at least one GST-wrong-base finding in 20 fee-leak examples"

    payments_by_order = {p.order_id: p for p in batch.payments}
    for f in gst_findings:
        payment = payments_by_order[f.transaction_id]
        correct_gst = round(payment.fee_amount * GST_RATE)
        assert f.contracted_gst == correct_gst
        assert f.actual_gst == payment.tax_amount
        assert f.gst_variance == payment.tax_amount - correct_gst
        assert abs(f.fee_variance) <= 100, "the fee itself should be correctly contracted in this pattern"


def test_gst_wrong_rate_is_caught_with_the_correct_amount():
    batch = generate_fee_leak_batch(seed=1, n=21)
    report = run_fee_leak_detection(batch.orders, batch.payments)
    wrong_rate_findings = [f for f in report.findings if f.pattern == "gst_wrong_rate"]
    assert wrong_rate_findings, "should have found at least one gst_wrong_rate finding in 21 fee-leak examples"

    payments_by_order = {p.order_id: p for p in batch.payments}
    for f in wrong_rate_findings:
        payment = payments_by_order[f.transaction_id]
        correct_gst = round(payment.fee_amount * GST_RATE)
        assert f.contracted_gst == correct_gst
        assert f.actual_gst == payment.tax_amount
        assert f.gst_variance == payment.tax_amount - correct_gst
        assert abs(f.fee_variance) <= 100, "the fee itself should be correctly contracted in this pattern"
        assert f.rail == "card", "restricted to card -- see the generator's own docstring for why"


def test_every_fee_leak_example_produces_a_finding():
    """The generator's whole premise is that these transactions reconcile cleanly but ARE real
    leaks -- every single one it produces must be caught, not just most of them. Verified across a
    wider seed sweep, not just one lucky seed, given gst_wrong_rate's narrower (card-only) margin
    above the rounding-noise epsilon compared to the other two patterns."""
    for seed in range(1, 20):
        batch = generate_fee_leak_batch(seed=seed, n=30)
        report = run_fee_leak_detection(batch.orders, batch.payments)
        assert len(report.findings) == len(batch.orders), f"seed {seed}: {len(report.findings)}/{len(batch.orders)}"


def test_zero_false_positives_against_every_existing_category():
    """The critical property: none of the main/stress batch's ordinary, correctly-charged
    transactions (clean_match, timing_lag, fee_deduction, partial_refund, currency_rounding,
    duplicate_refund, netting_trap, genuine_error) should ever be flagged as a fee leak. Every one
    of them uses fee_and_tax(rail, amount) -- the same contracted-rate formula this detector
    checks against -- so a false positive here would mean the detector's own arithmetic doesn't
    actually match the contract it's supposed to be checking against."""
    # 51,000 transactions rather than the 260 this test used to scan. A false-positive rate is only
    # as strong as the sample it is measured over, and the detector is a pure arithmetic pass with no
    # per-transaction state, so scanning 200x more costs 0.06s of detection time. Every category the
    # generator produces is represented.
    main, stress = generate(seed=42, main_n=50_000, stress_n=1_000)
    scanned = 0
    for batch in (main, stress):
        report = run_fee_leak_detection(batch.orders, batch.payments)
        scanned += len(batch.orders)
        assert report.findings == [], (
            f"false positive(s) on ordinary generated data: "
            f"{[(f.transaction_id, f.pattern, f.total_impact) for f in report.findings]}"
        )
    assert scanned == 51_000


def test_report_aggregates_are_computed_correctly():
    batch = generate_fee_leak_batch(seed=3, n=10)
    report = run_fee_leak_detection(batch.orders, batch.payments)
    assert report.total_fee_recovery == sum(f.fee_variance for f in report.findings if f.fee_variance > 0)
    assert report.total_gst_correction == sum(abs(f.gst_variance) for f in report.findings)
    assert sum(report.by_pattern.values()) == len(report.findings)


def test_findings_are_sorted_by_impact_descending():
    batch = generate_fee_leak_batch(seed=5, n=20)
    report = run_fee_leak_detection(batch.orders, batch.payments)
    impacts = [abs(f.total_impact) for f in report.findings]
    assert impacts == sorted(impacts, reverse=True)


def test_dispute_template_names_the_transaction_and_the_real_variance():
    batch = generate_fee_leak_batch(seed=9, n=4)
    report = run_fee_leak_detection(batch.orders, batch.payments)
    assert report.findings
    f = report.findings[0]
    assert f.transaction_id in f.dispute_template
    assert f"{f.total_impact / 100:,.2f}" in f.dispute_template
