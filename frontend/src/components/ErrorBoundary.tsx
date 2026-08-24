import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

// A top-level render-error safety net (an external audit 2026-08-24 flagged its absence): every
// typed FastAPI endpoint validates its response through a pydantic model before serializing, so a
// malformed-but-200 API response is structurally hard to produce today -- but /api/transactions/
// evaluate is the one endpoint that accepts free-form judge input, and defense-in-depth here costs
// nothing. React only supports this via a class component; there's no hook equivalent.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Unhandled render error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="panel">
          <h2>Something went wrong</h2>
          <p className="error-text" role="alert">
            {this.state.error.message}
          </p>
          <p className="panel-sub">Reload the page to continue. This didn't affect any data already saved on the backend.</p>
        </div>
      );
    }
    return this.props.children;
  }
}
