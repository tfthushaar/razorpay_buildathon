import type { BatchRunResult } from "../types";
import { pct, rupees } from "../formatters";

export function SummaryTiles({ result }: { result: BatchRunResult }) {
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
    </section>
  );
}
