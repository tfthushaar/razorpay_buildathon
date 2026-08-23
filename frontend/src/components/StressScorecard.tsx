import type { StressScorecard as StressScorecardType } from "../types";

export function StressScorecard({ stress }: { stress: StressScorecardType }) {
  const clean = stress.wrongly_auto_resolved === 0;
  return (
    <section className={`panel scorecard ${clean ? "scorecard-clean" : "scorecard-dirty"}`}>
      <h2>Adversarial stress-test scorecard</h2>
      <p className="panel-sub">
        A separate batch that is <strong>100% traps</strong> — duplicate refunds, netting pairs, unexplained errors.
        Never blended into the main batch's accuracy. This is the "break it" number.
      </p>
      <div className="scorecard-headline">
        {stress.total - stress.wrongly_auto_resolved}/{stress.total}
        <span className="scorecard-headline-sub">handled correctly</span>
      </div>
      <div className="scorecard-big-stat">
        <span className={clean ? "stat-good" : "stat-bad"}>{stress.wrongly_auto_resolved}</span> wrongly auto-resolved
      </div>
      <p className="scorecard-detail">
        {stress.deterministic_correct} resolved deterministically (fee/timing/rounding), {stress.narrated_total} needed
        the agentic narrator, {stress.narrated_correctly_handled} of those were handled correctly (correctly
        auto-resolved or correctly escalated).
      </p>
    </section>
  );
}
