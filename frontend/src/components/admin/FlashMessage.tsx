import { AlertCircleIcon, CheckCircleIcon, CloseIcon } from "../ui/icons";
import type { Flash } from "./hooks";

/** Live region for action outcomes; always mounted so screen readers announce new messages. */
export default function FlashMessage({ flash }: { flash: Flash }) {
  const { message } = flash;
  return (
    <div className="admin-flash-region" role="status" aria-live="polite">
      {message ? (
        <div
          key={message.id}
          className={`ui-notice ${message.tone === "success" ? "ui-notice-success" : "ui-notice-danger"} admin-flash`}
        >
          {message.tone === "success" ? <CheckCircleIcon size={15} /> : <AlertCircleIcon size={15} />}
          <span className="ui-notice-text">{message.text}</span>
          <button type="button" className="btn btn-ghost btn-icon" aria-label="Dismiss" onClick={flash.dismiss}>
            <CloseIcon size={15} />
          </button>
        </div>
      ) : null}
    </div>
  );
}
