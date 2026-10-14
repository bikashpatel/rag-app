import type { DocumentItem, DocumentSelection } from "../../api/types";
import { formatNumber } from "../../lib/format";

export type SelectionState = "all" | "some" | "none";

export function readyDocumentIds(documents: readonly DocumentItem[]): number[] {
  return documents.filter((doc) => doc.status === "ready").map((doc) => doc.id);
}

/** A document is used in chat when it is ready and the selection includes it (null = every document). */
export function isDocumentSelected(selection: DocumentSelection, doc: DocumentItem): boolean {
  return doc.status === "ready" && (selection === null || selection.includes(doc.id));
}

/**
 * Toggle one ready document. Unchecking while everything is selected (null) produces the explicit list of the
 * other ready documents; checking the last unchecked one collapses the list back to null.
 */
export function toggleDocument(
  selection: DocumentSelection,
  docId: number,
  readyIds: readonly number[],
): DocumentSelection {
  if (selection === null) return readyIds.filter((id) => id !== docId);
  if (selection.includes(docId)) return selection.filter((id) => id !== docId);
  const next = [...selection, docId];
  return readyIds.every((id) => next.includes(id)) ? null : next;
}

export function countSelected(selection: DocumentSelection, readyIds: readonly number[]): number {
  if (selection === null) return readyIds.length;
  const chosen = new Set(selection);
  return readyIds.filter((id) => chosen.has(id)).length;
}

export function selectionState(selected: number, ready: number): SelectionState {
  if (selected === 0) return "none";
  return selected >= ready ? "all" : "some";
}

export function selectionSummary(selection: DocumentSelection, selected: number, ready: number): string {
  if (selection !== null && selection.length === 0) return "Document search off";
  if (ready === 0) return "No documents ready to search yet";
  if (selection === null) return ready === 1 ? "Searching 1 document" : `Searching all ${formatNumber(ready)} documents`;
  return `Searching ${formatNumber(selected)} of ${formatNumber(ready)} ${ready === 1 ? "document" : "documents"}`;
}

/** Drop ids that are no longer listed. Returns the same array when nothing changed. */
export function pruneSelection(selection: number[], documents: readonly DocumentItem[]): number[] {
  const listed = new Set(documents.map((doc) => doc.id));
  return selection.every((id) => listed.has(id)) ? selection : selection.filter((id) => listed.has(id));
}
