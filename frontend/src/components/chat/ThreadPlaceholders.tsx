import { AlertCircleIcon, RetryIcon } from "./icons";

export function ThreadLoading() {
  return (
    <div className="chat-thread chat-thread-placeholder" aria-busy="true">
      <span className="sr-only" role="status">
        Loading conversation
      </span>
      <div className="chat-messages" aria-hidden="true">
        <div className="ui-skeleton chat-skeleton-user" />
        <div className="chat-skeleton-group">
          <div className="ui-skeleton" />
          <div className="ui-skeleton" style={{ width: "92%" }} />
          <div className="ui-skeleton chat-skeleton-short" />
        </div>
        <div className="ui-skeleton chat-skeleton-user chat-skeleton-user-short" />
        <div className="chat-skeleton-group">
          <div className="ui-skeleton" style={{ width: "86%" }} />
          <div className="ui-skeleton chat-skeleton-short" />
        </div>
      </div>
    </div>
  );
}

export function ThreadError({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <div className="chat-thread chat-thread-placeholder">
      <div className="chat-thread-error" role="alert">
        <span className="chat-thread-error-icon" aria-hidden="true">
          <AlertCircleIcon size={22} />
        </span>
        <h2>Couldn't load this conversation</h2>
        {message ? <p>{message}</p> : null}
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          <RetryIcon size={16} />
          Try again
        </button>
      </div>
    </div>
  );
}
