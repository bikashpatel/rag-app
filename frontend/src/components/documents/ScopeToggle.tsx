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

/** Segmented control (native radios, so arrow keys work) choosing where new uploads go. */
export default function ScopeToggle({ value, onChange }: ScopeToggleProps) {
  const groupName = useId();
  const labelId = useId();
  return (
    <div className="docs-scope">
      <span id={labelId} className="docs-scope-label">
        Upload to
      </span>
      <div className="docs-segmented" role="radiogroup" aria-labelledby={labelId}>
        {OPTIONS.map((option) => (
          <label key={option.value} className="docs-segment">
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
      <p className="docs-scope-hint">
        {value === "shared"
          ? "Every user can see and search shared documents."
          : "Only you can see and search your private documents."}
      </p>
    </div>
  );
}
