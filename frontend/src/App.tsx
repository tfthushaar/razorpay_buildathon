import { useEffect, useState } from "react";
import { getHealth, runBatch } from "./api";
import type { RunRequest } from "./api";
import type { BatchRunResult, CalibrationReport } from "./types";
import { RunControls } from "./components/RunControls";
import { SummaryTiles } from "./components/SummaryTiles";
import { BaselineComparison } from "./components/BaselineComparison";
import { CalibrationPanel } from "./components/CalibrationPanel";
import { EscalationQueue } from "./components/EscalationQueue";
import { StressScorecard } from "./components/StressScorecard";
import { FeeLeakAnalysis } from "./components/FeeLeakAnalysis";
import { ForecastPanel } from "./components/ForecastPanel";
import { ResidualPanel } from "./components/ResidualPanel";
import { ErpExport } from "./components/ErpExport";
import { AuditLogView } from "./components/AuditLogView";
import { SettlementQA } from "./components/SettlementQA";
import { RevocationDrill } from "./components/RevocationDrill";
import { BreakItPanel } from "./components/BreakItPanel";
import { GuidedTour } from "./components/GuidedTour";
import { HeroFindings } from "./components/HeroFindings";
import sampleRun from "./evidence/sample-run.json";
import "./App.css";

function App() {
  // Seeded with a committed sample run rather than null. Every panel used to be gated behind a live
  // result, so a cold backend or an unclicked page showed a header and a form. The sample is a real
  // run (seed 42, mock provider) committed to the repo, labelled as such until a live run replaces
  // it, so the page is fully legible before anyone clicks and with the backend down.
  const [result, setResult] = useState<BatchRunResult>(sampleRun as unknown as BatchRunResult);
  const [isSample, setIsSample] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [liveCalibration, setLiveCalibration] = useState<CalibrationReport | null>(null);
  const [backendUnreachable, setBackendUnreachable] = useState(false);
  const [resolveSignal, setResolveSignal] = useState(0);

  // Proactive health check on load, not just reactive failure on the first user action -- without
  // this, a stopped backend is invisible until someone clicks "Run batch" and gets a generic
  // error. Checking on load means the very first thing a user (or a judge, mid-demo) sees if the
  // backend isn't up is a clear "here's what's wrong and how to fix it" message, not a working-
  // looking dashboard that fails on the first real interaction.
  useEffect(() => {
    getHealth()
      .then(() => setBackendUnreachable(false))
      .catch(() => setBackendUnreachable(true));
  }, []);

  const liveAutoResolveCategories = new Set(
    (liveCalibration ?? result?.calibration)?.categories.filter((c) => c.decision === "auto_resolve").map((c) => c.category) ?? []
  );

  const handleRun = async (req: RunRequest) => {
    setLoading(true);
    setError(null);
    try {
      const run = await runBatch(req);
      setResult(run);
      setIsSample(false);
      setRefreshKey((k) => k + 1);
      setBackendUnreachable(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      // api.ts's request() throws this exact, specific message for a network-level failure (no
      // response at all) -- distinct from a handled server error, which has its own status/body
      // instead. Keeps the proactive banner in sync with live reality, not just the mount-time
      // check: the backend can go down mid-session, or come back up after the initial check found
      // it unreachable.
      setBackendUnreachable(message.startsWith("Could not reach the backend"));
    } finally {
      setLoading(false);
    }
  };

  const showStale = loading;

  return (
    <div className="app">
      {backendUnreachable && (
        <div className="backend-unreachable-banner" role="alert">
          Can't reach the backend. Start it with <code>cd backend && python -m uvicorn app.main:app --reload --port 8000</code>,
          then reload this page.
        </div>
      )}
      <header className="app-header">
        <div className="app-header-text">
          <span className="app-header-mark" aria-hidden="true">SR</span>
          <div>
            <h1>Settlement Reconciliation Copilot</h1>
            <p className="app-subtitle">
              Causal-chain matching + calibrated autonomy — Razorpay AI Buildathon 2026, Track 04
            </p>
          </div>
        </div>
      </header>

      <HeroFindings />

      <RunControls onRun={handleRun} loading={loading} error={error} />

      {(
        <div className="stale-wrap" data-stale={showStale ? "true" : "false"}>
          {showStale && (
            <div className="stale-banner">
              <span className="stale-banner-dot" aria-hidden="true" />
              Running a new batch — showing the previous run's results until it lands
            </div>
          )}
          {isSample && (
            <div className="sample-banner">
              Showing a committed sample run (seed 42, mock provider) so this page works with the backend cold. Click
              <strong> Run batch </strong> for a live one.
            </div>
          )}
          <SummaryTiles result={result} refreshKey={refreshKey} />

          {/* Weighted, not flat. The residual funnel is the architecture, the queue with its tool
              trace open is the work product, the drill is the safety property, the fee leak is the
              money. Everything below "Also built" is evidence of range and is closed by default, so
              it costs no scroll depth against the things that carry the argument. */}
          {result.residual && <ResidualPanel residual={result.residual} />}
          <EscalationQueue
            escalations={result.escalations}
            runId={result.run_id}
            onResolved={() => {
              setRefreshKey((k) => k + 1);
              setResolveSignal((s) => s + 1);
            }}
            liveAutoResolveCategories={liveAutoResolveCategories}
            categoryProposals={result.category_proposals}
          />
          <RevocationDrill />
          <FeeLeakAnalysis result={result} />
          <CalibrationPanel initialReport={result.calibration} refreshKey={refreshKey} onReportChange={setLiveCalibration} />

          <details className="also-built">
            <summary>Also built</summary>
            <p className="also-built-note">
              Range rather than argument: a naive-baseline comparison, an all-traps stress batch, a forward settlement
              forecaster, an ERP journal export, a free-text Q&amp;A agent over the batch, a hand-crafted scenario
              submitter, and the full audit trail.
            </p>
            <div className="two-column">
              <BaselineComparison result={result} />
              <StressScorecard stress={result.stress} />
            </div>
            <ForecastPanel refreshKey={refreshKey} />
            <ErpExport result={result} />
            <BreakItPanel />
            <SettlementQA key={refreshKey} />
            <AuditLogView runId={result.run_id} refreshKey={refreshKey} />
          </details>
        </div>
      )}

      <GuidedTour hasEscalations={result.escalations.length > 0} resolveSignal={resolveSignal} />
    </div>
  );
}

export default App;
