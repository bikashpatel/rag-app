import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AttachmentChips from "./AttachmentChips";
import type { AttachmentView } from "./useAttachments";

const views: AttachmentView[] = [
  { id: 1, name: "notes.md", state: { kind: "queued" }, documentId: null },
  { id: 2, name: "board-deck.pdf", state: { kind: "uploading", progress: 41.6 }, documentId: null },
  { id: 3, name: "forecast.xlsx", state: { kind: "indexing" }, documentId: 211 },
  { id: 4, name: "contract.docx", state: { kind: "ready" }, documentId: 212 },
  { id: 5, name: "scan.tiff", state: { kind: "failed", reason: ".tiff files aren't supported." }, documentId: null },
];

describe("AttachmentChips", () => {
  it("renders nothing without attachments", () => {
    const { container } = render(<AttachmentChips views={[]} onRemove={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists each file with its state", () => {
    render(<AttachmentChips views={views} onRemove={vi.fn()} />);
    const [queued, uploading, indexing, ready, failed] = within(
      screen.getByRole("list", { name: "Attached files" }),
    ).getAllByRole("listitem");
    expect(within(queued).getByText("Queued")).toBeInTheDocument();
    expect(within(uploading).getByText("Uploading 42%")).toBeInTheDocument();
    expect(within(indexing).getByText("Indexing…")).toBeInTheDocument();
    expect(within(ready).getByText("Ready")).toBeInTheDocument();
    expect(within(failed).getByText("Failed: .tiff files aren't supported.")).toBeInTheDocument();
    expect(within(failed).getByText("scan.tiff")).toBeInTheDocument();
  });

  it("labels each remove button by what it does and reports the chip's id", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(<AttachmentChips views={views} onRemove={onRemove} />);
    expect(screen.getByRole("button", { name: "Cancel uploading notes.md" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel uploading board-deck.pdf" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove scan.tiff" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove contract.docx from this message; it stays in your documents" }));
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(4);
    await user.click(screen.getByRole("button", { name: "Remove forecast.xlsx from this message; it stays in your documents" }));
    expect(onRemove).toHaveBeenLastCalledWith(3);
  });
});
