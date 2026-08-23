import { useState } from "react";
import type { RunRequest } from "../api";

interface Props {
  onRun: (req: RunRequest) => void;
  loading: boolean;
  error: string | null;
}

export function RunControls({ onRun, loading, error }: Props) {
  const [seed, setSeed] = useState(42);
  const [mainN, setMainN] = useState(120);
  const [stressN, setStressN] = useState(40);
  const [provider, setProvider] = useState("mock");
  const [resetHistory, setResetHistory] = useState(false);

  return (
    <section className="panel run-controls">
      <h2>Run a batch</h2>
      <div className="run-controls-grid">
        <label>
          Seed
          <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
        </label>
        <label>
          Main batch size
          <input type="number" min={10} value={mainN} onChange={(e) => setMainN(Number(e.target.value))} />
        </label>
        <label>
          Stress batch size
          <input type="number" min={0} value={stressN} onChange={(e) => setStressN(Number(e.target.value))} />
        </label>
        <label>
          Narrator provider
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="mock">mock (zero-cost, deterministic)</option>
            <option value="groq">groq (Llama 3.3, needs GROQ_API_KEY)</option>
          </select>
        </label>
        <label className="checkbox-label">
          <input type="checkbox" checked={resetHistory} onChange={(e) => setResetHistory(e.target.checked)} />
          Reset calibration history
        </label>
        <button
          disabled={loading}
          onClick={() => onRun({ seed, main_n: mainN, stress_n: stressN, threshold: 0.9, provider, reset_history: resetHistory })}
        >
          {loading ? "Running…" : "Run batch"}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </section>
  );
}
