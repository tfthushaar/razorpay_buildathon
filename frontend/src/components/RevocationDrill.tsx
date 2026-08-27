import { useState } from "react";
import { runRevocationDrill } from "../api";
import type { RevocationDrillReport } from "../types";
import { categoryLabel, rupees } from "../formatters";

export function RevocationDrill() {
  const [category, setCategory] = useState("netting_trap");
  const [report, setReport] = useState<RevocationDrillReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await runRevocationDrill(category));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not run the drill.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="panel" id="revocation-drill-panel">
      <h2>Time-to-revocation drill</h2>
      <p className="panel-sub">
        Not a live number — a controlled experiment against an isolated, throwaway history (never the real accumulated
        calibration data above): seed a category into auto-resolve with clean decisions, then feed it deliberately
        wrong ones one at a time, and record exactly how many decisions — and how many real rupees — pass before the
        system revokes autonomy on its own, using the same <code>detect_drift()</code> machinery the live dial above
        already runs.
      </p>
      <div className="run-controls-grid">
        <label>
          Category to qualify then break
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="netting_trap">{categoryLabel("netting_trap")}</option>
            <option value="duplicate_refund">{categoryLabel("duplicate_refund")}</option>
          </select>
        </label>
        <button type="button" disabled={loading} onClick={run}>
          {loading ? "Running…" : "Run drill"}
        </button>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {report && (
        <div className={report.revoked ? "escalation-resolved was-wrong" : "escalation-resolved was-correct"}>
          {report.revoked ? (
            <p>
              <strong>
                {report.decisions_survived} decision{report.decisions_survived === 1 ? "" : "s"}
              </strong>{" "}
              (<strong>{rupees(report.amount_survived ?? 0)}</strong> in flight) passed before autonomy was revoked for{" "}
              {categoryLabel(report.category)} — after {report.qualifying_decision_count} clean decisions earned it in the first
              place.
              <br />
              <span className="panel-sub">{report.revocation_reason}</span>
            </p>
          ) : (
            <p>
              Not revoked. <span className="panel-sub">{report.revocation_reason}</span>
            </p>
          )}
        </div>
      )}
    </section>
  );
}
