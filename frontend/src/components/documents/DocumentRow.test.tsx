import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { DocumentItem, GeneratedImageDetails } from "../../api/types";
import { makeDocument } from "../../test/fixtures";
import { deferred, stubFetch } from "../../test/http";
import DocumentRow from "./DocumentRow";

function renderRow(doc: DocumentItem, handlers: Partial<{ onDelete: () => Promise<void>; onReprocess: () => Promise<void> }> = {}) {
  const props = {
    onToggle: vi.fn(),
    onDelete: vi.fn(handlers.onDelete ?? (() => Promise.resolve())),
    onReprocess: vi.fn(handlers.onReprocess ?? (() => Promise.resolve())),
  };
  render(
    <ul>
      <DocumentRow doc={doc} checked={doc.status === "ready"} {...props} />
    </ul>,
  );
  return props;
}

const checkbox = (doc: DocumentItem) => screen.getByRole("checkbox", { name: `Use ${doc.filename} in chat` });

describe("DocumentRow", () => {
  it("shows a ready document without a status badge and lets it be used in chat", async () => {
    const user = userEvent.setup();
    const doc = makeDocument();
    const { onToggle } = renderRow(doc);
    expect(screen.getByText("quarterly-report.pdf")).toBeInTheDocument();
    expect(screen.getByText(/^471 KB, 42 chunks, 12 pages, /)).toBeInTheDocument();
    expect(screen.queryByText(/Queued|Indexing…|Failed|Needs re-index/)).not.toBeInTheDocument();
    expect(checkbox(doc)).toBeChecked();
    await user.click(checkbox(doc));
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Download quarterly-report.pdf" })).toHaveAttribute(
      "href",
      "/api/documents/101/download",
    );
  });

  it.each([
    ["pending", "Queued"],
    ["processing", "Indexing…"],
  ] as const)("shows a %s document as %s, not yet usable in chat", (status, badge) => {
    const doc = makeDocument({ status, num_chunks: 0 });
    renderRow(doc);
    expect(screen.getByText(badge)).toBeInTheDocument();
    expect(checkbox(doc)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Retry|Re-index/ })).not.toBeInTheDocument();
  });

  it("shows a failed document's error with Retry", async () => {
    const user = userEvent.setup();
    const doc = makeDocument({ status: "failed", error: "No text could be extracted.", num_chunks: 0 });
    const { onReprocess } = renderRow(doc);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("No text could be extracted.")).toBeInTheDocument();
    expect(checkbox(doc)).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onReprocess).toHaveBeenCalledWith(101);
  });

  it("offers no Retry on a failed document the user can't change", () => {
    renderRow(makeDocument({ status: "failed", error: null, can_delete: false, scope: "shared" }));
    expect(screen.getByText("Indexing failed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("flags a stale document as needing a re-index and starts one", async () => {
    const user = userEvent.setup();
    const pending = deferred<void>();
    const doc = makeDocument({ is_stale: true });
    const { onReprocess } = renderRow(doc, { onReprocess: () => pending.promise });
    expect(screen.getByText("Needs re-index")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Re-index" }));
    expect(onReprocess).toHaveBeenCalledWith(101);
    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
    pending.resolve();
    expect(await screen.findByRole("button", { name: "Re-index" })).toBeEnabled();
  });

  it("shows why a re-index couldn't start", async () => {
    const user = userEvent.setup();
    renderRow(makeDocument({ is_stale: true }), {
      onReprocess: () => Promise.reject(new ApiError(409, "This document is already being indexed.")),
    });
    await user.click(screen.getByRole("button", { name: "Re-index" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This document is already being indexed.");
  });

  it("names where a document came from", () => {
    renderRow(makeDocument({ generated: true, filename: "summary.docx" }));
    expect(screen.getByText(/^Created in chat, /)).toBeInTheDocument();
  });

  it("names the plugin a document was imported from", () => {
    renderRow(makeDocument({ imported_from: "Gmail", filename: "Q3 numbers.txt" }));
    expect(screen.getByText(/^Imported from Gmail, /)).toBeInTheDocument();
  });

  it("asks before deleting, and Cancel or Escape keeps the document", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderRow(makeDocument());
    const deleteButton = screen.getByRole("button", { name: "Delete quarterly-report.pdf" });

    await user.click(deleteButton);
    const confirm = screen.getByRole("group", { name: "Confirm deleting quarterly-report.pdf" });
    expect(deleteButton).toHaveAttribute("aria-expanded", "true");
    expect(within(confirm).getByText("Delete this document?")).toBeInTheDocument();
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: /Confirm deleting/ })).not.toBeInTheDocument();
    expect(deleteButton).toHaveFocus();

    await user.click(deleteButton);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: /Confirm deleting/ })).not.toBeInTheDocument();
    expect(deleteButton).toHaveFocus();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("deletes after confirmation", async () => {
    const user = userEvent.setup();
    const pending = deferred<void>();
    const { onDelete } = renderRow(makeDocument(), { onDelete: () => pending.promise });
    await user.click(screen.getByRole("button", { name: "Delete quarterly-report.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete document" }));
    expect(onDelete).toHaveBeenCalledWith(101);
    expect(screen.getByRole("button", { name: "Deleting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    pending.resolve();
    expect(await screen.findByRole("button", { name: "Delete document" })).toBeEnabled();
  });

  it("closes the confirmation and shows the error when deleting fails", async () => {
    const user = userEvent.setup();
    renderRow(makeDocument(), { onDelete: () => Promise.reject(new TypeError("Failed to fetch")) });
    await user.click(screen.getByRole("button", { name: "Delete quarterly-report.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete document" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong. Please try again.");
    expect(screen.queryByRole("group", { name: /Confirm deleting/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete quarterly-report.pdf" })).toHaveFocus();
  });

  it("opens a generated image in the viewer", async () => {
    const user = userEvent.setup();
    const details: GeneratedImageDetails = {
      prompt: "A lighthouse at dawn, watercolour",
      revised_prompt: null,
      model_name: "openai/gpt-image-1",
      created_at: "2026-10-19T12:00:00Z",
    };
    stubFetch({ "GET /documents/210/image/details": details });
    renderRow(
      makeDocument({
        id: 210,
        filename: "lighthouse.png",
        content_type: "image/png",
        generated: true,
        image: { width: 1024, height: 768, alt_text: "A lighthouse at dawn", quality: "medium" },
      }),
    );
    expect(screen.getByText(/^Created in chat, 1024 × 768, /)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "View lighthouse.png" }));
    const viewer = screen.getByRole("dialog", { name: "lighthouse.png" });
    expect(await within(viewer).findByText("A lighthouse at dawn, watercolour")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
