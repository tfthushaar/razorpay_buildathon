import type { BatchRunResult } from "../types";
import { pct, rupees } from "../formatters";

export function SummaryTiles({ result }: { result: BatchRunResult }) {
  const reconciledPct = result.amount_reconciled / result.total_amount;
  const escalatedPct = result.escalated_count / result.total_transactions;

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
        <span className="tile-value">{result.elapsed_seconds.toFixed(2)}s</span>
        <span className="tile-sub">
          {result.total_transactions} txns ({result.narrated_count} narrated) · {result.transactions_per_second.toFixed(1)}/s
        </span>
      </div>
    </section>
  );
}
