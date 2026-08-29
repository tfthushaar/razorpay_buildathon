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
  // Locally, default to the real provider so a first run shows the model reasoning rather than the
  // rule-only path. In a deployed build that is wrong: the hosted backend has no GPU and no local
  // model, so "ollama" is the one default guaranteed to fail or silently degrade for the first
  // person who clicks Run. VITE_DEFAULT_PROVIDER sets it per environment; the deployed build sets
  // it to mock, which is deterministic, instant, and honest about being the zero-LLM path.
  const [provider, setProvider] = useState(import.meta.env.VITE_DEFAULT_PROVIDER ?? "ollama");
  // Whether this build is running somewhere a local model can exist. The deployed build sets
  // VITE_DEFAULT_PROVIDER because its host cannot; that same signal drives the labels below.
  const hostCanRunOllama = (import.meta.env.VITE_DEFAULT_PROVIDER ?? "ollama") === "ollama";
  const [resetHistory, setResetHistory] = useState(false);
  const [enableDiscovery, setEnableDiscovery] = useState(false);
  const [enableMultiwayNetting, setEnableMultiwayNetting] = useState(false);
  const [enableHeldOutVariants, setEnableHeldOutVariants] = useState(false);
  const [enableNarrationExplained, setEnableNarrationExplained] = useState(false);
  const [enableCompoundDelta, setEnableCompoundDelta] = useState(false);
  const [heldOutAdvicePhrasing, setHeldOutAdvicePhrasing] = useState(false);
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
          {/* The labels have to follow the environment, not just the default. On the deployed build
              the host has no GPU and no local model, so ollama cannot run there at all -- leaving it
              marked "recommended" would send the first person who clicks straight into a failure. */}
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="ollama" disabled={!hostCanRunOllama}>
              {hostCanRunOllama
                ? "ollama (local qwen2.5:7b, zero cost, zero rate limit) — recommended"
                : "ollama — needs a local install, unavailable on the hosted demo"}
            </option>
            <option value="mock">
              {hostCanRunOllama
                ? "mock (zero-cost, deterministic, no LLM calls)"
                : "mock (zero-cost, deterministic, no LLM calls) — recommended here"}
            </option>
            <option value="groq">groq (gpt-oss-20b, needs GROQ_API_KEY, rate-limited)</option>
          </select>
        </label>
        <button
          className="run-button"
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
              enable_multiway_netting: enableMultiwayNetting,
              enable_held_out_variants: enableHeldOutVariants,
              enable_narration_explained: enableNarrationExplained,
              enable_compound_delta: enableCompoundDelta,
              held_out_advice_phrasing: heldOutAdvicePhrasing,
            })
          }
        >
          {loading ? `Running… ${elapsedSeconds}s` : "Run batch"}
        </button>
      </div>

      {/* Six technical toggles in a wrapping row was the first thing anyone saw, in the vocabulary of
          the modules rather than the job. They are all opt-in extras; none is needed for a first run.
          Folded behind a disclosure, grouped, and relabelled by what they let you SEE. */}
      <details className="advanced-options">
        <summary>Advanced: inject harder cases</summary>
        <p className="panel-sub">
          Off by default. Each one adds a specific kind of hard case to the batch so you can watch how the system
          handles it. Hover any label for what it does.
        </p>
        <div className="advanced-grid">
        <label className="checkbox-label" title="Wipe the accumulated per-category accuracy history before this run, so trust has to be re-earned from zero">
          <input type="checkbox" checked={resetHistory} onChange={(e) => setResetHistory(e.target.checked)} />
          Start trust from zero
        </label>
        <label className="checkbox-label" title="For each genuine_error case, ask the model to propose a named candidate category — never auto-adopted, shown on the escalation card for a human to review">
          <input type="checkbox" checked={enableDiscovery} onChange={(e) => setEnableDiscovery(e.target.checked)} />
          Name unexplained patterns
        </label>
        <label
          className="checkbox-label"
          title="Inject a real multi-way netting_trap case: 3+ transactions whose deltas cancel together, invisible to the pairwise rule by construction — a genuine judgment task, off by default so existing evidence/numbers stay valid"
        >
          <input type="checkbox" checked={enableMultiwayNetting} onChange={(e) => setEnableMultiwayNetting(e.target.checked)} />
          Add a 3-way netting trap
        </label>
        <label
          className="checkbox-label"
          title="Inject near-miss duplicate_refund/netting_trap cases -- same true category, but perturbed just enough that the exact-match rule can never confirm them, breaking the 'same author wrote the rule and the injector' problem the clean versions can't test"
        >
          <input type="checkbox" checked={enableHeldOutVariants} onChange={(e) => setEnableHeldOutVariants(e.target.checked)} />
          Add near-miss cases the rule can't confirm
        </label>
        <label
          className="checkbox-label"
          title="Inject a narration_explained case: a delta explained only by the settlement's own free-text remarks field -- no structured field or delta-arithmetic a rule could check at any scale records this"
        >
          <input type="checkbox" checked={enableNarrationExplained} onChange={(e) => setEnableNarrationExplained(e.target.checked)} />
          Add a case only the bank's free text explains
        </label>
        <label
          className="checkbox-label"
          title="Inject compound_delta cases and run the residual architecture: the deterministic resolver runs first over every exception and keeps everything it can explain on its own, and the model is handed only what is left -- cases where the resolver found two or more equally valid explanations and arithmetic has no basis left to choose between them"
        >
          <input type="checkbox" checked={enableCompoundDelta} onChange={(e) => setEnableCompoundDelta(e.target.checked)} />
          Add compound deltas, resolver-first
        </label>
        <label
          className="checkbox-label"
          title="Build the remittance advice from phrasing the keyword baseline's negation cues were never written against -- the honest test of whether reading generalises or was simply authored by the same person who wrote the parser"
        >
          <input
            type="checkbox"
            checked={heldOutAdvicePhrasing}
            disabled={!enableCompoundDelta}
            onChange={(e) => setHeldOutAdvicePhrasing(e.target.checked)}
          />
          Use bank wording the parser has never seen
        </label>
        </div>
      </details>
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
