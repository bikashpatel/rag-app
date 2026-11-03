import type { ReactNode, Ref } from "react";
import { AlertCircleIcon } from "../ui/icons";
import "./auth-components.css";

/** A person's name (lib/names checks it), with its label, hint and error. Required; the input is named `name`. */
export default function NameField({
  id,
  label,
  value,
  onChange,
  error = null,
  hint,
  autoFocus = false,
  ref,
}: {
  id: string;
  label: string;
  value: string;
  onChange(value: string): void;
  error?: string | null;
  hint?: ReactNode;
  autoFocus?: boolean;
  ref?: Ref<HTMLInputElement>;
}) {
  const ids = { hint: `${id}-hint`, error: `${id}-error` };
  const describedBy = [hint && ids.hint, error && ids.error].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        className="input"
        type="text"
        name="name"
        autoComplete="name"
        autoCapitalize="words"
        spellCheck={false}
        required
        autoFocus={autoFocus}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
      />
      {hint ? (
        <span className="field-hint" id={ids.hint}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <p className="error-text" id={ids.error} role="alert">
          <AlertCircleIcon size={13} />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}
