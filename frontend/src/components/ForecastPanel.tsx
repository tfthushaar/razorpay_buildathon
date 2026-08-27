import { useEffect, useState } from "react";
import { checkPayrollCoverage, getForecastBacktest, getPendingForecast } from "../api";
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
        money, not a lookup. The backtest below scores this same predictor honestly against the latest run's own real
        settlements.
      </p>

      {fetchError && (
        <p className="error-text" role="alert">
          {fetchError}
        </p>
      )}

      {backtest && (
        <div className="fee-leak-summary">
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(backtest.mape)}</span>
            <span className="fee-leak-summary-label">MAPE on predicted vs. actual net amount</span>
          </div>
          <div className="fee-leak-summary-stat">
            <span className="fee-leak-summary-value">{pct(backtest.interval_coverage)}</span>
            <span className="fee-leak-summary-label">of {backtest.n} real settlements landed inside the predicted window</span>
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
