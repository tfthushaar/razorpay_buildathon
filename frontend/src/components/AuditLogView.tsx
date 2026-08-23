import { useEffect, useState } from "react";
import { getAudit } from "../api";
import type { AuditEntry } from "../types";
import { categoryLabel } from "../formatters";

const DECISION_LABELS: Record<string, string> = {
  clean_pass1: "Clean (exact match)",
  auto_resolved_deterministic: "Auto-resolved (deterministic)",
  auto_resolved_calibrated: "Auto-resolved (calibrated)",
  escalated: "Escalated",
};

export function AuditLogView({ runId, refreshKey }: { runId: string; refreshKey: number }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    getAudit(runId).then(setEntries).catch(console.error);
  }, [runId, refreshKey, open]);

  return (
    <section className="panel">
      <button className="link-button" onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} Audit log for this run ({entries.length || "…"} decisions)
      </button>
      {open && (
        <div className="audit-table-wrap">
          <table className="audit-table">
            <thead>
              <tr>
                <th>Transaction</th>
                <th>Decision</th>
                <th>Category</th>
                <th>Confidence</th>
                <th>Reasoning</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="mono">{e.transaction_id}</td>
                  <td>{DECISION_LABELS[e.decision] ?? e.decision}</td>
                  <td>{e.category ? categoryLabel(e.category) : "—"}</td>
                  <td>{e.confidence != null ? e.confidence.toFixed(2) : "—"}</td>
                  <td className="reasoning-cell">{e.reasoning ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
