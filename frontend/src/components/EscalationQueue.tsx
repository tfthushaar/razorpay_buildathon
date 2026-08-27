import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { getAudit, resolveEscalation } from "../api";
import type { AuditEntry, CategoryProposal, EscalationItem, ResolveResponse } from "../types";
import { categoryLabel, rupees, pct, parseToolCalls } from "../formatters";

interface Props {
  escalations: EscalationItem[];
  runId: string; // used to fetch this run's tool-call trace for each escalated case, same source the audit log reads
  onResolved: () => void; // tells the parent to bump the calibration refresh key
  liveAutoResolveCategories: Set<string>; // reflects the calibration dial's current position, not the run-time threshold
  categoryProposals: CategoryProposal[]; // only non-empty when the run had "Propose new categories" enabled
}

// Total wall-clock budget for the staggered reveal -- mirrors AuditLogView's so both "decision
// feed" lists feel like the same live system rather than two differently-timed animations.
const REVEAL_BUDGET_MS = 1200;
const REVEAL_MAX_PER_ITEM_MS = 90;

export function EscalationQueue({ escalations, runId, onResolved, liveAutoResolveCategories, categoryProposals }: Props) {
  const [resolved, setResolved] = useState<Record<string, ResolveResponse>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [resolveErrors, setResolveErrors] = useState<Record<string, string>>({});
  const [auditByTxn, setAuditByTxn] = useState<Record<string, AuditEntry>>({});
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);

  // The escalation list itself (this prop) only ever changes reference on a genuinely new run --
  // dragging the calibration threshold only changes `liveAutoResolveCategories`, and resolving an
  // item only moves it into local `resolved` state below. So keying the reveal animation off this
  // reference is exactly "animate on a new run, not on every re-render" without any extra flags.
  const animatedEscalations = useRef<EscalationItem[] | null>(null);
  const [animateReveal, setAnimateReveal] = useState(false);

  useEffect(() => {
    const isNew = animatedEscalations.current !== escalations;
    animatedEscalations.current = escalations;
    setAnimateReveal(isNew);
    // The highest-value, least-certain case is the one a judge sees first, and it should look like
    // real reasoning immediately -- not one click away. Auto-expand only the first escalation's own
    // tool-call trace on a genuinely new run, never re-collapsing anything a user has open or closed
    // by hand on a threshold drag/resolve (those don't change the `escalations` reference).
    if (isNew && escalations.length > 0) {
      setExpandedTraceId(escalations[0].transaction_id);
    }
  }, [escalations]);

  // Escalation cases already carry category/confidence/reasoning, but the tool-call trace that
  // shows exactly what the narrator checked before answering lives on this run's audit entries --
  // fetched once per run and matched by transaction id, rather than duplicated onto EscalationItem.
  useEffect(() => {
    if (!runId) return;
    getAudit(runId)
      .then((entries) => {
        const map: Record<string, AuditEntry> = {};
        for (const e of entries) map[e.transaction_id] = e;
        setAuditByTxn(map);
      })
      .catch(console.error);
  }, [runId]);

  const [crossedThreshold, setCrossedThreshold] = useState<Record<string, boolean>>({});

  const handleResolve = async (transactionId: string) => {
    setPending(transactionId);
    setResolveErrors((prev) => ({ ...prev, [transactionId]: "" }));
    try {
      const category = escalations.find((e) => e.transaction_id === transactionId)?.category;
      const wasAutoResolveBefore = category !== undefined && liveAutoResolveCategories.has(category);
      const resp = await resolveEscalation(transactionId);
      const isAutoResolveAfter = resp.updated_calibration.categories.some((c) => c.category === category && c.decision === "auto_resolve");
      setResolved((prev) => ({ ...prev, [transactionId]: resp }));
      // The moment a category earns auto-resolve is otherwise invisible unless a viewer happens to
      // notice the calibration table above change on its own -- this makes the exact resolve action
      // that caused it explicit, right where it happened, rather than something to spot separately.
      if (!wasAutoResolveBefore && isAutoResolveAfter) {
        setCrossedThreshold((prev) => ({ ...prev, [transactionId]: true }));
      }
      onResolved();
    } catch (err) {
      // used to just log to the console and silently re-enable the button, leaving no trace
      // anything went wrong -- caught by an external audit 2026-08-24.
      console.error(err);
      setResolveErrors((prev) => ({ ...prev, [transactionId]: err instanceof Error ? err.message : "Could not resolve this escalation." }));
    } finally {
      setPending(null);
    }
  };

  const pendingItems = escalations.filter((e) => !resolved[e.transaction_id]);
  const proposalByTxn: Record<string, CategoryProposal> = {};
  for (const p of categoryProposals) proposalByTxn[p.transaction_id] = p;

  return (
    <section className="panel" id="escalation-queue-panel">
      <h2>Escalation queue — triaged by ₹ amount × ambiguity</h2>
      <p className="panel-sub">
        Highest-value, least-certain cases surface first. This is the honest exception list — nothing here was hidden.
        These reflect the threshold at run time; items marked below would flip to auto-resolve at the calibration
        dial's current position, without needing a human, once a real run is made at that threshold.
      </p>
      {pendingItems.length === 0 && Object.keys(resolved).length === 0 && (
        <div className="clean-sweep">
          <span className="clean-sweep-icon">✓</span>
          <span className="clean-sweep-text">
            <strong>Clean sweep — nothing to escalate.</strong>
            <span>Every case in this run cleared the calibration threshold on its own. That's the system working, not a blank state.</span>
          </span>
        </div>
      )}
      <ul className="escalation-list">
        {pendingItems.map((item, index) => {
          const wouldAutoResolve = liveAutoResolveCategories.has(item.category);
          const toolCalls = auditByTxn[item.transaction_id] ? parseToolCalls(auditByTxn[item.transaction_id].tool_calls_json) : [];
          const isFirst = index === 0;
          const isTraceOpen = expandedTraceId === item.transaction_id;
          return (
            <li
              key={item.transaction_id}
              id={isFirst ? "tour-first-escalation" : undefined}
              className={`escalation-item ${wouldAutoResolve ? "would-auto-resolve" : ""} ${animateReveal ? "reveal-item" : ""}`}
              style={animateReveal ? ({ "--delay": `${Math.min(index * Math.min(REVEAL_MAX_PER_ITEM_MS, REVEAL_BUDGET_MS / Math.max(pendingItems.length, 1)), REVEAL_BUDGET_MS)}ms` } as CSSProperties) : undefined}
            >
              <div className="escalation-header">
                <span className="badge badge-warn">{categoryLabel(item.category)}</span>
                <span className="escalation-amount">{rupees(item.amount)}</span>
                <span className="escalation-confidence">confidence {pct(item.confidence)}</span>
                {wouldAutoResolve && <span className="badge badge-good">Would auto-resolve at current dial</span>}
              </div>
              <p className="escalation-reasoning">{item.reasoning}</p>
              {item.category === "genuine_error" && proposalByTxn[item.transaction_id]?.proposed_name && (
                <div className="category-proposal">
                  <span className="badge badge-neutral">Proposed new category (unreviewed)</span>
                  <p className="category-proposal-name mono">{proposalByTxn[item.transaction_id].proposed_name}</p>
                  <p className="category-proposal-hypothesis">{proposalByTxn[item.transaction_id].hypothesis}</p>
                  {proposalByTxn[item.transaction_id].supporting_evidence.length > 0 && (
                    <ul className="category-proposal-evidence">
                      {proposalByTxn[item.transaction_id].supporting_evidence.map((ev, i) => (
                        <li key={i}>{ev}</li>
                      ))}
                    </ul>
                  )}
                  <p className="category-proposal-footer">
                    confidence {pct(proposalByTxn[item.transaction_id].confidence)} — a candidate hypothesis for a human to review, never
                    auto-adopted
                  </p>
                </div>
              )}
              {toolCalls.length > 0 && (
                <button
                  type="button"
                  className="link-button tool-call-toggle"
                  onClick={() => setExpandedTraceId(isTraceOpen ? null : item.transaction_id)}
                >
                  {isTraceOpen ? "▾" : "▸"} {toolCalls.length} tool call{toolCalls.length === 1 ? "" : "s"} — what the narrator checked
                </button>
              )}
              {isTraceOpen && toolCalls.length > 0 && (
                <ul className="tool-call-list escalation-tool-calls">
                  {toolCalls.map((tc, i) => (
                    <li key={i}>
                      <span className="mono tool-call-name">{tc.tool}</span>
                      <span className="tool-call-args">args: {JSON.stringify(tc.arguments)}</span>
                      <span className="tool-call-result">result: {JSON.stringify(tc.result)}</span>
                    </li>
                  ))}
                </ul>
              )}
              <button
                id={isFirst ? "tour-resolve-button" : undefined}
                disabled={pending === item.transaction_id}
                onClick={() => handleResolve(item.transaction_id)}
              >
                {pending === item.transaction_id ? "Resolving…" : "Resolve against source records"}
              </button>
              {resolveErrors[item.transaction_id] && (
                <p className="error-text" role="alert">
                  {resolveErrors[item.transaction_id]}
                </p>
              )}
            </li>
          );
        })}
        {Object.values(resolved).map((r) => (
          <li key={r.transaction_id} className={`escalation-item escalation-resolved ${r.was_correct ? "was-correct" : "was-wrong"}`}>
            <div className="escalation-header">
              <span className="badge badge-neutral">{r.transaction_id}</span>
              <span>
                Narrator said <strong>{categoryLabel(r.predicted_category)}</strong>; confirmed{" "}
                <strong>{categoryLabel(r.confirmed_true_label)}</strong> {r.was_correct ? "✓" : "✗"}
              </span>
            </div>
            {crossedThreshold[r.transaction_id] && (
              <p className="threshold-crossed-callout">
                🎯 This confirmation just pushed <strong>{categoryLabel(r.predicted_category)}</strong> over the auto-resolve
                threshold, live — see the calibration table above.
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
