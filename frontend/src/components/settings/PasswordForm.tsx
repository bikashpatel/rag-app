import { useId, useRef, useState, type FormEvent } from "react";
import { ApiError, changePassword } from "../../api/client";
import { AlertIcon } from "../ui/icons";
import PasswordInput from "../ui/PasswordInput";
import { ButtonSpinner } from "../ui/Spinner";

const MIN_PASSWORD_CHARS = 8;

type PasswordFormProps = {
  id: string;
  /** The password was changed (the server re-issued this browser's cookie and ended the other sessions). */
  onChanged: () => void;
  onCancel: () => void;
};

/** Change password, inline in Settings → Account: current, new and confirm, each with a show/hide toggle. */
export default function PasswordForm({ id, onChanged, onCancel }: PasswordFormProps) {
  const hintId = useId();
  const mismatchId = useId();
  const errorId = useId();
  const currentRef = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  /** The server refused the current password: that field is marked invalid until it changes. */
  const [currentWrong, setCurrentWrong] = useState(false);
  const [saving, setSaving] = useState(false);

  const tooShort = next.length > 0 && next.length < MIN_PASSWORD_CHARS;
  const mismatch = confirm.length > 0 && confirm !== next;
  const valid = current.length > 0 && next.length >= MIN_PASSWORD_CHARS && confirm === next;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      await changePassword(current, next);
      onChanged();
    } catch (err) {
      // The only 400 this form can get is a wrong current password (the env admin never sees it): go back to that
      // field. Otherwise focus stays on the submit button, which stays focusable while saving.
      const wrong = err instanceof ApiError && err.status === 400;
      setCurrentWrong(wrong);
      setError(err instanceof ApiError ? err.message : "Couldn't change the password. Please try again.");
      setSaving(false);
      if (wrong) {
        currentRef.current?.focus();
        currentRef.current?.select();
      }
    }
  }

  return (
    <form id={id} className="settings-inline-form" onSubmit={onSubmit} noValidate aria-label="Change password">
      <PasswordInput
        ref={currentRef}
        label="Current password"
        autoComplete="current-password"
        value={current}
        onChange={(event) => {
          setCurrent(event.target.value);
          setCurrentWrong(false);
        }}
        aria-invalid={currentWrong || undefined}
        aria-describedby={currentWrong ? errorId : undefined}
        autoFocus
        required
      />
      <div className="field">
        <PasswordInput
          label="New password"
          autoComplete="new-password"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          aria-invalid={tooShort || undefined}
          aria-describedby={hintId}
          required
        />
        <span id={hintId} className={tooShort ? "error-text" : "field-hint"}>
          {tooShort ? <AlertIcon size={13} /> : null}
          At least {MIN_PASSWORD_CHARS} characters.
        </span>
      </div>
      <div className="field">
        <PasswordInput
          label="Confirm new password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          aria-invalid={mismatch || undefined}
          aria-describedby={mismatch ? mismatchId : undefined}
          required
        />
        {mismatch ? (
          <span id={mismatchId} className="error-text">
            <AlertIcon size={13} />
            The passwords don't match.
          </span>
        ) : null}
      </div>
      {error ? (
        <p id={errorId} className="error-text" role="alert">
          <AlertIcon size={13} />
          {error}
        </p>
      ) : null}
      <div className="settings-actions">
        <button type="submit" className="btn btn-primary btn-sm" disabled={!valid} aria-disabled={saving || undefined} aria-busy={saving || undefined}>
          {saving ? <ButtonSpinner size={14} /> : null}
          Change password
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
