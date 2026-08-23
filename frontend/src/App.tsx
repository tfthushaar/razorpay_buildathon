import { useState } from "react";
import { runBatch } from "./api";
import type { RunRequest } from "./api";
import type { BatchRunResult, CalibrationReport } from "./types";
import { RunControls } from "./components/RunControls";
import { SummaryTiles } from "./components/SummaryTiles";
import { BaselineComparison } from "./components/BaselineComparison";
import { CalibrationPanel } from "./components/CalibrationPanel";
import { EscalationQueue } from "./components/EscalationQueue";
import { StressScorecard } from "./components/StressScorecard";
import { AuditLogView } from "./components/AuditLogView";
import { BreakItPanel } from "./components/BreakItPanel";
import "./App.css";

function App() {
  const [result, setResult] = useState<BatchRunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [liveCalibration, setLiveCalibration] = useState<CalibrationReport | null>(null);

  const liveAutoResolveCategories = new Set(
    (liveCalibration ?? result?.calibration)?.categories.filter((c) => c.decision === "auto_resolve").map((c) => c.category) ?? []
  );

  const handleRun = async (req: RunRequest) => {
    setLoading(true);
    setError(null);
    try {
      const run = await runBatch(req);
      setResult(run);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>Settlement Reconciliation Copilot</h1>
        <p className="app-subtitle">
          Causal-chain matching + calibrated autonomy — Razorpay AI Buildathon 2026, Track 04
        </p>
      </header>

      <RunControls onRun={handleRun} loading={loading} error={error} />
      <BreakItPanel />

      {result && (
        <>
          <SummaryTiles result={result} />
          <div className="two-column">
            <BaselineComparison result={result} />
            <StressScorecard stress={result.stress} />
          </div>
          <CalibrationPanel initialReport={result.calibration} refreshKey={refreshKey} onReportChange={setLiveCalibration} />
          <EscalationQueue
            escalations={result.escalations}
            onResolved={() => setRefreshKey((k) => k + 1)}
            liveAutoResolveCategories={liveAutoResolveCategories}
          />
          <AuditLogView runId={result.run_id} refreshKey={refreshKey} />
        </>
      )}

      {!result && !loading && (
        <p className="empty-state">Run a batch above to see match rate, calibration, and the exception queue.</p>
      )}
    </div>
  );
}

export default App;
