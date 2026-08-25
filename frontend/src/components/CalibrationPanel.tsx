import { useEffect, useRef, useState } from "react";
import { getCalibration } from "../api";
import type { CalibrationReport } from "../types";
import { categoryLabel, pct, rupees } from "../formatters";

interface Props {
  initialReport: CalibrationReport;
  refreshKey: number; // bump this after a run or a resolved escalation to force a refetch
  onReportChange?: (report: CalibrationReport) => void; // lets the escalation queue below react to the live dial too
}

export function CalibrationPanel({ initialReport, refreshKey, onReportChange }: Props) {
  const [threshold, setThreshold] = useState(initialReport.threshold);
  const [report, setReport] = useState(initialReport);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const applyReport = (r: CalibrationReport) => {
    setReport(r);
    setFetchError(null);
    onReportChange?.(r);
  };
  // a failed background refresh used to just log to the console and leave the table showing
  // stale data with no indication anything went wrong -- caught by an external audit 2026-08-24.
  // The table itself keeps showing the last-known-good report (never blanks on a transient
  // failure); this just makes the failure visible instead of silent.
  const handleFetchError = (e: unknown) => {
    console.error(e);
    setFetchError(e instanceof Error ? e.message : "Could not refresh calibration data.");
  };

  // the live dial: dragging the slider re-fetches a cheap re-aggregation over the accumulated
  // history (GET /api/calibration), never re-running the batch pipeline (spec §6.5).
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      getCalibration(threshold).then(applyReport).catch(handleFetchError);
    }, 120);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threshold]);

  useEffect(() => {
    getCalibration(threshold).then(applyReport).catch(handleFetchError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const totalAtRisk = report.categories.reduce((sum, c) => sum + c.amount_at_risk, 0);

  return (
    <section className="panel" id="calibration-panel">
      <h2>Calibration — live threshold dial</h2>
      <p className="panel-sub">
        Threshold is checked against each category's 95% Wilson confidence interval <em>lower bound</em>, not the raw
        accuracy — drag it and watch decisions and ₹-at-risk change instantly, without re-running anything.
      </p>
      <div className="chart-legend">
        <span className="chart-legend-item">
          <span className="chart-legend-swatch" style={{ background: "var(--accent)", opacity: 0.55 }} />
          95% confidence interval
        </span>
        <span className="chart-legend-item">
          <span className="chart-legend-swatch" style={{ background: "var(--ink)", width: 2, height: 12, borderRadius: 0 }} />
          current threshold
        </span>
        <span className="chart-legend-item">
          <span className="chart-legend-swatch" style={{ background: "var(--good)" }} />
          auto-resolve
        </span>
        <span className="chart-legend-item">
          <span className="chart-legend-swatch" style={{ background: "var(--warn)" }} />
          escalate
        </span>
      </div>
      {fetchError && (
        <p className="error-text" role="alert">
          {fetchError} — showing the last successfully loaded data.
        </p>
      )}
      <label className="threshold-slider">
        Auto-resolve threshold: <strong>{pct(threshold, 0)}</strong>
        <input
          type="range"
          min={0.5}
          max={0.99}
          step={0.01}
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
        />
      </label>

      <table className="calibration-table">
        <thead>
          <tr>
            <th>Category</th>
            <th>N</th>
            <th>Accuracy</th>
            <th>95% CI</th>
            <th>Decision</th>
            <th>₹ at risk</th>
          </tr>
        </thead>
        <tbody>
          {report.categories.map((c) => (
            <tr key={c.category} className={c.decision === "auto_resolve" ? "row-auto-resolve" : "row-escalate"}>
              <td>{categoryLabel(c.category)}</td>
              <td>
                {c.n}
                {c.n !== c.distinct_transaction_count && (
                  <span
                    className="mock-n-note"
                    title="Some real-provider decisions are the same case re-scored across multiple runs, not a new independent observation — auto-resolve requires enough DISTINCT cases, not just enough decisions."
                  >
                    {" "}
                    ({c.distinct_transaction_count} distinct)
                  </span>
                )}
                {c.mock_n > 0 && <span className="mock-n-note"> (+{c.mock_n} mock, not counted)</span>}
              </td>
              <td>
                {pct(c.accuracy)}
                {c.drift_alert && (
                  <span
                    className="mock-n-note drift-alert-note"
                    title={`Recent-decision accuracy (EWMA ${pct(c.ewma_accuracy)}) has fallen below its statistical control limit even though the all-time aggregate still looks fine — this category may be regressing right now, so it's escalating regardless of the CI.`}
                  >
                    {" "}
                    ⚠ recent EWMA {pct(c.ewma_accuracy)}
                  </span>
                )}
              </td>
              <td>
                <div className="ci-bar-track" title={c.reason}>
                  <div className="ci-bar-fill" style={{ left: `${c.ci_lower * 100}%`, width: `${(c.ci_upper - c.ci_lower) * 100}%` }} />
                  <div className="ci-threshold-line" style={{ left: `${threshold * 100}%` }} />
                </div>
                <span className="ci-text">
                  [{pct(c.ci_lower)}, {pct(c.ci_upper)}]
                </span>
              </td>
              <td>
                <span className={`badge ${c.decision === "auto_resolve" ? "badge-good" : "badge-warn"}`}>
                  {c.decision === "auto_resolve" ? "Auto-resolve" : "Escalate"}
                </span>
              </td>
              <td>{rupees(c.amount_at_risk)}</td>
            </tr>
          ))}
          {report.categories.length === 0 && (
            <tr>
              <td colSpan={6} className="empty-row">
                No narrator-classified decisions yet — run a batch first.
              </td>
            </tr>
          )}
        </tbody>
        {report.categories.length > 0 && (
          <tfoot>
            <tr>
              <td colSpan={5}>Total ₹ at risk at this threshold</td>
              <td>{rupees(totalAtRisk)}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </section>
  );
}
