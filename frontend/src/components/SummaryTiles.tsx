import { useEffect, useState } from "react";
import { getRegret } from "../api";
import type { BatchRunResult, RegretReport } from "../types";
import { pct, rupees } from "../formatters";

interface Props {
  result: BatchRunResult;
  refreshKey: number; // bump after a run or a resolved escalation, same convention as CalibrationPanel
}

export function SummaryTiles({ result, refreshKey }: Props) {
  const [regret, setRegret] = useState<RegretReport | null>(null);

  // Regret is accumulated-history data (app/calibration/regret.py), not part of this one batch's
  // own result -- refetched the same way CalibrationPanel re-fetches the live dial, not read off
  // `result` itself.
  useEffect(() => {
    getRegret(result.threshold)
      .then(setRegret)
      .catch(console.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // 0/0 is NaN, which renders as the literal string "NaN%" -- reachable with a deliberately
  // zero-sized batch (main_n=0), which the backend itself handles fine (an all-zero result, not a
  // crash). Guarded here rather than on the batch-size input, since the input's min is just a soft
  // HTML hint a user can still type past. Caught by an external audit 2026-08-24.
  const reconciledPct = result.total_amount > 0 ? result.amount_reconciled / result.total_amount : 0;
  const escalatedPct = result.total_transactions > 0 ? result.escalated_count / result.total_transactions : 0;
  const leakCount = result.fee_leak_report.findings.length;

  return (
    <section className="tiles">
      <div className="tile">
        <span className="tile-label">Transactions</span>
        <span className="tile-value">{result.total_transactions}</span>
        <span className="tile-sub">{rupees(result.total_amount)} total value</span>
      </div>
      <div className="tile tile-good">
        <span className="tile-label">Auto-reconciled</span>
        <span className="tile-value">{pct(reconciledPct)}</span>
        <span className="tile-sub">{rupees(result.amount_reconciled)}</span>
      </div>
      <div className="tile tile-warn">
        <span className="tile-label">Escalated</span>
        <span className="tile-value">{result.escalated_count}</span>
        <span className="tile-sub">{pct(escalatedPct)} of batch — honest, not hidden</span>
      </div>
      <div className="tile">
        <span className="tile-label">Narrator provider</span>
        <span className="tile-value">{result.provider}</span>
        <span className="tile-sub">seed {result.seed} · threshold {pct(result.threshold, 0)}</span>
      </div>
      <div className="tile">
        <span className="tile-label">Throughput</span>
        <span className="tile-value">{result.elapsed_seconds < 0.01 ? "instant" : `${result.elapsed_seconds.toFixed(2)}s`}</span>
        <span className="tile-sub">
          {result.total_transactions} txns ({result.narrated_count} narrated)
          {result.elapsed_seconds >= 0.01 && ` · ${result.transactions_per_second.toFixed(1)}/s`}
          {result.elapsed_seconds < 0.01 && " (mock — no network calls)"}
        </span>
      </div>
      <div className="tile tile-warn">
        <span className="tile-label">Fee recovery</span>
        <span className="tile-value">{rupees(result.fee_leak_report.total_fee_recovery)}</span>
        <span className="tile-sub">
          {leakCount} leak{leakCount === 1 ? "" : "s"} found — overcharged vs. contracted fee
        </span>
      </div>
      <div className="tile tile-good">
        <span className="tile-label">ITC separated</span>
        <span className="tile-value">{rupees(result.total_itc_separated)}</span>
        <span className="tile-sub">GST-on-fee split into its own ledger line across the batch's journal, ready for GSTR-2B</span>
      </div>
      {regret && (
        <>
          <div className={regret.realized_regret_amount > 0 ? "tile tile-warn" : "tile tile-good"}>
            <span className="tile-label">Regret in rupees</span>
            <span className="tile-value">{rupees(regret.realized_regret_amount)}</span>
            <span className="tile-sub">
              {regret.realized_regret_transaction_count} real transaction{regret.realized_regret_transaction_count === 1 ? "" : "s"}{" "}
              actually auto-resolved wrong, across all accumulated history — realized, not a forward-looking estimate
            </span>
          </div>
          <div className="tile">
            <span className="tile-label">Analyst hours saved</span>
            <span className="tile-value">{regret.estimated_analyst_hours_saved.toFixed(1)}h</span>
            <span className="tile-sub">
              estimate — {regret.auto_resolved_transaction_count} auto-resolved transactions × {regret.minutes_per_manual_review_assumption}
              min/review assumption, not a measured fact
            </span>
          </div>
        </>
      )}
    </section>
  );
}
