import { useId, type ReactNode } from "react";

type SwitchProps = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  className?: string;
};

/** A labelled on/off switch (a checkbox with role="switch" on the .switch styles): the label block, then the switch. */
export default function Switch({ checked, onChange, label, description, disabled = false, className }: SwitchProps) {
  const id = useId();
  const descriptionId = `${id}-description`;
  return (
    <div className={className ? `ui-switch ${className}` : "ui-switch"}>
      <span className="ui-switch-text">
        <label htmlFor={id} className="ui-switch-label">
          {label}
        </label>
        {description ? (
          <span id={descriptionId} className="ui-switch-description">
            {description}
          </span>
        ) : null}
      </span>
      <span className="switch">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          aria-describedby={description ? descriptionId : undefined}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span aria-hidden="true" />
      </span>
    </div>
  );
}
