import { useId, useRef, useState, type FormEvent } from "react";
import { accountEmailApi, ApiError, authErrorCode, changePassword, passwordRefusal } from "../../api/client";
import type { PasswordReason } from "../../api/types";
import PasswordRules from "../auth/PasswordRules";
import { AlertIcon } from "../ui/icons";
import PasswordInput from "../ui/PasswordInput";
import { ButtonSpinner } from "../ui/Spinner";

type PasswordFormProps = {
  id: string;
  /** "change": Change password (current, new, confirm); "setup": a first password after Google's step-up. */
  mode?: "change" | "setup";
  /** The policy's minimum length (GET /auth/config). */
  minimum: number;
  /** Saved: changed (the server re-issued this browser's cookie and ended the other sessions), or set. */
  onChanged: () => void;
  onCancel: () => void;
  /** setup: the step-up grant ran out; Google's confirmation is needed again. */
  onStepUpNeeded?: () => void;
};

/**
 * Change password (or set a first one), inline in Settings → Account: the current password when changing, the new one
 * with the policy's rules (a refusal replaces the rule it broke), and the new one again; each with a show/hide toggle.
 */
export default function PasswordForm({
  id,
  mode = "change",
  minimum,
  onChanged,
  onCancel,
  onStepUpNeeded,
}: PasswordFormProps) {
  const rulesId = useId();
  const mismatchId = useId();
  const errorId = useId();
  const currentRef = useRef<HTMLInputElement>(null);
  const nextRef = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ reason: PasswordReason; message: string } | null>(null);
  /** The server refused the current password: that field is marked invalid until it changes. */
  const [currentWrong, setCurrentWrong] = useState(false);
  const [saving, setSaving] = useState(false);
  const changing = mode === "change";

  const mismatch = confirm.length > 0 && confirm !== next;
  const valid = (!changing || current.length > 0) && next.length > 0 && confirm === next;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!valid || saving) return;
    if (Array.from(next.normalize("NFC")).length < minimum) {
      setRefusal({ reason: "too_short", message: `Use at least ${minimum} characters.` });
      nextRef.current?.focus();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (changing) await changePassword(current, next);
      else await accountEmailApi.setupPassword({ new_password: next });
      onChanged();
    } catch (err) {
      setSaving(false);
      const rejected = passwordRefusal(err);
      if (rejected) {
        setRefusal(rejected);
        nextRef.current?.focus();
        return;
      }
      if (!changing && authErrorCode(err) === "reauth_required" && onStepUpNeeded) {
        onStepUpNeeded();
        return;
      }
      // Changing, the other 400 is a wrong current password (the env admin never sees this form): go back to that
      // field. Otherwise focus stays on the submit button, which stays focusable while saving.
      const wrong = changing && err instanceof ApiError && err.status === 400 && authErrorCode(err) === null;
      setCurrentWrong(wrong);
      setError(err instanceof ApiError ? err.message : "Couldn't save the password. Please try again.");
      if (wrong) {
        currentRef.current?.focus();
        currentRef.current?.select();
      }
    }
  }

  return (
    <form
      id={id}
      className="settings-inline-form"
      onSubmit={onSubmit}
      noValidate
      aria-label={changing ? "Change password" : "Set a password"}
    >
      {changing ? (
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
      ) : null}
      <div className="field">
        <PasswordInput
          ref={nextRef}
          label="New password"
          autoComplete="new-password"
          value={next}
          onChange={(event) => {
            setNext(event.target.value);
            setRefusal(null);
          }}
          aria-invalid={refusal ? true : undefined}
          aria-describedby={rulesId}
          autoFocus={!changing}
          required
        />
        <PasswordRules id={rulesId} password={next} minimum={minimum} rejected={refusal} />
      </div>
      <div className="field">
        <PasswordInput
          label={changing ? "Confirm new password" : "Repeat it"}
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
        <button
          type="submit"
          className="btn btn-primary btn-sm"
          disabled={!valid}
          aria-disabled={saving || undefined}
          aria-busy={saving || undefined}
        >
          {saving ? <ButtonSpinner size={14} /> : null}
          {changing ? "Change password" : "Save password"}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
