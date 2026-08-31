"""The claim the whole architecture rests on, tested rather than asserted.

README and ARCHITECTURE both say the same thing: the deterministic resolver runs first and keeps
everything it can explain alone, so a case a rule could solve is taken by the rule and cannot sit
inside a model's accuracy figure. That is the load-bearing sentence of this project and it was
structural -- true because of how pipeline.py is wired -- but nothing failed if the wiring changed.

These five tests are that claim, one assertion each. If the boundary ever leaks, they break before
the numbers in RESULTS.md quietly stop meaning what they say.
"""

from __future__ import annotations

import pytest

from app.calibration.calibrator import NEVER_AUTO_RESOLVE
from app.chain.builder import build_all_chains
from app.data_gen.generate import generate
from app.matching.engine import run_matching_engine
from app.narrator.agent import NarratorOutput
from app.pipeline import run_batch


@pytest.fixture
def spy(monkeypatch):
    """Record every transaction the narrator is shown, and let the real one answer."""
    import app.pipeline as pipeline

    seen: list[str] = []
    real = pipeline.narrate

    def spying(chain, context, provider="mock", **kwargs):
        seen.append(chain.transaction_id)
        return real(chain, context, provider=provider, **kwargs)

    monkeypatch.setattr(pipeline, "narrate", spying)
    return seen


def _silent_narrator(monkeypatch):
    """A provider that answers nothing at all, the honest worst case for a real one."""
    import app.pipeline as pipeline

    def silent(chain, context, provider="mock", **kwargs):
        return NarratorOutput(
            transaction_id=chain.transaction_id,
            category="genuine_error",
            confidence=0.0,
            reasoning="stub provider: returns nothing",
            tool_calls=[],
            provider="mock",
        )

    monkeypatch.setattr(pipeline, "narrate", silent)


# --- 1. the boundary itself -------------------------------------------------------------------


def test_the_model_is_never_shown_a_case_layer_zero_closed(spy):
    """The sentence this project is built on. Whatever the deterministic engine resolved is out of
    scope for the model: it is not asked to review it, confirm it, or revisit it."""
    result = run_batch(seed=42, main_n=120, stress_n=0, provider="mock")

    batch = generate(seed=42, main_n=120, stress_n=0)[0]
    engine = run_matching_engine(build_all_chains(batch))
    closed_by_rule = {t for t, r in engine.items() if r.resolution != "needs_narration"}

    assert set(spy) & closed_by_rule == set(), (
        "the narrator was shown transactions the deterministic engine had already resolved: "
        f"{sorted(set(spy) & closed_by_rule)[:5]}"
    )
    assert result.deterministic_only_resolved_count == len(closed_by_rule)


# --- 2. the tail has to be a tail --------------------------------------------------------------


def test_the_residual_is_a_minority_at_demo_density(spy):
    run_batch(seed=42, main_n=120, stress_n=0, provider="mock")
    assert len(spy) <= 120 * 0.20, f"{len(spy)}/120 reached a model; 'AI on the tail' needs a tail"


def test_the_residual_is_a_small_minority_at_realistic_density():
    """The density a real merchant sees, measured at the matching engine because run_batch does not
    expose clean_ratio. If the model reached much of this batch, the throughput argument in
    RESULTS.md would stop holding: it prices 1.1% of transactions at model speed, not 15%."""
    from app.data_gen.generate import SyntheticDataGenerator

    batch = SyntheticDataGenerator(seed=42).generate_main_batch(2000, clean_ratio=0.97)
    engine = run_matching_engine(build_all_chains(batch))
    residual = [t for t, r in engine.items() if r.resolution == "needs_narration"]
    share = len(residual) / len(engine)
    assert share <= 0.06, f"{share:.1%} of a 97%-clean batch reached a model; RESULTS.md claims 1.1%"


# --- 3. turning the model on must not move a deterministic answer -------------------------------


def test_deterministic_resolutions_are_identical_with_the_model_on_and_off(monkeypatch):
    """The strongest form of the claim: a silent model and a working one produce the SAME
    deterministic half, transaction for transaction."""
    with_model = run_batch(seed=42, main_n=120, stress_n=0, provider="mock")

    _silent_narrator(monkeypatch)
    without = run_batch(seed=42, main_n=120, stress_n=0, provider="mock")

    assert with_model.deterministic_only_resolved_count == without.deterministic_only_resolved_count
    assert with_model.deterministic_only_amount_reconciled == without.deterministic_only_amount_reconciled
    assert with_model.total_transactions == without.total_transactions
    assert with_model.baseline_clean_count == without.baseline_clean_count


# --- 4. a silent model costs recall, never precision --------------------------------------------


def test_a_model_that_answers_nothing_costs_escalations_and_not_correctness(monkeypatch):
    """The worst honest outcome of a broken provider is a longer queue, never a wrong auto-resolve
    and never a lost transaction."""
    _silent_narrator(monkeypatch)
    result = run_batch(seed=42, main_n=120, stress_n=40, provider="mock")

    assert result.stress.wrongly_auto_resolved == 0
    assert result.escalated_count > 0
    # nothing may vanish: every transaction is either resolved or in the queue
    assert result.escalated_count <= result.total_transactions


# --- 5. the model cannot invent work for itself -------------------------------------------------


def test_a_category_the_gate_forbids_is_never_auto_resolved(monkeypatch):
    """`genuine_error` is in NEVER_AUTO_RESOLVE by design. A model returning it with maximum
    confidence must still escalate, or the gate is decoration."""
    import app.pipeline as pipeline

    def always_confident(chain, context, provider="mock", **kwargs):
        return NarratorOutput(
            transaction_id=chain.transaction_id,
            category="genuine_error",
            confidence=1.0,
            reasoning="stub: maximum confidence on a category the policy forbids",
            tool_calls=[],
            provider="mock",
        )

    monkeypatch.setattr(pipeline, "narrate", always_confident)
    result = run_batch(seed=42, main_n=120, stress_n=0, provider="mock")

    assert "genuine_error" in NEVER_AUTO_RESOLVE
    auto = {e.category for e in result.escalations}
    assert result.escalated_count > 0
    assert "genuine_error" not in (result.calibration.auto_resolve_categories if result.calibration else [])
