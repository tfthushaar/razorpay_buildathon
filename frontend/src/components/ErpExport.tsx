import { useState } from "react";
import { exportJournal } from "../api";
import type { JournalExportResponse } from "../types";
import type { BatchRunResult } from "../types";

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

const PREVIEW_LINES = 20;

// result is unused beyond gating this section on a run existing (matches the other components'
// `{ result: BatchRunResult }` pattern in App.tsx), same as EscalationQueue only reading a couple
// of top-level fields off the run.
export function ErpExport({ result: _result }: { result: BatchRunResult }) {
  const [format, setFormat] = useState<Format>("tally");
  const [exportResult, setExportResult] = useState<JournalExportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExport = async (nextFormat: Format) => {
    setFormat(nextFormat);
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
      </div>
      {loading && <p className="empty-row">Generating export…</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {exportResult && !loading && (
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
    </section>
  );
}
