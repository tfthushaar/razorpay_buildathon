import { useState } from "react";
import { exportJournal, getGstr2bMatch } from "../api";
import type { Gstr2bResponse, JournalExportResponse } from "../types";
import type { BatchRunResult } from "../types";
import { rupees } from "../formatters";

type Format = "tally" | "zoho" | "generic";

const FORMAT_LABELS: Record<Format, string> = {
  tally: "Tally XML",
  zoho: "Zoho Books CSV",
  generic: "Generic CSV",
};

const FORMAT_MIME: Record<Format, string> = {
  tally: "text/xml",
  zoho: "text/csv",
  generic: "text/csv",
};

const FORMAT_EXTENSION: Record<Format, string> = {
  tally: "xml",
  zoho: "csv",
  generic: "csv",
};

const EXCEPTION_KIND_LABELS: Record<string, string> = {
  missing_in_gstr2b: "Not yet filed by supplier",
  amount_mismatch: "Amount mismatch",
  blocked_credit: "Blocked credit (Sec 17(5))",
};

const PREVIEW_LINES = 20;

// result is unused beyond gating this section on a run existing (matches the other components'
// `{ result: BatchRunResult }` pattern in App.tsx), same as EscalationQueue only reading a couple
// of top-level fields off the run.
export function ErpExport({ result: _result }: { result: BatchRunResult }) {
  const [format, setFormat] = useState<Format | "gstr2b">("tally");
  const [exportResult, setExportResult] = useState<JournalExportResponse | null>(null);
  const [gstr2bResult, setGstr2bResult] = useState<Gstr2bResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExport = async (nextFormat: Format) => {
    setFormat(nextFormat);
    setGstr2bResult(null);
    setLoading(true);
    setError(null);
    try {
      const resp = await exportJournal(nextFormat);
      setExportResult(resp);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : "Could not export the journal.");
    } finally {
      setLoading(false);
    }
  };

  const handleGstr2b = async () => {
    setFormat("gstr2b");
    setExportResult(null);
    setLoading(true);
    setError(null);
    try {
      setGstr2bResult(await getGstr2bMatch());
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : "Could not match against GSTR-2B.");
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = () => {
    if (!exportResult) return;
    const blob = new Blob([exportResult.content], { type: FORMAT_MIME[exportResult.format] });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `journal-export.${FORMAT_EXTENSION[exportResult.format]}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const previewLines = exportResult ? exportResult.content.split("\n") : [];
  const preview = previewLines.slice(0, PREVIEW_LINES).join("\n");
  const truncated = previewLines.length > PREVIEW_LINES;

  return (
    <section className="panel">
      <h2>ERP journal export</h2>
      <p className="panel-sub">
        Export this run's reconciliation decisions as a journal ready to import into your accounting system. Every
        transaction posts, balanced — one still sitting in the escalation queue posts with a "pending review" note
        instead of being silently forced or left out.
      </p>
      <div className="preset-buttons">
        {(Object.keys(FORMAT_LABELS) as Format[]).map((f) => (
          <button
            key={f}
            type="button"
            className={f === format ? "" : "secondary-button"}
            disabled={loading}
            onClick={() => handleExport(f)}
          >
            {FORMAT_LABELS[f]}
          </button>
        ))}
        <button type="button" className={format === "gstr2b" ? "" : "secondary-button"} disabled={loading} onClick={handleGstr2b}>
          GSTR-2B match
        </button>
      </div>
      {loading && <p className="empty-row">Generating export…</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {exportResult && !loading && format !== "gstr2b" && (
        <>
          <p className="pitch-line">
            <strong>{exportResult.entry_count}</strong> entries generated. <strong>{exportResult.finalized_count}</strong>{" "}
            finalized, <strong>{exportResult.pending_count}</strong> pending human review before the journal is
            finalized.
          </p>
          <div className="export-preview-wrap">
            <pre className="export-preview">
              {preview}
              {truncated && "\n…"}
            </pre>
          </div>
          <button type="button" onClick={handleDownload}>
            Download {FORMAT_LABELS[exportResult.format]}
          </button>
        </>
      )}
      {gstr2bResult && !loading && format === "gstr2b" && (
        <>
          <p className="panel-sub">
            Our own books, matched against a <em>simulated</em> GSTR-2B (the supplier's own filing isn't available in
            this sandbox — see docs for why a real reconciliation needs an independent second side).
          </p>
          <div className="fee-leak-summary">
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{rupees(gstr2bResult.match_report.matched_itc_amount)}</span>
              <span className="fee-leak-summary-label">{gstr2bResult.match_report.matched_count} transactions matched cleanly</span>
            </div>
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{rupees(gstr2bResult.match_report.exception_itc_amount)}</span>
              <span className="fee-leak-summary-label">
                {gstr2bResult.match_report.exceptions.length} exception{gstr2bResult.match_report.exceptions.length === 1 ? "" : "s"} at
                risk
              </span>
            </div>
          </div>
          {gstr2bResult.match_report.exceptions.length > 0 && (
            <table className="calibration-table">
              <thead>
                <tr>
                  <th>Transaction</th>
                  <th>Kind</th>
                  <th>Our ITC</th>
                  <th>GSTR-2B ITC</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {gstr2bResult.match_report.exceptions.map((e) => (
                  <tr key={e.transaction_id}>
                    <td className="mono">{e.transaction_id}</td>
                    <td>{EXCEPTION_KIND_LABELS[e.kind] ?? e.kind}</td>
                    <td>{rupees(e.our_itc_amount)}</td>
                    <td>{e.gstr2b_itc_amount === null ? "—" : rupees(e.gstr2b_itc_amount)}</td>
                    <td>{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
