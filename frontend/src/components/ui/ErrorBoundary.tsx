import { Component, type ReactNode } from "react";

type ErrorBoundaryProps = { children: ReactNode };
type ErrorBoundaryState = { failed: boolean };

/**
 * Last-resort boundary around the whole app. Without one, React unmounts the root on any render error (a failed
 * lazy chunk after a redeploy, a bug), leaving a blank page with no way forward but a manual reload.
 * The fallback uses no router or auth context, so it can sit outside both.
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="page-center">
        <div className="card stack status-card" role="alert">
          <h1>Something went wrong</h1>
          <p>The page hit an unexpected error. This can happen right after the app was updated; reloading usually fixes it.</p>
          <div className="status-card-actions">
            <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
              Reload
            </button>
            <button type="button" className="btn" onClick={() => window.location.assign("/")}>
              Back to chat
            </button>
          </div>
        </div>
      </div>
    );
  }
}
