import type { BatchRunResult } from "../types";
import { pct } from "../formatters";

export function BaselineComparison({ result }: { result: BatchRunResult }) {
  const ourResolvedRate = (result.total_transactions - result.escalated_count) / result.total_transactions;
  const deterministicOnlyRate = result.deterministic_only_resolved_count / result.total_transactions;
  const baselineRate = result.baseline_clean_count / result.total_transactions;
  const lift = ourResolvedRate - baselineRate;
  const narratorLift = ourResolvedRate - deterministicOnlyRate;

  return (
    <section className="panel">
      <h2>Baseline comparison</h2>
      <p className="pitch-line">
        This system resolved <strong>{pct(ourResolvedRate)}</strong> of the batch vs.{" "}
        <strong>{pct(baselineRate)}</strong> for a naive exact-match reconciler — a{" "}
        <strong>{lift >= 0 ? "+" : ""}{pct(lift)}</strong> lift, with{" "}
        <strong>{result.stress.wrongly_auto_resolved}</strong> adversarial cases wrongly auto-resolved.
      </p>
      <p className="pitch-line">
        Of that lift, <strong>{pct(deterministicOnlyRate)}</strong> comes from causal-chain matching alone — zero LLM
        calls, deterministic fee/refund/timing/rounding logic.{" "}
        {narratorLift > 0.001 ? (
          <>
            The agentic narrator adds a further <strong>+{pct(narratorLift)}</strong> on top, on exactly the cases the
            deterministic engine couldn't explain by itself.
          </>
        ) : (
          <>
            The agentic narrator hasn't auto-resolved anything <em>yet</em> in this run — every category still needs
            more accumulated evidence to clear the calibration threshold (see the dial below). That's the intended,
            conservative default, not a missing feature: resolve a few escalations or run more batches and watch this
            number become positive as trust is earned.
          </>
        )}
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
          <span>Deterministic only</span>
          <div className="bar-track">
            <div className="bar-fill bar-fill-deterministic" style={{ width: `${deterministicOnlyRate * 100}%` }} />
          </div>
          <span>{pct(deterministicOnlyRate)}</span>
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
