import { useEffect, useId, useRef } from "react";
import type { DocumentItem, DocumentSelection } from "../../api/types";
import { countSelected, readyDocumentIds, selectionState, selectionSummary } from "./selection";

type SelectionBarProps = {
  documents: DocumentItem[];
  selection: DocumentSelection;
  onSelectionChange: (selection: DocumentSelection) => void;
};

/** Master "use in chat" checkbox (checked / indeterminate / unchecked) with a summary of what chat searches. */
export default function SelectionBar({ documents, selection, onSelectionChange }: SelectionBarProps) {
  const checkboxRef = useRef<HTMLInputElement>(null);
  const summaryId = useId();
  const readyIds = readyDocumentIds(documents);
  const selected = countSelected(selection, readyIds);
  const state = selectionState(selected, readyIds.length);
  const searchOff = selection !== null && selection.length === 0;

  // `indeterminate` has no HTML attribute; re-apply it after every render since a click clears it.
  useEffect(() => {
    if (checkboxRef.current) checkboxRef.current.indeterminate = state === "some";
  });

  return (
    <div className="docs-selectbar">
      <label className="docs-check">
        <input
          ref={checkboxRef}
          type="checkbox"
          checked={state === "all"}
          disabled={readyIds.length === 0}
          onChange={() => onSelectionChange(state === "all" ? [] : null)}
          aria-label="Use all documents in chat"
          aria-describedby={summaryId}
        />
      </label>
      <span id={summaryId} className={searchOff ? "docs-selectbar-summary docs-selectbar-off" : "docs-selectbar-summary"}>
        {selectionSummary(selection, selected, readyIds.length)}
      </span>
    </div>
  );
}
