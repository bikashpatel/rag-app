import { AlertIcon, RetryIcon } from "./icons";

export function ThreadLoading() {
  return (
    <div className="chat-thread chat-thread-placeholder" aria-busy="true">
      <span className="sr-only" role="status">
        Loading conversation
      </span>
      <div className="chat-messages" aria-hidden="true">
        <div className="chat-skeleton chat-skeleton-user" />
        <div className="chat-skeleton-group">
          <div className="chat-skeleton" />
          <div className="chat-skeleton" />
          <div className="chat-skeleton chat-skeleton-short" />
        </div>
        <div className="chat-skeleton chat-skeleton-user" />
        <div className="chat-skeleton-group">
          <div className="chat-skeleton" />
          <div className="chat-skeleton chat-skeleton-short" />
        </div>
      </div>
    </div>
  );
}

export function ThreadError({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <div className="chat-thread chat-thread-placeholder">
      <div className="chat-thread-error" role="alert">
        <AlertIcon size={22} />
        <h2>Couldn't load this conversation</h2>
        {message ? <p>{message}</p> : null}
        <button type="button" className="btn" onClick={onRetry}>
          <RetryIcon size={14} />
          Try again
        </button>
      </div>
    </div>
  );
}
