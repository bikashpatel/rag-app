import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ButtonSpinner } from "../ui/Spinner";
import { errorMessage } from "./adminApi";

type ConfirmActionProps = {
  /** Content of the button that asks for confirmation. */
  label: ReactNode;
  /** Accessible name for the trigger when `label` alone is ambiguous (e.g. "Delete" in a table row). */
  ariaLabel?: string;
  message: ReactNode;
  confirmLabel: string;
  /** Rejecting keeps the confirmation open and shows the error message inline. */
  onConfirm: () => Promise<void>;
  tone?: "danger" | "primary";
  /** Defaults to a small ghost button (danger tone) or a small button (primary tone). */
  triggerClassName?: string;
  disabled?: boolean;
  title?: string;
  /** Rendered before the trigger, and hidden while confirming (e.g. a row's other actions). */
  children?: ReactNode;
};

export default function ConfirmAction({
  label,
  ariaLabel,
  message,
  confirmLabel,
  onConfirm,
  tone = "danger",
  triggerClassName,
  disabled,
  title,
  children,
}: ConfirmActionProps) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);
  const messageId = useId();

  useEffect(() => {
    if (asking) {
      cancelRef.current?.focus();
    } else if (returnFocus.current) {
      returnFocus.current = false;
      triggerRef.current?.focus();
    }
  }, [asking]);

  function close() {
    returnFocus.current = true;
    setAsking(false);
    setError(null);
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      close();
    } catch (err) {
      setError(errorMessage(err, "That didn't work. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <>
        {children}
        <button
          ref={triggerRef}
          type="button"
          className={triggerClassName ?? (tone === "danger" ? "btn btn-ghost btn-sm admin-text-danger" : "btn btn-sm")}
          aria-label={ariaLabel}
          disabled={disabled}
          title={title}
          onClick={() => setAsking(true)}
        >
          {label}
        </button>
      </>
    );
  }

  return (
    <div
      className={`admin-confirm admin-confirm-${tone}`}
      role="group"
      aria-labelledby={messageId}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !busy) {
          e.stopPropagation();
          close();
        }
      }}
    >
      <span id={messageId} className="admin-confirm-message">
        {message}
      </span>
      <span className="admin-confirm-buttons">
        <button
          type="button"
          className={`btn btn-sm ${tone === "danger" ? "btn-danger" : "btn-primary"}`}
          disabled={busy}
          aria-busy={busy || undefined}
          onClick={() => void confirm()}
        >
          {busy ? <ButtonSpinner size={12} /> : null}
          {confirmLabel}
        </button>
        <button ref={cancelRef} type="button" className="btn btn-sm" disabled={busy} onClick={close}>
          Cancel
        </button>
      </span>
      {error ? (
        <span className="admin-confirm-error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
