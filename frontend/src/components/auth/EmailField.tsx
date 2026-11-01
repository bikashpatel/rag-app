import { useRef, useState, type ReactNode } from "react";
import { suggestEmail } from "../../lib/emailTypos";
import { AlertCircleIcon } from "../ui/icons";
import "./auth-components.css";

/**
 * An email input with its label, hint and error, and, after the field is left, "Did you mean …?" for a common
 * domain typo: one tap puts the suggestion in the field. It never blocks. The input is named `email`.
 */
export default function EmailField({
  id,
  label,
  value,
  onChange,
  error = null,
  hint,
  autoFocus = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange(value: string): void;
  error?: string | null;
  hint?: ReactNode;
  autoFocus?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  /** The suggestion for the value the field had when it was last left. */
  const [checked, setChecked] = useState<{ value: string; suggestion: string | null } | null>(null);
  const suggestion = checked?.value === value ? checked.suggestion : null;
  const ids = { suggestion: `${id}-suggestion`, hint: `${id}-hint`, error: `${id}-error` };
  const describedBy = [suggestion && ids.suggestion, hint && ids.hint, error && ids.error].filter(Boolean).join(" ");

  function apply(address: string) {
    onChange(address);
    setChecked(null);
    input.current?.focus();
  }

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        ref={input}
        id={id}
        className="input"
        type="email"
        name="email"
        autoComplete="email"
        autoCapitalize="none"
        spellCheck={false}
        autoFocus={autoFocus}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onBlur={() => setChecked({ value, suggestion: suggestEmail(value) })}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
      />
      {suggestion ? (
        <p className="auth-suggest" id={ids.suggestion} role="status">
          Did you mean{" "}
          <button type="button" className="auth-suggest-apply" onClick={() => apply(suggestion)}>
            {suggestion}
          </button>
          ?
        </p>
      ) : null}
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
