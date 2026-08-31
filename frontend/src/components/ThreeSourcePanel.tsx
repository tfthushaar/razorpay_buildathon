import { useState } from "react";
import { evaluateThreeSource, type ThreeSourceResult } from "../api";
import { pct } from "../formatters";

/**
 * Three-source matching, run live.
 *
 * This is the strongest technical result in the project and until now it was reachable only by
 * running an evidence script, which meant a reader had to take RESULTS.md's word for it. Same code
 * the script uses, so the numbers agree.
 *
 * Held-out phrasing is the default because that is the condition the result rests on. On phrasing
 * the regex author saw, the regex wins; shipping that as the default would be choosing the demo over
 * the finding, so the toggle starts on the honest side and says what flipping it does.
 */

const LABELS: Record<string, string> = {
  no_cycle_parsing: "No cycle parsing",
  estimated_weights_no_cycle: "Estimated weights, no cycle",
  regex_cycle_parser: "Best regex cycle parser",
  model_cycle_reader: "Model cycle reader",
};

export function ThreeSourcePanel() {
  const [heldOut, setHeldOut] = useState(true);
  const [data, setData] = useState<ThreeSourceResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(useHeldOut: boolean) {
    setBusy(true);
    setError(null);
    try {
      setData(await evaluateThreeSource(useHeldOut));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>Three-source matching</h2>
      <p className="pitch-line">
        A settlement report, a bank statement and an ERP ledger joined on nothing reliable. The hard case is two
        payouts to the same merchant, same amount, same day: every structured field stops discriminating at once,
        and only the free-text cycle reference is left.
      </p>

      <div className="control-row">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={heldOut}
            onChange={(e) => {
              setHeldOut(e.target.checked);
              setData(null);
            }}
          />
          Held-out phrasing
        </label>
        <button className="button" disabled={busy} onClick={() => run(heldOut)}>
          {busy ? "Running…" : "Run the comparison"}
        </button>
      </div>
      <p className="hint-text">
        {heldOut
          ? "Cycle references phrased in words the regex author never saw. This is the condition the result rests on."
          : "Cycle references phrased the way the regex was written for. The regex wins here, which is the point of the other setting."}
      </p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {data && (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>Cycle reader</th>
                <th>Correct</th>
                <th>Accuracy</th>
                <th>Under-determined</th>
                <th>vs. regex (paired)</th>
              </tr>
            </thead>
            <tbody>
              {data.columns.map((c) => {
                const paired = data.mcnemar_vs_regex[c.column];
                return (
                  <tr key={c.column}>
                    <td>{LABELS[c.column] ?? c.column}</td>
                    <td className="numeric">
                      {c.correct}/{c.total}
                    </td>
                    <td className="numeric">{pct(c.accuracy)}</td>
                    <td className="numeric">{c.under_determined}</td>
                    <td className="numeric">
                      {paired ? (
                        <>
                          {paired.wins}W / {paired.losses}L, p = {paired.p}
                        </>
                      ) : (
                        <span className="muted">baseline</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="hint-text">
            {data.n_settlements} settlements against {data.n_bank_rows} bank rows, seed {data.seed}. Every column
            scored the identical settlements, so the tests are paired rather than a comparison of two independent
            intervals. The true bank row was reachable in {data.columns[0]?.reachable}/{data.n_settlements} cases, which
            caps every column equally.
          </p>
          <p className="hint-text">
            The model column needs a real provider and one call per candidate pair, so it is left to
            <code> scripts/generate_three_source_evidence.py</code> rather than fired from a web request.
          </p>
        </>
      )}
    </section>
  );
}
