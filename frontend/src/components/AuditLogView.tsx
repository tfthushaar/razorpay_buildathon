import { Fragment, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { getAudit } from "../api";
import type { AuditEntry } from "../types";
import { categoryLabel, parseToolCalls } from "../formatters";

const DECISION_LABELS: Record<string, string> = {
  clean_pass1: "Clean (exact match)",
  auto_resolved_deterministic: "Auto-resolved (deterministic)",
  auto_resolved_calibrated: "Auto-resolved (calibrated)",
  escalated: "Escalated",
};

// Same three-way color language as the calibration badges: dot color communicates whether a
// human was needed, independent of the label text next to it.
const DECISION_DOT: Record<string, string> = {
  clean_pass1: "decision-dot-clean",
  auto_resolved_deterministic: "decision-dot-auto",
  auto_resolved_calibrated: "decision-dot-auto",
  escalated: "decision-dot-escalated",
};

// Total wall-clock budget for the staggered reveal, regardless of how many rows there are --
// a 120-row batch shouldn't take any longer to finish "arriving" than a 10-row one.
const REVEAL_BUDGET_MS = 1400;
const REVEAL_MAX_PER_ROW_MS = 40;

export function AuditLogView({ runId, refreshKey }: { runId: string; refreshKey: number }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  // Tracks which run ids have already played the staggered-reveal animation, so re-opening the
  // panel or refreshing after resolving an escalation (same run, refreshKey bumps too) shows the
  // table immediately instead of replaying the whole reveal on a judge who's already seen it.
  const animatedRunIds = useRef<Set<string>>(new Set());
  const [animateThisOpen, setAnimateThisOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    getAudit(runId)
      .then((fetched) => {
        setAnimateThisOpen(!animatedRunIds.current.has(runId));
        animatedRunIds.current.add(runId);
        setEntries(fetched);
      })
      .catch(console.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, refreshKey, open]);

  const delayFor = (index: number) =>
    animateThisOpen ? Math.min(index * Math.min(REVEAL_MAX_PER_ROW_MS, REVEAL_BUDGET_MS / Math.max(entries.length, 1)), REVEAL_BUDGET_MS) : 0;

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
              {entries.map((e, i) => {
                const toolCalls = parseToolCalls(e.tool_calls_json);
                const isExpanded = expandedId === e.id;
                return (
                  <Fragment key={e.id}>
                    <tr className={animateThisOpen ? "reveal-item" : undefined} style={animateThisOpen ? ({ "--delay": `${delayFor(i)}ms` } as CSSProperties) : undefined}>
                      <td className="mono">{e.transaction_id}</td>
                      <td>
                        <span className="decision-cell">
                          <span className={`decision-dot ${DECISION_DOT[e.decision] ?? "decision-dot-clean"}`} />
                          {DECISION_LABELS[e.decision] ?? e.decision}
                        </span>
                      </td>
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
                            {toolCalls.map((tc, ti) => (
                              <li key={ti}>
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
