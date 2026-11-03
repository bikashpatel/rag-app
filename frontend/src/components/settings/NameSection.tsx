import { useId, useRef, useState, type FormEvent } from "react";
import { ApiError, setDisplayName } from "../../api/client";
import type { AccountInfo } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { nameProblem, nameRefusal, normalizeName } from "../../lib/names";
import NameField from "../auth/NameField";
import { AlertIcon, CheckCircleIcon, PenIcon } from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";

type NameFormProps = { id: string; current: string | null; onSaved: (name: string) => void; onCancel: () => void };

/** The name as the person writes it: checked here as the server checks it, saved with PUT /me/name. */
function NameForm({ id, current, onSaved, onCancel }: NameFormProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(current ?? "");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    const problem = nameProblem(value);
    setFieldError(problem);
    setError(null);
    if (problem) {
      inputRef.current?.focus();
      return;
    }
    const name = normalizeName(value);
    if (name === current) {
      onCancel();
      return;
    }
    setSaving(true);
    try {
      onSaved((await setDisplayName({ name })).display_name);
    } catch (err) {
      setSaving(false);
      const refusal = err instanceof ApiError && err.status === 422 ? nameRefusal(err.message, value) : null;
      if (refusal) {
        setFieldError(refusal);
        inputRef.current?.focus();
      } else {
        setError(err instanceof ApiError ? err.message : "Couldn't save your name. Please try again.");
      }
    }
  }

  return (
    <form
      id={id}
      className="settings-inline-form"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
      aria-label={current ? "Change your name" : "Add your name"}
    >
      <NameField
        ref={inputRef}
        id={`${id}-name`}
        label="Name"
        value={value}
        onChange={(next) => {
          setValue(next);
          setFieldError(null);
        }}
        error={fieldError}
        autoFocus
      />
      {error ? (
        <p className="error-text" role="alert">
          <AlertIcon size={13} />
          {error}
        </p>
      ) : null}
      <div className="settings-actions">
        <button type="submit" className="btn btn-primary btn-sm" aria-busy={saving || undefined}>
          {saving ? <ButtonSpinner size={14} /> : null}
          Save
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** Settings → Account → Name: the person's name, shown in the greeting and the account menu; any account sets it. */
export default function NameSection({ account, onChanged }: { account: AccountInfo; onChanged: () => void }) {
  const { refresh } = useAuth();
  const formId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<{ name: string; at: number } | null>(null);
  const name = saved?.name ?? account.display_name;

  function close() {
    setOpen(false);
    toggleRef.current?.focus();
  }

  return (
    <div className="settings-group">
      <div className="settings-row settings-row-wrap">
        <div className="settings-label">
          <h3>Name</h3>
          <p className={name ? "settings-name" : "settings-name settings-name-none"}>{name ?? "Not set"}</p>
          <p className="settings-desc">How the app greets you. It's also the name on receipts unless you set another.</p>
        </div>
        <div className="settings-actions">
          {saved && !open ? (
            <span key={saved.at} className="settings-saved" role="status">
              <CheckCircleIcon size={15} />
              Name saved
            </span>
          ) : null}
          <button
            ref={toggleRef}
            type="button"
            className="btn btn-sm"
            aria-expanded={open}
            aria-controls={open ? formId : undefined}
            onClick={() => {
              setSaved(null);
              setOpen((value) => !value);
            }}
          >
            <PenIcon size={15} />
            {name ? "Edit name…" : "Add name…"}
          </button>
        </div>
      </div>
      {open ? (
        <NameForm
          id={formId}
          current={name}
          onSaved={(value) => {
            setSaved({ name: value, at: Date.now() });
            close();
            onChanged();
            void refresh().catch(() => undefined);
          }}
          onCancel={close}
        />
      ) : null}
    </div>
  );
}
