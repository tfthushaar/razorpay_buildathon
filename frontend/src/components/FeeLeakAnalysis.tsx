import { useState } from "react";
import type { BatchRunResult } from "../types";
import { rupees } from "../formatters";

export function FeeLeakAnalysis({ result }: { result: BatchRunResult }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  // Every finding rendered at once made this panel about a fifth of the page. The count and the
  // recovered total are the point; the individual rows are for someone who wants to check one.
  const [showAll, setShowAll] = useState(false);
  const VISIBLE = 6;

  // Backend already sorts by -abs(total_impact) (app/feeleak/detector.py) -- re-sorting here
  // defensively rather than trusting it blindly, since a sort defined once in the API contract is
  // cheap to re-assert on the display side and doesn't cost anything if the backend already did it.
  const findings = [...result.fee_leak_report.findings].sort((a, b) => b.total_impact - a.total_impact);
  const report = result.fee_leak_report;

  const handleCopy = async (transactionId: string, template: string) => {
    try {
      await navigator.clipboard.writeText(template);
      setCopiedId(transactionId);
      setTimeout(() => setCopiedId((id) => (id === transactionId ? null : id)), 1500);
    } catch (err) {
      // Clipboard access can be blocked (permissions, insecure context, browser policy) -- fails
      // quietly into the console rather than throwing, same posture as the rest of this app's
      // non-critical side effects.
      console.error(err);
    }
  };

  return (
    <section className="panel">
      <h2>Fee leak analysis — overcharged vs. contracted rate</h2>
      <p className="panel-sub">
        A separate batch of transactions that reconcile perfectly cleanly but were billed above the merchant's own fee
        contract — blended-rate overcharges and GST computed on the wrong base. Ranked by ₹ impact, highest first.
      </p>
      {findings.length === 0 && <p className="empty-row">No fee leaks detected in this batch.</p>}
      {findings.length > 0 && (
        <>
          <div className="fee-leak-summary">
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{rupees(report.total_fee_recovery)}</span>
              <span className="fee-leak-summary-label">recoverable fees</span>
            </div>
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{rupees(report.total_gst_correction)}</span>
              <span className="fee-leak-summary-label">miscalculated tax</span>
            </div>
            <div className="fee-leak-summary-stat">
              <span className="fee-leak-summary-value">{findings.length}</span>
              <span className="fee-leak-summary-label">
                findings across {Object.keys(report.by_pattern).length} pattern{Object.keys(report.by_pattern).length === 1 ? "" : "s"}
              </span>
            </div>
          </div>
          <ul className="fee-leak-list">
            {(showAll ? findings : findings.slice(0, VISIBLE)).map((f) => {
              const isOpen = expandedId === f.transaction_id;
              return (
                <li key={f.transaction_id} className={`fee-leak-row${isOpen ? " is-open" : ""}`}>
                  <button
                    type="button"
                    className="fee-leak-row-header"
                    onClick={() => setExpandedId(isOpen ? null : f.transaction_id)}
                    aria-expanded={isOpen}
                  >
                    <span className="fee-leak-row-chevron" aria-hidden="true">
                      {isOpen ? "▾" : "▸"}
                    </span>
                    <span className="badge badge-neutral">{f.transaction_id}</span>
                    <span className="badge badge-warn">{f.pattern_label}</span>
                    <span className="fee-leak-row-rail">{f.rail}</span>
                    <span className="fee-leak-row-amount">{rupees(f.total_impact)}</span>
                  </button>
                  {isOpen && (
                    <div className="fee-leak-row-detail">
                      <p className="escalation-reasoning">
                        Contracted fee <strong>{rupees(f.contracted_fee)}</strong> vs. actual{" "}
                        <strong>{rupees(f.actual_fee)}</strong> (variance {rupees(f.fee_variance)}) · contracted GST{" "}
                        <strong>{rupees(f.contracted_gst)}</strong> vs. actual <strong>{rupees(f.actual_gst)}</strong>{" "}
                        (variance {rupees(f.gst_variance)})
                      </p>
                      <div className="dispute-template-block">
                        <pre className="dispute-template-text">{f.dispute_template}</pre>
                        <button type="button" className="secondary-button" onClick={() => handleCopy(f.transaction_id, f.dispute_template)}>
                          {copiedId === f.transaction_id ? "Copied ✓" : "Copy dispute template"}
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {findings.length > VISIBLE && (
            <button type="button" className="secondary-button show-all-toggle" onClick={() => setShowAll((v) => !v)}>
              {showAll ? `Show top ${VISIBLE}` : `Show all ${findings.length} findings`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
