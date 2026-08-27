import { useState } from "react";
import { askSettlementQuestion } from "../api";
import type { QAAnswer } from "../types";

const SUGGESTED_QUESTIONS = [
  "Are there any duplicate refunds in this batch?",
  "Which transactions settled outside their SLA window?",
  "Why might a payout have come in short?",
];

interface HistoryEntry {
  question: string;
  answer: QAAnswer;
}

export function SettlementQA() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [expandedTraceIndex, setExpandedTraceIndex] = useState<number | null>(null);

  const submit = async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setError(null);
    try {
      const answer = await askSettlementQuestion(trimmed);
      setHistory((prev) => [{ question: trimmed, answer }, ...prev]);
      setQuestion("");
      setExpandedTraceIndex(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not answer that question.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="panel" id="settlement-qa-panel">
      <h2>Ask about this batch</h2>
      <p className="panel-sub">
        Free-text questions over the latest run's real chains — a separate agentic loop from the narrator, grounded
        the same way: every answer below is built from real tool calls against this batch, shown in the trace, not
        free-floating text.
      </p>

      <div className="preset-buttons">
        {SUGGESTED_QUESTIONS.map((q) => (
          <button key={q} type="button" className="secondary-button" disabled={loading} onClick={() => submit(q)}>
            {q}
          </button>
        ))}
      </div>

      <form
        className="qa-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          submit(question);
        }}
      >
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Which transactions look like netting traps?"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !question.trim()}>
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {history.length > 0 && (
        <ul className="qa-history">
          {history.map((entry, index) => {
            const isTraceOpen = expandedTraceIndex === index;
            return (
              <li key={index} className="qa-entry">
                <p className="qa-question">{entry.question}</p>
                <p className="qa-answer">{entry.answer.answer}</p>
                {entry.answer.cited_transaction_ids.length > 0 && (
                  <p className="qa-citations">
                    Cited:{" "}
                    {entry.answer.cited_transaction_ids.map((id, i) => (
                      <span key={id} className="mono">
                        {i > 0 ? ", " : ""}
                        {id}
                      </span>
                    ))}
                  </p>
                )}
                {entry.answer.tool_calls.length > 0 && (
                  <button
                    type="button"
                    className="link-button tool-call-toggle"
                    onClick={() => setExpandedTraceIndex(isTraceOpen ? null : index)}
                  >
                    {isTraceOpen ? "▾" : "▸"} {entry.answer.tool_calls.length} tool call
                    {entry.answer.tool_calls.length === 1 ? "" : "s"} — what it checked
                  </button>
                )}
                {isTraceOpen && entry.answer.tool_calls.length > 0 && (
                  <ul className="tool-call-list escalation-tool-calls">
                    {entry.answer.tool_calls.map((tc, i) => (
                      <li key={i}>
                        <span className="mono tool-call-name">{tc.tool}</span>
                        <span className="tool-call-args">args: {JSON.stringify(tc.arguments)}</span>
                        <span className="tool-call-result">result: {JSON.stringify(tc.result)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
