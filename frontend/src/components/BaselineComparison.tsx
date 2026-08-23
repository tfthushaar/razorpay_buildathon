import type { BatchRunResult } from "../types";
import { pct } from "../formatters";

export function BaselineComparison({ result }: { result: BatchRunResult }) {
  const ourResolvedRate = (result.total_transactions - result.escalated_count) / result.total_transactions;
  const baselineRate = result.baseline_clean_count / result.total_transactions;
  const lift = ourResolvedRate - baselineRate;

  return (
    <section className="panel">
      <h2>Baseline comparison</h2>
      <p className="pitch-line">
        This system resolved <strong>{pct(ourResolvedRate)}</strong> of the batch vs.{" "}
        <strong>{pct(baselineRate)}</strong> for a naive exact-match reconciler — a{" "}
        <strong>{lift >= 0 ? "+" : ""}{pct(lift)}</strong> lift, with{" "}
        <strong>{result.stress.wrongly_auto_resolved}</strong> adversarial cases wrongly auto-resolved.
      </p>
      <div className="bar-compare">
        <div className="bar-row">
          <span>This system</span>
          <div className="bar-track">
            <div className="bar-fill bar-fill-good" style={{ width: `${ourResolvedRate * 100}%` }} />
          </div>
          <span>{pct(ourResolvedRate)}</span>
        </div>
        <div className="bar-row">
          <span>Naive baseline</span>
          <div className="bar-track">
            <div className="bar-fill bar-fill-neutral" style={{ width: `${baselineRate * 100}%` }} />
          </div>
          <span>{pct(baselineRate)}</span>
        </div>
      </div>
      <ul className="blindspot-list">
        <li>
          <strong>{result.baseline_false_negative_timing_lag}</strong> transactions the naive baseline silently called
          "clean" were actually settled well outside the normal SLA window for their rail — a false negative it has no
          way to catch, since it never looks at dates.
        </li>
        <li>
          <strong>{result.baseline_false_positive_rounding}</strong> transactions the naive baseline flagged as
          mismatches were actually harmless FX rounding drift — a false positive from having zero tolerance.
        </li>
      </ul>
    </section>
  );
}
