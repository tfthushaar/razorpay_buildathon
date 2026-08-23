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
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const applyReport = (r: CalibrationReport) => {
    setReport(r);
    onReportChange?.(r);
  };

  // the live dial: dragging the slider re-fetches a cheap re-aggregation over the accumulated
  // history (GET /api/calibration), never re-running the batch pipeline (spec §6.5).
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      getCalibration(threshold).then(applyReport).catch(console.error);
    }, 120);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threshold]);

  useEffect(() => {
    getCalibration(threshold).then(applyReport).catch(console.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const totalAtRisk = report.categories.reduce((sum, c) => sum + c.amount_at_risk, 0);

  return (
    <section className="panel">
      <h2>Calibration — live threshold dial</h2>
      <p className="panel-sub">
        Threshold is checked against each category's 95% Wilson confidence interval <em>lower bound</em>, not the raw
        accuracy — drag it and watch decisions and ₹-at-risk change instantly, without re-running anything.
      </p>
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
                {c.mock_n > 0 && <span className="mock-n-note"> (+{c.mock_n} mock, not counted)</span>}
              </td>
              <td>{pct(c.accuracy)}</td>
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
