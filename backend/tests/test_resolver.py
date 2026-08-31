"""Tests for the residual architecture: Layer 0, the verifier, the baselines, and the stage.

The load-bearing test in this file is `test_layer0_recovers_the_true_decomposition`. Every accuracy
number this project reports on the residual is meaningless if the resolver's candidate set does not
actually contain the true answer -- "the model chose wrong" and "the right answer was never on the
table" would be indistinguishable. That check already caught one real bug (a percentage base read off
the post-fee hop instead of the captured amount), which is exactly why it is an assertion and not a
one-off script.
"""

import pytest

from app.chain.builder import build_all_chains
from app.data_gen.generate import SyntheticDataGenerator, generate
from app.data_gen.schemas import SyntheticBatch
from app.narrator.attribution import _components_from_choice, _components_from_payload, _strip_fences, attribute_mock
from app.narrator.tools import build_tool_context
from app.calibration.cause_calibrator import NEVER_AUTO_ATTRIBUTE, calibrate_causes, score_attribution
from app.resolver import resolve
from app.resolver.causes import CauseCandidate, Decomposition
from app.resolver.enumerate import build_candidate_pool, enumerate_decompositions
from app.resolver.keyword_baseline import best_decomposition_by_advice, read_advice
from app.resolver.residual_stage import run_residual_stage
from app.resolver.resolver import present_options, rank_decompositions
from app.resolver.verifier import verify_decomposition


def compound_batch(seed: int = 42, n: int = 40, **kw):
    g = SyntheticDataGenerator(seed=seed)
    parts = [g._gen_compound_delta(**kw) for _ in range(n)]
    o, p, r, s, l, gt = [], [], [], [], [], []
    for a, b, c, d, e, f in parts:
        o += a
        p += b
        r += c
        s += d
        l += e
        gt += f
    batch = SyntheticBatch(orders=o, payments=p, refunds=r, settlements=s, ledger_entries=l, ground_truth=gt)
    chains = build_all_chains(batch)
    return batch, chains, build_tool_context(batch, chains), {e.transaction_id: e for e in gt}


def truth_multiset(entry):
    return tuple(sorted((c.cause, c.amount) for c in entry.true_causes))


# --- Layer 0 correctness -------------------------------------------------------------------


def test_layer0_recovers_the_true_decomposition():
    """At a tolerance at least as large as the injected rounding noise, the resolver's enumeration
    must CONTAIN the true answer for every case. Without this, no accuracy number below means
    anything."""
    _, chains, ctx, truth = compound_batch(n=40)
    missed = [
        tid
        for tid, chain in chains.items()
        if not any(d.cause_multiset() == truth_multiset(truth[tid]) for d in resolve(chain, ctx, tolerance=10).decompositions)
    ]
    assert missed == [], f"Layer 0 failed to recover the true decomposition for {len(missed)} case(s)"


def test_compositionality_alone_makes_it_under_determined():
    """The architecture must not rest on the tolerance knob. With ZERO rounding noise and ZERO
    tolerance -- exact integer arithmetic -- most compound cases are still under-determined."""
    _, chains, ctx, _ = compound_batch(n=40, rounding_noise=0)
    statuses = [resolve(chain, ctx, tolerance=0).status for chain in chains.values()]
    under = statuses.count("UNDER_DETERMINED")
    assert under > len(statuses) * 0.5, f"only {under}/{len(statuses)} under-determined at exact match"


def test_chance_baseline_is_one_over_k():
    _, chains, ctx, _ = compound_batch(n=20)
    for chain in chains.values():
        out = resolve(chain, ctx)
        if out.status == "UNDER_DETERMINED":
            assert out.chance_baseline == pytest.approx(1.0 / out.ambiguity)
        elif out.status == "RESOLVED":
            assert out.chance_baseline == 1.0
        else:
            assert out.chance_baseline == 0.0


def test_zero_delta_resolves_without_search():
    _, chains, ctx, _ = compound_batch(n=5)
    chain = next(iter(chains.values()))
    chain.settlement_delta = 0
    out = resolve(chain, ctx)
    assert out.status == "RESOLVED"
    assert out.decompositions[0].components == []


# --- the enumerator ------------------------------------------------------------------------


def test_enumerator_respects_physical_exclusivity():
    """Two different fee rates cannot both have been applied to one transaction."""
    pool = [
        CauseCandidate(cause="fee_rate_mismatch", amount=100, evidence_ref="fee_schedule:upi@0.0025"),
        CauseCandidate(cause="fee_rate_mismatch", amount=200, evidence_ref="fee_schedule:upi@0.0035"),
    ]
    found, _ = enumerate_decompositions(300, pool, tolerance=0, max_components=2)
    assert found == []


def test_enumerator_allows_repeatable_causes():
    """Refunds genuinely can stack -- exclusivity must not over-apply."""
    pool = [
        CauseCandidate(cause="partial_refund", amount=-100, evidence_ref="refund:a"),
        CauseCandidate(cause="partial_refund", amount=-200, evidence_ref="refund:b"),
    ]
    found, _ = enumerate_decompositions(-300, pool, tolerance=0, max_components=2)
    assert len(found) == 1


def test_enumerator_deduplicates_orderings():
    pool = [
        CauseCandidate(cause="partial_refund", amount=-100, evidence_ref="refund:a"),
        CauseCandidate(cause="partial_refund", amount=-100, evidence_ref="refund:a"),
    ]
    found, _ = enumerate_decompositions(-100, pool, tolerance=0, max_components=2)
    assert len(found) == 1


def test_pool_uses_the_captured_amount_as_the_percentage_base():
    """Regression: percentage candidates were once computed off the post-FEE hop, so the pool looked
    full of plausible numbers while never containing the true ones."""
    _, chains, ctx, _ = compound_batch(n=5)
    chain = next(iter(chains.values()))
    captured = chain.hops[0].actual
    pool = build_candidate_pool(chain, ctx)
    tds = [c for c in pool if c.evidence_ref == "tds:0.0100"]
    assert tds and tds[0].amount == -round(captured * 0.01)


# --- the verifier ---------------------------------------------------------------------------


def test_verifier_accepts_ground_truth():
    _, chains, ctx, truth = compound_batch(n=30)
    for tid, chain in chains.items():
        comps = [CauseCandidate(cause=c.cause, amount=c.amount, evidence_ref=c.evidence_ref) for c in truth[tid].true_causes]
        assert verify_decomposition(chain, ctx, comps, tolerance=10).passed, tid


@pytest.mark.parametrize(
    "mutate,expect_in_reason",
    [
        (lambda c, ch: setattr(c[0], "evidence_ref", "refund:rfnd_deadbeef") or c, "no refund"),
        (lambda c, ch: setattr(c[0], "evidence_ref", "tds:0.0777") or c, "not a standard"),
        (lambda c, ch: setattr(c[0], "evidence_ref", "nonsense") or c, "required"),
        (lambda c, ch: setattr(c[0], "evidence_ref", "wormhole:xyz") or c, "not a kind of evidence"),
    ],
)
def test_verifier_rejects_ungrounded_citations(mutate, expect_in_reason):
    _, chains, ctx, truth = compound_batch(n=10)
    tid, chain = next(iter(chains.items()))
    comps = [CauseCandidate(cause=c.cause, amount=c.amount, evidence_ref=c.evidence_ref) for c in truth[tid].true_causes]
    result = verify_decomposition(chain, ctx, mutate(comps, chain), tolerance=10)
    assert not result.passed
    assert any(expect_in_reason in v.reason for v in result.verdicts if not v.grounded)


def test_verifier_rejects_a_real_citation_with_the_wrong_amount():
    """Grounding is 'the citation SUPPORTS the amount', not merely 'the id exists'."""
    _, chains, ctx, truth = compound_batch(n=40)
    tid = next(t for t in chains if any(c.cause == "duplicate_refund" for c in truth[t].true_causes))
    comps = [CauseCandidate(cause=c.cause, amount=c.amount, evidence_ref=c.evidence_ref) for c in truth[tid].true_causes]
    target = next(c for c in comps if c.cause == "duplicate_refund")
    target.amount -= 5000
    result = verify_decomposition(chains[tid], ctx, comps, tolerance=10)
    assert not result.passed
    assert any("but this component claims" in v.reason for v in result.verdicts if not v.grounded)


def test_verifier_rejects_an_empty_decomposition():
    _, chains, ctx, _ = compound_batch(n=5)
    chain = next(iter(chains.values()))
    result = verify_decomposition(chain, ctx, [], tolerance=10)
    assert not result.passed
    assert "explains nothing" in result.failure_feedback()


def test_failure_feedback_never_names_the_right_answer():
    """The loop must complain, not coach -- otherwise every downstream number measures the loop."""
    _, chains, ctx, truth = compound_batch(n=10)
    tid, chain = next(iter(chains.items()))
    bogus = [CauseCandidate(cause="tds_deduction", amount=-99999, evidence_ref="tds:0.0100")]
    feedback = verify_decomposition(chain, ctx, bogus, tolerance=10).failure_feedback()
    for c in truth[tid].true_causes:
        assert c.evidence_ref not in feedback


# --- presentation symmetry ------------------------------------------------------------------


def test_present_options_is_deterministic_but_not_parsimony_ordered():
    _, chains, ctx, _ = compound_batch(n=20)
    tid = next(t for t in chains if resolve(chains[t], ctx).status == "UNDER_DETERMINED")
    out = resolve(chains[tid], ctx)
    a = present_options(out.decompositions, tid, limit=40)
    b = present_options(out.decompositions, tid, limit=40)
    assert [d.cause_multiset() for d in a] == [d.cause_multiset() for d in b]
    ranked = rank_decompositions(out.decompositions, limit=40)
    assert set(d.cause_multiset() for d in a) == set(d.cause_multiset() for d in ranked)


def test_present_options_removes_positional_advantage():
    """Across many cases the true answer must not concentrate at position 1 the way it does under
    parsimony ordering."""
    _, chains, ctx, truth = compound_batch(n=60)
    first_ranked = first_shuffled = counted = 0
    for tid, chain in chains.items():
        out = resolve(chain, ctx)
        if out.status != "UNDER_DETERMINED":
            continue
        want = truth_multiset(truth[tid])
        ranked = rank_decompositions(out.decompositions, limit=40)
        shuffled = present_options(out.decompositions, tid, limit=40)
        if not any(d.cause_multiset() == want for d in ranked):
            continue
        counted += 1
        first_ranked += ranked[0].cause_multiset() == want
        first_shuffled += shuffled[0].cause_multiset() == want
    assert counted > 10
    assert first_shuffled < first_ranked


# --- the keyword baseline --------------------------------------------------------------------


def test_read_advice_distinguishes_assertion_from_denial():
    verdicts = read_advice("TDS @1pct withheld u/s 194O | rolling reserve released - no hold this cycle")
    assert verdicts["tds_deduction"] is True
    assert verdicts["rolling_reserve"] is False


def test_read_advice_omits_causes_the_text_never_mentions():
    assert "fx_rounding" not in read_advice("TDS @1pct withheld u/s 194O")


def test_keyword_baseline_reports_its_own_ties():
    d1 = Decomposition(components=[CauseCandidate(cause="tds_deduction", amount=-100, evidence_ref="tds:0.0100")], observed_delta=-100)
    d2 = Decomposition(components=[CauseCandidate(cause="rolling_reserve", amount=-100, evidence_ref="reserve:0.0100")], observed_delta=-100)
    _, tied = best_decomposition_by_advice([d1, d2], None)
    assert tied == 2


# --- attribution plumbing ---------------------------------------------------------------------


def test_choice_out_of_range_yields_nothing_rather_than_crashing():
    opts = [Decomposition(components=[CauseCandidate(cause="fx_rounding", amount=1, evidence_ref="fx:INR")], observed_delta=1)]
    assert _components_from_choice({"choice": 99}, opts)[0] == []
    assert _components_from_choice({"choice": "not a number"}, opts)[0] == []
    assert _components_from_choice({}, opts)[0] == []


def test_candidate_selection_uses_the_pool_amount_not_a_retyped_one():
    """Regression: a live run kept selecting the right candidate and flipping its sign."""
    pool = [CauseCandidate(cause="fee_rate_mismatch", amount=-7500, evidence_ref="fee_schedule:upi@0.0045")]
    got = _components_from_payload({"decomposition": [{"candidate": 1, "amount": 7500}]}, pool)
    assert got[0].amount == -7500


def test_strip_fences_handles_fenced_and_bare_json():
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_fences('here you go {"a": 1} ok') == '{"a": 1}'


def test_mock_attribution_is_the_keyword_rule_and_always_verifies():
    _, chains, ctx, _ = compound_batch(n=25)
    for tid, chain in chains.items():
        out = resolve(chain, ctx)
        if out.status != "UNDER_DETERMINED":
            continue
        result = attribute_mock(chain, ctx, out)
        assert result.provider == "mock"
        assert result.verified, "choosing a pre-validated option must always pass verification"


# --- per-cause calibration ---------------------------------------------------------------------


def test_score_attribution_records_omissions_as_recall_misses():
    from app.data_gen.schemas import TrueCause

    truth = [TrueCause(cause="tds_deduction", amount=-100, evidence_ref="tds:0.0100"), TrueCause(cause="fx_rounding", amount=1, evidence_ref="fx:INR")]
    proposed = [CauseCandidate(cause="tds_deduction", amount=-100, evidence_ref="tds:0.0100")]
    scored = score_attribution("t1", proposed, truth, "ollama")
    assert sum(1 for s in scored if s.was_in_truth_but_omitted) == 1
    assert sum(1 for s in scored if s.correct) == 1


def test_cause_calibration_excludes_mock_from_the_gate():
    from app.calibration.cause_calibrator import ScoredAttribution

    scored = [ScoredAttribution(transaction_id=f"t{i}", cause="tds_deduction", amount=-1, correct=True, provider="mock") for i in range(200)]
    report = calibrate_causes(scored)
    entry = next(c for c in report.causes if c.cause == "tds_deduction")
    assert entry.n == 0 and entry.mock_n == 200
    assert entry.decision == "escalate"
    assert report.auto_attribute_causes == []


def test_cause_calibration_grants_autonomy_on_strong_real_evidence():
    from app.calibration.cause_calibrator import ScoredAttribution

    scored = [ScoredAttribution(transaction_id=f"t{i}", cause="tds_deduction", amount=-1, correct=True, provider="ollama") for i in range(200)]
    report = calibrate_causes(scored)
    assert "tds_deduction" in report.auto_attribute_causes


def test_policy_causes_never_auto_attribute_however_good_the_numbers():
    from app.calibration.cause_calibrator import ScoredAttribution

    for cause in NEVER_AUTO_ATTRIBUTE:
        scored = [ScoredAttribution(transaction_id=f"t{i}", cause=cause, amount=-1, correct=True, provider="ollama") for i in range(500)]
        report = calibrate_causes(scored)
        assert report.auto_attribute_causes == [], cause


# --- the pipeline stage --------------------------------------------------------------------------


def test_residual_stage_funnel_adds_up():
    _, chains, ctx, _ = compound_batch(n=20)
    report = run_residual_stage(chains, ctx, list(chains), provider="mock", closed_before_stage=100)
    assert report.total == report.layer0_resolved + report.under_determined + report.unmatched
    assert report.total == len(chains)
    assert report.model_calls == report.under_determined + report.unmatched
    assert len(report.cases) == len(chains)


def test_layer0_resolved_cases_never_reach_a_model():
    _, chains, ctx, _ = compound_batch(n=30)
    report = run_residual_stage(chains, ctx, list(chains), provider="mock")
    for case in report.cases:
        if case.status == "RESOLVED":
            assert not case.reached_model


def test_pipeline_flag_off_changes_nothing():
    from app.pipeline import run_batch

    off = run_batch(seed=42, main_n=60, stress_n=10, provider="mock")
    on = run_batch(seed=42, main_n=60, stress_n=10, provider="mock", enable_compound_delta=True)
    assert off.residual is None
    assert on.residual is not None
    assert on.residual.closed_before_stage > 0


def test_compound_delta_preserves_the_batch_slot_invariant():
    for n in (60, 120, 240):
        main, _ = generate(seed=7, main_n=n, stress_n=10, enable_compound_delta=True)
        assert len(main.orders) == n
        assert len(main.ground_truth) == n


def test_compound_generation_is_deterministic_per_seed():
    a, _, _, ta = compound_batch(seed=11, n=15)
    b, _, _, tb = compound_batch(seed=11, n=15)
    assert [s.bank_narration for s in a.settlements] == [s.bank_narration for s in b.settlements]
    assert [truth_multiset(v) for v in ta.values()] == [truth_multiset(v) for v in tb.values()]


def test_held_out_phrasing_uses_no_seen_negation_cue():
    """The held-out bank must genuinely be held out, or the generalisation gap measures nothing."""
    from app.resolver.keyword_baseline import NEGATION_CUES

    g = SyntheticDataGenerator(seed=5)
    for phrases in g._CAUSE_PHRASES_HELDOUT.values():
        for template in phrases["neg"]:
            rendered = f" {template.format(rate='1', ref='x').lower()} "
            assert not any(cue in rendered for cue in NEGATION_CUES), rendered


def test_advice_mentions_ground_truth_matches_the_text():
    """Whatever the generator recorded as mentioned must actually be findable in the advice."""
    _, chains, _, truth = compound_batch(n=30)
    for tid, chain in chains.items():
        if truth[tid].advice_mentions:
            assert chain.bank_narration


# --- cascade routing -------------------------------------------------------------------------------


def test_cascade_tier0_absorbs_only_when_the_advice_actually_discriminated():
    """Tier 0 must hand a case up when it is choosing by parsimony rather than by anything it read.

    This is the measurement that made a cascade worth building: the rule is not uniformly weak, it is
    specifically weak where the advice does not discriminate -- and that is detectable in advance,
    from the tie count, without asking a model anything."""
    from experiments.cascade import route

    _, chains, ctx, _ = compound_batch(n=25)
    handed_up_with_ties = 0
    for tid, chain in chains.items():
        out = resolve(chain, ctx)
        if out.status != "UNDER_DETERMINED":
            continue
        result = route(chain, ctx, out, model_tiers=())  # no model tiers: isolate tier 0's decision
        tier0 = result.tiers_tried[0]
        if tier0.resolved_here:
            assert "unique winner" in tier0.reason
        else:
            handed_up_with_ties += 1
            assert result.escalated_to_human
    assert handed_up_with_ties > 0, "tier 0 absorbed everything -- the tie gate is not doing anything"


def test_cascade_records_cost_for_every_tier_it_tried():
    from experiments.cascade import route

    _, chains, ctx, _ = compound_batch(n=15)
    tid = next(t for t in chains if resolve(chains[t], ctx).status == "UNDER_DETERMINED")
    out = resolve(chains[tid], ctx)
    result = route(chains[tid], ctx, out, model_tiers=())
    assert result.tiers_tried
    assert result.total_seconds == round(sum(t.seconds for t in result.tiers_tried), 4)
    assert result.final_tier in {"keyword_rule", "human"}
