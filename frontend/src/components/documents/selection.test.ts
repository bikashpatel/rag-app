import { describe, expect, it } from "vitest";
import { makeDocument } from "../../test/fixtures";
import {
  countSelected,
  isDocumentSelected,
  pruneSelection,
  readyDocumentIds,
  selectionState,
  selectionSummary,
  toggleDocument,
} from "./selection";

const ready = makeDocument({ id: 1 });
const alsoReady = makeDocument({ id: 2, filename: "plan.docx" });
const indexing = makeDocument({ id: 3, status: "processing" });
const failed = makeDocument({ id: 4, status: "failed", error: "No text found." });
const documents = [ready, alsoReady, indexing, failed];
const readyIds = readyDocumentIds(documents);

describe("document selection", () => {
  it("only counts ready documents as searchable", () => {
    expect(readyIds).toEqual([1, 2]);
    expect(isDocumentSelected(null, ready)).toBe(true);
    expect(isDocumentSelected(null, indexing)).toBe(false);
    expect(isDocumentSelected([3], indexing)).toBe(false);
    expect(isDocumentSelected([2], ready)).toBe(false);
  });

  it("turns 'all' into the other ready documents when one is unchecked", () => {
    expect(toggleDocument(null, 1, readyIds)).toEqual([2]);
  });

  it("removes a checked document from an explicit list", () => {
    expect(toggleDocument([1, 2], 2, readyIds)).toEqual([1]);
  });

  it("collapses back to 'all' when the last unchecked document is checked", () => {
    expect(toggleDocument([2], 1, readyIds)).toBeNull();
    expect(toggleDocument([], 1, [1, 2])).toEqual([1]);
  });

  it("counts and classifies the selection", () => {
    expect(countSelected(null, readyIds)).toBe(2);
    expect(countSelected([2, 3, 99], readyIds)).toBe(1);
    expect(selectionState(0, 2)).toBe("none");
    expect(selectionState(1, 2)).toBe("some");
    expect(selectionState(2, 2)).toBe("all");
  });

  it.each([
    [[], 0, 2, "Document search off"],
    [null, 0, 0, "No documents ready to search yet"],
    [null, 1, 1, "Searching 1 document"],
    [null, 1200, 1200, `Searching all ${new Intl.NumberFormat().format(1200)} documents`],
    [[1], 1, 1, "Searching 1 of 1 document"],
    [[1], 1, 3, "Searching 1 of 3 documents"],
  ] as const)("summarises %j (%i of %i ready)", (selection, selected, total, summary) => {
    expect(selectionSummary(selection === null ? null : [...selection], selected, total)).toBe(summary);
  });

  it("prunes ids that are no longer listed, keeping the array when nothing changed", () => {
    const selection = [1, 2];
    expect(pruneSelection(selection, documents)).toBe(selection);
    expect(pruneSelection([1, 9], documents)).toEqual([1]);
  });
});
