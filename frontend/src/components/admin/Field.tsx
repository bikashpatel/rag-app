import { useId, type ReactNode } from "react";
import { AlertCircleIcon } from "../ui/icons";
import SegmentedControl, { type SegmentedOption } from "../ui/SegmentedControl";
import Switch from "../ui/Switch";

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
          <AlertCircleIcon size={13} />
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

type SegmentedFieldProps<T extends string> = {
  label: string;
  hint?: ReactNode;
  value: T;
  options: readonly SegmentedOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
};

/** A visible label over a small segmented control (its radio group carries the same name), then the hint. */
export function SegmentedField<T extends string>({
  label,
  hint,
  value,
  options,
  onChange,
  disabled,
}: SegmentedFieldProps<T>) {
  const hintId = useId();
  return (
    <div className="admin-segmented">
      <span className="field-label" aria-hidden="true">
        {label}
      </span>
      <SegmentedControl
        label={label}
        value={value}
        options={options}
        onChange={onChange}
        disabled={disabled}
        size="sm"
        describedBy={hint ? hintId : undefined}
        className="admin-segmented-options"
      />
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

/** A labelled switch: the label and hint, then the switch on the right (the shared Switch). */
export function SwitchField({ label, hint, checked, disabled, onChange }: SwitchFieldProps) {
  return (
    <Switch
      className="admin-switch-field"
      label={label}
      description={hint}
      checked={checked}
      disabled={disabled}
      onChange={onChange}
    />
  );
}
