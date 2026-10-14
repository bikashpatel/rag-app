import type { Flash } from "./hooks";
import { AlertIcon, CheckIcon, CloseIcon } from "./icons";

/** Live region for action outcomes; always mounted so screen readers announce new messages. */
export default function FlashMessage({ flash }: { flash: Flash }) {
  const { message } = flash;
  return (
    <div className="admin-flash-region" role="status" aria-live="polite">
      {message ? (
        <div key={message.id} className={`admin-flash admin-flash-${message.tone}`}>
          {message.tone === "success" ? <CheckIcon size={16} /> : <AlertIcon size={16} />}
          <span className="admin-flash-text">{message.text}</span>
          <button type="button" className="btn btn-ghost btn-icon admin-flash-close" aria-label="Dismiss" onClick={flash.dismiss}>
            <CloseIcon size={14} />
          </button>
        </div>
      ) : null}
    </div>
  );
}
