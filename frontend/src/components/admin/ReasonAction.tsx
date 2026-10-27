import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon } from "../ui/icons";
import { errorMessage } from "./adminApi";

type ReasonActionProps = {
  /** The trigger's visible text ("Revoke…") and accessible name when the text alone is ambiguous. */
  label: string;
  ariaLabel?: string;
  message: ReactNode;
  confirmLabel: string;
  /** Rejecting keeps the form open with the error inline. */
  onConfirm: (reason: string) => Promise<void>;
  tone?: "danger" | "primary";
  disabled?: boolean;
};

/**
 * An inline confirmation that asks for a reason (1 to 200 characters), for billing changes the audit trail records:
 * revoking a period, ending a complimentary plan. Escape or Cancel closes it and focus returns to the trigger. Not a
 * <form>: it sits inside the user form, so Enter in the reason confirms here instead of submitting that form.
 */
export default function ReasonAction({
  label,
  ariaLabel,
  message,
  confirmLabel,
  onConfirm,
  tone = "danger",
  disabled,
}: ReasonActionProps) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const returnFocus = useRef(false);
  const id = useId();

  useEffect(() => {
    if (asking) inputRef.current?.focus();
    else if (returnFocus.current) {
      returnFocus.current = false;
      triggerRef.current?.focus();
    }
  }, [asking]);

  function close() {
    returnFocus.current = true;
    setAsking(false);
    setReason("");
    setError(null);
  }

  async function submit() {
    if (busy) return;
    const text = reason.trim();
    if (!text) {
      setError("Give a reason: it's kept in the billing record.");
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onConfirm(text);
      close();
    } catch (err) {
      setError(errorMessage(err, "That didn't work. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <button
        ref={triggerRef}
        type="button"
        className={tone === "danger" ? "btn btn-ghost btn-xs admin-text-danger" : "btn btn-sm"}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setAsking(true)}
      >
        {label}
      </button>
    );
  }

  return (
    <div
      className={`admin-confirm admin-confirm-${tone} admin-reason`}
      role="group"
      aria-labelledby={`${id}-message`}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !busy) {
          e.stopPropagation();
          close();
        }
      }}
    >
      <span id={`${id}-message`} className="admin-confirm-message">
        {message}
      </span>
      <label className="sr-only" htmlFor={`${id}-reason`}>
        Reason
      </label>
      <input
        ref={inputRef}
        id={`${id}-reason`}
        className="input admin-reason-input"
        placeholder="Reason"
        maxLength={200}
        autoComplete="off"
        value={reason}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(e) => setReason(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void submit();
          }
        }}
      />
      <span className="admin-confirm-buttons">
        <button
          type="button"
          className={`btn btn-sm ${tone === "danger" ? "btn-danger" : "btn-primary"}`}
          disabled={busy}
          aria-busy={busy || undefined}
          onClick={() => void submit()}
        >
          {busy ? <ButtonSpinner size={12} /> : null}
          {confirmLabel}
        </button>
        <button type="button" className="btn btn-sm" disabled={busy} onClick={close}>
          Cancel
        </button>
      </span>
      {error ? (
        <span id={`${id}-error`} className="admin-confirm-error" role="alert">
          <AlertCircleIcon size={13} />
          {error}
        </span>
      ) : null}
    </div>
  );
}
