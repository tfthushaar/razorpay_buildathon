import type { BatchRunResult } from "../types";

/**
 * A slim "you are looking at this run" strip for pages that render results but do not own the run
 * controls.
 *
 * Repeating the full control panel on four pages would be noise, and dropping it entirely would
 * leave a reader unsure which run the numbers belong to after navigating. This says what is on
 * screen and offers one click back to where it can be changed.
 */
export function RunContextStrip({
  result,
  isSample,
  loading,
  onGoToControls,
}: {
  result: BatchRunResult;
  isSample: boolean;
  loading: boolean;
  onGoToControls: () => void;
}) {
  return (
    <div className={`run-context${isSample ? " is-sample" : ""}`}>
      <span className="run-context-facts">
        {isSample ? "Committed sample run" : "Live run"}
        <span className="run-context-sep">·</span>seed {result.seed}
        <span className="run-context-sep">·</span>
        {result.provider}
        <span className="run-context-sep">·</span>
        {result.total_transactions.toLocaleString()} transactions
        <span className="run-context-sep">·</span>
        {result.escalated_count} escalated
      </span>
      <button type="button" className="run-context-change" onClick={onGoToControls} disabled={loading}>
        {loading ? "Running…" : isSample ? "Run a live batch →" : "Change run →"}
      </button>
    </div>
  );
}
