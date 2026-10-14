import { useId, type ReactNode } from "react";

export type ControlProps = {
  id: string;
  "aria-invalid": true | undefined;
  "aria-describedby": string | undefined;
};

type FieldProps = {
  label: string;
  hint?: ReactNode;
  error?: string;
  className?: string;
  children: (control: ControlProps) => ReactNode;
};

/** Label, control, hint and error, wired together with ids for assistive tech. */
export function Field({ label, hint, error, className, children }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={className ? `field ${className}` : "field"}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children({ id, "aria-invalid": error ? true : undefined, "aria-describedby": describedBy })}
      {error ? (
        <span id={errorId} className="error-text">
          {error}
        </span>
      ) : null}
      {hint ? (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

type SwitchFieldProps = {
  label: string;
  hint?: ReactNode;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
};

export function SwitchField({ label, hint, checked, disabled, onChange }: SwitchFieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="admin-switch-field">
      <label className="switch">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          aria-describedby={hintId}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span aria-hidden="true" />
      </label>
      <div className="admin-switch-text">
        <label htmlFor={id} className="admin-switch-label">
          {label}
        </label>
        {hint ? (
          <span id={hintId} className="field-hint">
            {hint}
          </span>
        ) : null}
      </div>
    </div>
  );
}
