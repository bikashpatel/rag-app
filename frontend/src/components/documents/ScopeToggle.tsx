import { useId } from "react";
import type { DocumentScope } from "../../api/types";

const OPTIONS: { value: DocumentScope; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "shared", label: "Shared knowledge base" },
];

type ScopeToggleProps = {
  value: DocumentScope;
  onChange: (scope: DocumentScope) => void;
};

/**
 * Where new uploads go (admins only): an "Upload to" label over a full-width small segmented control. Native radios,
 * so the arrow keys move and select; the selection shows by fill and weight.
 */
export default function ScopeToggle({ value, onChange }: ScopeToggleProps) {
  const groupName = useId();
  const labelId = useId();
  const hintId = useId();
  return (
    <div className="docs-scope">
      <span id={labelId} className="docs-scope-label">
        Upload to
      </span>
      <div
        className="ui-segmented ui-segmented-sm docs-segmented"
        role="radiogroup"
        aria-labelledby={labelId}
        aria-describedby={hintId}
      >
        {OPTIONS.map((option) => (
          <label key={option.value} className="ui-segment">
            <input
              type="radio"
              name={groupName}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
      <p id={hintId} className="docs-scope-hint">
        {value === "shared"
          ? "Every user can see and search shared documents."
          : "Only you can see and search your private documents."}
      </p>
    </div>
  );
}
