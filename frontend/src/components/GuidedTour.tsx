import { useEffect, useRef, useState } from "react";

interface Props {
  // Only makes sense to offer once there's an actual escalated case on screen to point at --
  // no fabricated data, no tour steps aimed at an empty list.
  hasEscalations: boolean;
  // Bumped by the parent each time an escalation is resolved -- lets step 2 auto-advance to step
  // 3 the moment the user does the thing being demonstrated, instead of making them click "Next"
  // after already taking the action.
  resolveSignal: number;
}

const STORAGE_KEY = "src-guided-tour-dismissed";

const STEPS: Array<{ targetId: string; title: string; body: string }> = [
  {
    targetId: "tour-first-escalation",
    title: "1. An escalated case, fully explained",
    body: "This case wasn't guessed on. Category, confidence, and reasoning are all real values from this run — and if you expand \"tool calls\" above the Resolve button, that's the exact trace of what the narrator checked before deciding it needed a human.",
  },
  {
    targetId: "tour-resolve-button",
    title: "2. Resolve it against source records",
    body: "Click Resolve on the highlighted case to confirm or correct it. That confirmation becomes one more distinct, real data point feeding this category's calibration below.",
  },
  {
    targetId: "calibration-panel",
    title: "3. Watch the dial learn",
    body: "N (sample count) and the confidence interval for that category just moved. Keep resolving cases across runs and watch a category's bar cross the auto-resolve threshold — that's trust being earned from evidence, not a knob someone turned.",
  },
];

function readDismissed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function GuidedTour({ hasEscalations, resolveSignal }: Props) {
  const [active, setActive] = useState(false);
  const [step, setStep] = useState(0);
  const [dismissedForGood, setDismissedForGood] = useState(readDismissed);
  const resolveSnapshotRef = useRef<number | null>(null);

  // Highlight whatever this step points at, and clear the highlight when the step changes or the
  // tour closes -- never leave a stray glow on the page.
  useEffect(() => {
    if (!active) return;
    const el = document.getElementById(STEPS[step].targetId);
    if (!el) return;
    el.classList.add("tour-highlight");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    return () => el.classList.remove("tour-highlight");
  }, [active, step]);

  // On entering step 2 (the "click Resolve" step), snapshot the current resolve count; if it
  // ticks up while still on this step, the user just did the thing -- jump ahead automatically.
  useEffect(() => {
    if (active && step === 1) {
      resolveSnapshotRef.current = resolveSignal;
    }
  }, [active, step]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (active && step === 1 && resolveSnapshotRef.current !== null && resolveSignal > resolveSnapshotRef.current) {
      setStep(2);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolveSignal]);

  const dismissForGood = () => {
    setActive(false);
    setDismissedForGood(true);
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* best-effort only -- a private/blocked storage context just means the hint reappears next visit */
    }
  };

  if (!hasEscalations) return null;

  if (!active) {
    if (dismissedForGood) return null;
    return (
      <button type="button" className="tour-entry-pill" onClick={() => { setStep(0); setActive(true); }}>
        ▸ See how escalate → resolve → recalibrate works (30s)
      </button>
    );
  }

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  return (
    <div className="tour-card" role="dialog" aria-label="Guided tour">
      <div className="tour-card-eyebrow">
        <span>Guided tour · step {step + 1} of {STEPS.length}</span>
        <button type="button" className="tour-card-close" aria-label="Close tour" onClick={() => setActive(false)}>
          ×
        </button>
      </div>
      <div className="tour-card-title">{current.title}</div>
      <p className="tour-card-body">{current.body}</p>
      <div className="tour-card-footer">
        <div className="tour-dots">
          {STEPS.map((_, i) => (
            <span key={i} className={`tour-dot ${i === step ? "tour-dot-active" : ""}`} />
          ))}
        </div>
        <div className="tour-card-nav">
          {step > 0 && (
            <button type="button" className="secondary-button" onClick={() => setStep((s) => s - 1)}>
              Back
            </button>
          )}
          {!isLast && (
            <button type="button" onClick={() => setStep((s) => s + 1)}>
              Next
            </button>
          )}
          {isLast && (
            <button type="button" onClick={dismissForGood}>
              Got it
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
