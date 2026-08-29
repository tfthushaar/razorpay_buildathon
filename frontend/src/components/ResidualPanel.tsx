import type { ResidualReport } from "../types";

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const rupees = (paise: number) =>
  `${paise < 0 ? "-" : "+"}₹${Math.abs(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * The residual funnel, shown as a funnel, because the shape is the argument.
 *
 * The point this panel exists to make is structural rather than statistical: a case the
 * deterministic layer could solve was taken BY the deterministic layer, so it cannot be sitting
 * inside a model's accuracy figure inflating it. Everything the model is measured on here is
 * something the resolver demonstrably could not finish, and for the under-determined half we can say
 * exactly how unfinishable it was — k valid explanations means blind choice scores exactly 1/k.
 */
export function ResidualPanel({ residual }: { residual: ResidualReport }) {
  const underDetermined = residual.cases.filter((c) => c.status === "UNDER_DETERMINED");
  const worst = [...underDetermined].sort((a, b) => b.ambiguity - a.ambiguity)[0];

  return (
    <section className="panel">
      <h2>Residual architecture — where the model is actually allowed to work</h2>
      <p className="panel-sub">
        The deterministic resolver runs <strong>first</strong> and keeps everything it can explain on its own. The model
        only ever sees what is left, so no number below can contain a case a rule could have handled.
      </p>

      <div className="residual-funnel">
        <div className="residual-step">
          <span className="residual-step-n">{residual.closed_before_stage}</span>
          <span className="residual-step-label">closed by the matching engine</span>
        </div>
        <div className="residual-arrow">→</div>
        <div className="residual-step">
          <span className="residual-step-n">{residual.total}</span>
          <span className="residual-step-label">exceptions reaching Layer 0</span>
        </div>
        <div className="residual-arrow">→</div>
        <div className="residual-step">
          <span className="residual-step-n">{residual.layer0_resolved}</span>
          <span className="residual-step-label">explained by Layer 0 alone, no model call</span>
        </div>
        <div className="residual-arrow">→</div>
        <div className="residual-step residual-step-model">
          <span className="residual-step-n">{residual.model_calls}</span>
          <span className="residual-step-label">handed to the model</span>
        </div>
      </div>

      <div className="scorecard-detail">
        <strong>{pct(residual.deterministic_share)}</strong> of the batch closed with no model call at all.{" "}
        <strong>{residual.under_determined}</strong> cases were under-determined — Layer 0 found two or more
        arithmetically valid explanations and had no basis left to choose — and <strong>{residual.unmatched}</strong>{" "}
        went unmatched, where it found none.
      </div>

      {underDetermined.length > 0 && (
        <p className="scorecard-detail">
          On the under-determined cases, blind choice among Layer 0's own valid answers scores exactly{" "}
          <strong>{pct(residual.mean_chance_baseline)}</strong> on average. That is a computed floor, not a strawman
          comparator — every accuracy claim about the model on this residual is measured against it.
          {worst && (
            <>
              {" "}
              The hardest case in this run had <strong>{worst.ambiguity}</strong> equally valid explanations for a delta
              of {rupees(worst.observed_delta)}.
            </>
          )}
        </p>
      )}

      {residual.cases.length > 0 && (
        <table className="data-table residual-table">
          <thead>
            <tr>
              <th>Transaction</th>
              <th>Layer 0</th>
              <th className="num">Valid answers</th>
              <th className="num">Chance</th>
              <th>Model's attribution</th>
              <th>Verified</th>
            </tr>
          </thead>
          <tbody>
            {residual.cases.slice(0, 12).map((c) => (
              <tr key={c.transaction_id}>
                <td className="mono">{c.transaction_id.slice(0, 18)}</td>
                <td>
                  <span className={`residual-status residual-status-${c.status.toLowerCase()}`}>{c.status.replace("_", "-").toLowerCase()}</span>
                </td>
                <td className="num">{c.status === "UNDER_DETERMINED" ? c.ambiguity : "—"}</td>
                <td className="num">{c.status === "UNDER_DETERMINED" ? pct(c.chance_baseline) : "—"}</td>
                <td>
                  {c.components.length === 0 ? (
                    <em>nothing survived verification</em>
                  ) : (
                    c.components.map((comp, i) => (
                      <div key={i} className="residual-component">
                        <span className="residual-cause">{comp.cause}</span> {rupees(comp.amount)}{" "}
                        <span className="residual-ref mono">{comp.evidence_ref}</span>
                      </div>
                    ))
                  )}
                </td>
                <td>
                  {c.verified ? (
                    <span className="stat-good">
                      ✓{c.reached_model && c.verify_rounds > 1 ? ` (round ${c.verify_rounds})` : ""}
                    </span>
                  ) : (
                    <span className="stat-bad">✗ escalated</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {residual.cases.length > 12 && <p className="panel-sub">Showing 12 of {residual.cases.length} residual cases.</p>}

      <p className="panel-sub">
        Every attribution above is checked by a deterministic verifier before it is accepted: the components must sum to
        the observed delta, and every citation must resolve to a real object in this batch whose actual properties
        support the amount claimed against it. A confident explanation citing a refund that does not exist, or one that
        exists for a different amount, is rejected rather than reported.
      </p>
    </section>
  );
}
