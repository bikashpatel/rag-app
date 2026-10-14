import { useId, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import Modal from "../ui/Modal";
import Spinner from "../ui/Spinner";

const MIN_PASSWORD_CHARS = 8;

/** POST /auth/change-password. Mount it to open; the server signs out the user's other sessions. */
export default function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const formId = useId();
  const hintId = useId();
  const mismatchId = useId();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

  const tooShort = next.length > 0 && next.length < MIN_PASSWORD_CHARS;
  const mismatch = confirm.length > 0 && confirm !== next;
  const valid = current.length > 0 && next.length >= MIN_PASSWORD_CHARS && confirm === next;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      await api.post<void>("/auth/change-password", { current_password: current, new_password: next });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't change the password. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  const footer = done ? (
    // autoFocus: the focused submit button was just replaced, so focus would otherwise fall to <body>.
    <button type="button" className="btn btn-primary" onClick={onClose} autoFocus>
      Done
    </button>
  ) : (
    <>
      <button type="button" className="btn" onClick={onClose}>
        Cancel
      </button>
      <button type="submit" form={formId} className="btn btn-primary" disabled={!valid || saving}>
        {saving ? <Spinner size={14} label="Saving" /> : null}
        Change password
      </button>
    </>
  );

  return (
    <Modal open title="Change password" onClose={onClose} footer={footer} width={420}>
      {done ? (
        <p className="chat-modal-text" role="status">
          Your password was changed. Your other sessions have been signed out.
        </p>
      ) : (
        <form id={formId} className="stack" onSubmit={onSubmit} noValidate>
          <label className="field">
            <span className="field-label">Current password</span>
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span className="field-label">New password</span>
            <input
              className="input"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              aria-invalid={tooShort || undefined}
              aria-describedby={hintId}
              required
            />
            <span id={hintId} className={tooShort ? "field-hint chat-field-error" : "field-hint"}>
              At least {MIN_PASSWORD_CHARS} characters.
            </span>
          </label>
          <label className="field">
            <span className="field-label">Confirm new password</span>
            <input
              className="input"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              aria-invalid={mismatch || undefined}
              aria-describedby={mismatch ? mismatchId : undefined}
              required
            />
            {mismatch ? (
              <span id={mismatchId} className="field-hint chat-field-error">
                The passwords don't match.
              </span>
            ) : null}
          </label>
          {error ? (
            <div className="error-text" role="alert">
              {error}
            </div>
          ) : null}
        </form>
      )}
    </Modal>
  );
}
