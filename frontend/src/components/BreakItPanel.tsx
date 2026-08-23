import { useState } from "react";
import { evaluateScenario } from "../api";
import { PRESETS } from "../presets";
import type { EvaluatedTransaction } from "../types";
import { categoryLabel } from "../formatters";

const DECISION_LABELS: Record<string, string> = {
  clean_pass1: "Clean (exact match)",
  auto_resolved_deterministic: "Auto-resolved (deterministic)",
  auto_resolved_calibrated: "Auto-resolved (calibrated)",
  escalated: "Escalated",
  needs_narration: "Needs narration",
};

const WARN_DECISIONS = new Set(["escalated", "needs_narration"]);

export function BreakItPanel() {
  const [json, setJson] = useState(() => JSON.stringify(PRESETS["Duplicate refund"], null, 2));
  const [results, setResults] = useState<EvaluatedTransaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadPreset = (name: string) => {
    setJson(JSON.stringify(PRESETS[name], null, 2));
    setResults(null);
    setError(null);
  };

  const submit = async () => {
    setLoading(true);
    setError(null);
    try {
      const parsed = JSON.parse(json);
      const resp = await evaluateScenario(parsed);
      setResults(resp.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setResults(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="panel">
      <h2>Break it — submit a scenario live</h2>
      <p className="panel-sub">
        Not a pre-generated batch — a hand-crafted (or judge-edited) scenario, evaluated through the exact same
        pipeline and calibration gate. Load a preset, tweak a number, resubmit.
      </p>
      <div className="preset-buttons">
        {Object.keys(PRESETS).map((name) => (
          <button key={name} type="button" className="secondary-button" onClick={() => loadPreset(name)}>
            {name}
          </button>
        ))}
      </div>
      <textarea className="scenario-textarea" value={json} onChange={(e) => setJson(e.target.value)} spellCheck={false} />
      <button disabled={loading} onClick={submit}>
        {loading ? "Evaluating…" : "Submit scenario"}
      </button>
      {error && <p className="error-text">{error}</p>}
      {results && (
        <ul className="escalation-list break-it-results">
          {results.map((r) => (
            <li key={r.transaction_id} className="escalation-item">
              <div className="escalation-header">
                <span className="badge badge-neutral">{r.transaction_id}</span>
                <span className={`badge ${WARN_DECISIONS.has(r.resolution) ? "badge-warn" : "badge-good"}`}>
                  {DECISION_LABELS[r.resolution] ?? r.resolution}
                </span>
                {r.category && <span className="badge badge-neutral">{categoryLabel(r.category)}</span>}
                {r.confidence != null && <span className="escalation-confidence">confidence {r.confidence.toFixed(2)}</span>}
              </div>
              {r.reasoning && <p className="escalation-reasoning">{r.reasoning}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
