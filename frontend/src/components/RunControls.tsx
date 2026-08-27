import { useEffect, useRef, useState } from "react";
import type { RunRequest } from "../api";

interface Props {
  onRun: (req: RunRequest) => void;
  loading: boolean;
  error: string | null;
}

// Below this, "Running…" alone is enough -- above it, a user watching a static button with no
// other feedback has no way to tell "still working" from "hung", which is exactly the mistake a
// real dev on this project made once with a live Ollama run (see BUILD_LOG.md's "hang, chased
// carefully, that turned out not to exist" entry) -- now client-facing instead of just a
// developer's own confusion. A real groq run can legitimately take many minutes on the free tier.
const EXPECTATION_NOTE_THRESHOLD_SECONDS = 10;

export function RunControls({ onRun, loading, error }: Props) {
  const [seed, setSeed] = useState(42);
  // Smaller than this project's own internal dev-testing default (120/40) on purpose: that size
  // is what took 11-70 minutes on Groq's free tier (see BUILD_LOG.md), a bad first impression for
  // anyone picking "groq" from curiosity without knowing that. ~30/10 still exercises every
  // category and is fast on every provider; anyone who wants a bigger run can just type a bigger
  // number here.
  const [mainN, setMainN] = useState(30);
  const [stressN, setStressN] = useState(10);
  const [provider, setProvider] = useState("mock");
  const [resetHistory, setResetHistory] = useState(false);
  const [enableDiscovery, setEnableDiscovery] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (loading) {
      setElapsedSeconds(0);
      const startedAt = Date.now();
      intervalRef.current = setInterval(() => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    } else if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [loading]);

  return (
    <section className="panel run-controls">
      <h2>Run a batch</h2>
      <div className="run-controls-grid">
        <label>
          Seed
          <div className="seed-input-row">
            <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
            <button
              type="button"
              className="secondary-button"
              title="Pick a fresh random seed — proves this isn't replaying the same fixed batch every time"
              onClick={() => setSeed(Math.floor(Math.random() * 1_000_000))}
            >
              Randomize
            </button>
          </div>
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
            <option value="ollama">ollama (local qwen2.5:7b, zero cost, zero rate limit)</option>
            <option value="groq">groq (gpt-oss-20b, needs GROQ_API_KEY, rate-limited)</option>
          </select>
        </label>
        <label className="checkbox-label">
          <input type="checkbox" checked={resetHistory} onChange={(e) => setResetHistory(e.target.checked)} />
          Reset calibration history
        </label>
        <label className="checkbox-label" title="For each genuine_error case, ask the model to propose a named candidate category — never auto-adopted, shown on the escalation card for a human to review">
          <input type="checkbox" checked={enableDiscovery} onChange={(e) => setEnableDiscovery(e.target.checked)} />
          Propose new categories (genuine_error)
        </label>
        <button
          disabled={loading}
          onClick={() =>
            onRun({
              seed,
              main_n: mainN,
              stress_n: stressN,
              threshold: 0.9,
              provider,
              reset_history: resetHistory,
              enable_discovery: enableDiscovery,
            })
          }
        >
          {loading ? `Running… ${elapsedSeconds}s` : "Run batch"}
        </button>
      </div>
      {loading && elapsedSeconds >= EXPECTATION_NOTE_THRESHOLD_SECONDS && (
        <p className="panel-sub run-still-going-note">
          Still working — this is expected, not a hang. Mock and Ollama runs typically finish in seconds to a couple
          minutes; Groq's free tier can take much longer on a full batch because of rate-limit backoff, not because
          anything is stuck.
        </p>
      )}
      {error && <p className="error-text">{error}</p>}
    </section>
  );
}
