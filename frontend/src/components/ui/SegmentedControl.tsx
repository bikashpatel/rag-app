import { useId, type ReactNode } from "react";

export type SegmentedOption<T extends string> = { value: T; label: string; icon?: ReactNode };

type SegmentedControlProps<T extends string> = {
  /** The group's accessible name. */
  label: string;
  value: T;
  options: readonly SegmentedOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  /** "sm": 28 px segments (the usage chart's metric switch). */
  size?: "md" | "sm";
  /** Ids of hints that describe the group. */
  describedBy?: string;
  className?: string;
};

/**
 * A radio group drawn as segments (theme, chat font size, chart metric). Native radios give it the radio keyboard
 * model: Tab reaches the selected segment, the arrow keys move and select. Selection shows by fill and weight.
 */
export default function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled = false,
  size = "md",
  describedBy,
  className,
}: SegmentedControlProps<T>) {
  const name = useId();
  const classes = ["ui-segmented", size === "sm" ? "ui-segmented-sm" : null, className].filter(Boolean).join(" ");
  return (
    <div className={classes} role="radiogroup" aria-label={label} aria-describedby={describedBy} aria-disabled={disabled || undefined}>
      {options.map((option) => (
        <label key={option.value} className="ui-segment">
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={option.value === value}
            disabled={disabled}
            onChange={() => onChange(option.value)}
          />
          <span>
            {option.icon}
            {option.label}
          </span>
        </label>
      ))}
    </div>
  );
}
