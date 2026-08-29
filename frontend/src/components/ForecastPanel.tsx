import { useEffect, useState } from "react";
import { checkPayrollCoverage, getForecastBacktest, getForecastBlindBacktest, getPendingForecast } from "../api";
import type { BacktestReport, PayrollCoverageResult, PendingForecastResponse } from "../types";
import { pct, rupees } from "../formatters";

interface Props {
  refreshKey: number; // bump after a run so the backtest re-targets the latest run's own batch
}

function formatDateShort(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

export function ForecastPanel({ refreshKey }: Props) {
  const [forecast, setForecast] = useState<PendingForecastResponse | null>(null);
  const [backtest, setBacktest] = useState<BacktestReport | null>(null);
  const [blindBacktest, setBlindBacktest] = useState<BacktestReport | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [outflowRupees, setOutflowRupees] = useState(2000);
  const [outflowDate, setOutflowDate] = useState("2026-01-22");
  const [payrollResult, setPayrollResult] = useState<PayrollCoverageResult | null>(null);
  const [payrollError, setPayrollError] = useState<string | null>(null);
  const [payrollLoading, setPayrollLoading] = useState(false);

  useEffect(() => {
    getPendingForecast(10)
      .then(setForecast)
      .catch((e) => setFetchError(e instanceof Error ? e.message : "Could not load the pending-settlement forecast."));
    // A backtest needs a run to already exist (it targets the latest run's own batch) -- a 404
    // here just means "no run yet", not a real failure, so it's swallowed rather than surfaced.
    getForecastBacktest()
      .then(setBacktest)
      .catch(() => setBacktest(null));
    // Self-contained -- doesn't need a prior run, so it loads once on mount, not per refreshKey.
    getForecastBlindBacktest()
      .then(setBlindBacktest)
      .catch(() => setBlindBacktest(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const handlePayrollCheck = async () => {
    setPayrollLoading(true);
    setPayrollError(null);
    try {
      const result = await checkPayrollCoverage(Math.round(outflowRupees * 100), outflowDate, 10);
      setPayrollResult(result);
    } catch (e) {
      setPayrollError(e instanceof Error ? e.message : "Could not check payroll coverage.");
    } finally {
      setPayrollLoading(false);
    }
  };

  return (
    <section className="panel">
      <h2>Forward settlement forecast</h2>
      <p className="panel-sub">
        Predicted from the order + payment + fee/SLA schedule alone, before a settlement exists — genuinely in-flight
        money, not a lookup. The first backtest below scores this same predictor against the latest run's own real
        settlements, computed with the exact schedule the predictor reads. The second is scored against a separate
        batch whose real settlements were computed with a hidden schedule drift the predictor never sees — a genuine
        test of forecast robustness, not the same reference data compared to itself.
      </p>

      {fetchError && (
        <p className="error-text" role="alert">
          {fetchError}
        </p>
      )}

      {backtest && (
        <div className="fee-leak-summary">
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(backtest.exact_rate)}</span>
            <span className="fee-leak-summary-label">of {backtest.n} predicted to the exact paise</span>
          </div>
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(backtest.median_ape)}</span>
            <span className="fee-leak-summary-label">median error on net amount</span>
          </div>
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(backtest.mape)}</span>
            <span className="fee-leak-summary-label">
              mean error — carried by the tail, not the middle: p95 is {pct(backtest.p95_ape)} and the worst single case is{" "}
              {pct(backtest.worst_ape)}. {backtest.n_undefined_ape} settlement(s) landed at or below zero, where a
              percentage error has no meaning, and are excluded rather than divided by.
            </span>
          </div>
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(backtest.interval_coverage)}</span>
            <span className="fee-leak-summary-label">
              landed inside the predicted window. That window is the rail's SLA tolerance, a policy boundary that states
              no confidence level, so this is its hit rate rather than a calibrated interval. The calibrated alternative
              is on the autonomy page.
            </span>
          </div>
        </div>
      )}

      {blindBacktest && (
        <div className="fee-leak-summary">
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(blindBacktest.mape)}</span>
            <span className="fee-leak-summary-label">
              MAPE, genuinely-blind backtest — real fee/SLA schedule hidden from the predictor (n={blindBacktest.n})
            </span>
          </div>
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(blindBacktest.interval_coverage)}</span>
            <span className="fee-leak-summary-label">
              interval coverage under the same hidden schedule drift — swings 3%–100% seed to seed; see LIMITATIONS.md
            </span>
          </div>
        </div>
      )}

      {forecast && (
        <>
          <div className="fee-leak-summary">
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{rupees(forecast.working_capital.total_unsettled_net)}</span>
              <span className="fee-leak-summary-label">in transit, net of predicted fees</span>
            </div>
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{rupees(forecast.working_capital.at_sla_risk_amount)}</span>
              <span className="fee-leak-summary-label">already past its predicted SLA ceiling</span>
            </div>
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{forecast.predictions.length}</span>
              <span className="fee-leak-summary-label">pending transactions</span>
            </div>
          </div>

          <table className="calibration-table">
            <thead>
              <tr>
                <th>Transaction</th>
                <th>Rail</th>
                <th>Captured</th>
                <th>Predicted net</th>
                <th>Predicted window</th>
              </tr>
            </thead>
            <tbody>
              {forecast.predictions.map((p) => (
                <tr key={p.transaction_id}>
                  <td className="mono">{p.transaction_id}</td>
                  <td>{p.rail}</td>
                  <td>{rupees(p.captured_amount)}</td>
                  <td>{rupees(p.predicted_net_amount)}</td>
                  <td>
                    {formatDateShort(p.predicted_date_low)} – {formatDateShort(p.predicted_date_high)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <div className="payroll-check">
        <h3>Payroll / shortfall check</h3>
        <p className="panel-sub">
          Given a scheduled outflow, does the forward-predicted pending cash cover it — counted conservatively, only
          once a prediction's <em>late</em> end has passed.
        </p>
        <div className="run-controls-grid">
          <label>
            Outflow amount (₹)
            <input type="number" min={1} value={outflowRupees} onChange={(e) => setOutflowRupees(Number(e.target.value))} />
          </label>
          <label>
            Outflow date
            <input type="date" value={outflowDate} onChange={(e) => setOutflowDate(e.target.value)} />
          </label>
          <button type="button" disabled={payrollLoading} onClick={handlePayrollCheck}>
            {payrollLoading ? "Checking…" : "Check coverage"}
          </button>
        </div>
        {payrollError && (
          <p className="error-text" role="alert">
            {payrollError}
          </p>
        )}
        {payrollResult && (
          <p className={payrollResult.clears ? "escalation-resolved was-correct" : "escalation-resolved was-wrong"}>
            {payrollResult.clears ? (
              <>
                <strong>Clears.</strong> {rupees(payrollResult.predicted_available_amount)} predicted to land by{" "}
                {payrollResult.outflow_date}, covering the {rupees(payrollResult.outflow_amount)} outflow.
              </>
            ) : (
              <>
                <strong>Short by {rupees(payrollResult.shortfall_amount)}.</strong> Only{" "}
                {rupees(payrollResult.predicted_available_amount)} predicted to land by {payrollResult.outflow_date} against a{" "}
                {rupees(payrollResult.outflow_amount)} outflow.
              </>
            )}
          </p>
        )}
      </div>
    </section>
  );
}
