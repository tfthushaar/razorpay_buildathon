import { Fragment, useEffect, useState } from "react";
import { getAudit } from "../api";
import type { AuditEntry } from "../types";
import { categoryLabel } from "../formatters";

const DECISION_LABELS: Record<string, string> = {
  clean_pass1: "Clean (exact match)",
  auto_resolved_deterministic: "Auto-resolved (deterministic)",
  auto_resolved_calibrated: "Auto-resolved (calibrated)",
  escalated: "Escalated",
};

interface ToolCallRecord {
  tool: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
}

function parseToolCalls(json: string): ToolCallRecord[] {
  try {
    const parsed = JSON.parse(json);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function AuditLogView({ runId, refreshKey }: { runId: string; refreshKey: number }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

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
                <th>Tool calls</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const toolCalls = parseToolCalls(e.tool_calls_json);
                const isExpanded = expandedId === e.id;
                return (
                  <Fragment key={e.id}>
                    <tr>
                      <td className="mono">{e.transaction_id}</td>
                      <td>{DECISION_LABELS[e.decision] ?? e.decision}</td>
                      <td>{e.category ? categoryLabel(e.category) : "—"}</td>
                      <td>{e.confidence != null ? e.confidence.toFixed(2) : "—"}</td>
                      <td className="reasoning-cell">{e.reasoning ?? "—"}</td>
                      <td>
                        {toolCalls.length > 0 ? (
                          <button
                            type="button"
                            className="link-button tool-call-toggle"
                            onClick={() => setExpandedId(isExpanded ? null : e.id)}
                          >
                            {isExpanded ? "▾" : "▸"} {toolCalls.length} call{toolCalls.length === 1 ? "" : "s"}
                          </button>
                        ) : (
                          <span className="empty-row">—</span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && toolCalls.length > 0 && (
                      <tr className="tool-call-detail-row">
                        <td colSpan={6}>
                          <ul className="tool-call-list">
                            {toolCalls.map((tc, i) => (
                              <li key={i}>
                                <span className="mono tool-call-name">{tc.tool}</span>
                                <span className="tool-call-args">args: {JSON.stringify(tc.arguments)}</span>
                                <span className="tool-call-result">result: {JSON.stringify(tc.result)}</span>
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
