import { useState } from "react";
import { resolveEscalation } from "../api";
import type { EscalationItem, ResolveResponse } from "../types";
import { categoryLabel, rupees, pct } from "../formatters";

interface Props {
  escalations: EscalationItem[];
  onResolved: () => void; // tells the parent to bump the calibration refresh key
  liveAutoResolveCategories: Set<string>; // reflects the calibration dial's current position, not the run-time threshold
}

export function EscalationQueue({ escalations, onResolved, liveAutoResolveCategories }: Props) {
  const [resolved, setResolved] = useState<Record<string, ResolveResponse>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [resolveErrors, setResolveErrors] = useState<Record<string, string>>({});

  const handleResolve = async (transactionId: string) => {
    setPending(transactionId);
    setResolveErrors((prev) => ({ ...prev, [transactionId]: "" }));
    try {
      const resp = await resolveEscalation(transactionId);
      setResolved((prev) => ({ ...prev, [transactionId]: resp }));
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

  return (
    <section className="panel">
      <h2>Escalation queue — triaged by ₹ amount × ambiguity</h2>
      <p className="panel-sub">
        Highest-value, least-certain cases surface first. This is the honest exception list — nothing here was hidden.
        These reflect the threshold at run time; items marked below would flip to auto-resolve at the calibration
        dial's current position, without needing a human, once a real run is made at that threshold.
      </p>
      {pendingItems.length === 0 && Object.keys(resolved).length === 0 && (
        <p className="empty-row">No escalations in the latest run.</p>
      )}
      <ul className="escalation-list">
        {pendingItems.map((item) => {
          const wouldAutoResolve = liveAutoResolveCategories.has(item.category);
          return (
            <li key={item.transaction_id} className={`escalation-item ${wouldAutoResolve ? "would-auto-resolve" : ""}`}>
              <div className="escalation-header">
                <span className="badge badge-warn">{categoryLabel(item.category)}</span>
                <span className="escalation-amount">{rupees(item.amount)}</span>
                <span className="escalation-confidence">confidence {pct(item.confidence)}</span>
                {wouldAutoResolve && <span className="badge badge-good">Would auto-resolve at current dial</span>}
              </div>
              <p className="escalation-reasoning">{item.reasoning}</p>
              <button disabled={pending === item.transaction_id} onClick={() => handleResolve(item.transaction_id)}>
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
          </li>
        ))}
      </ul>
    </section>
  );
}
