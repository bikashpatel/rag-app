import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentItem } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { makeDocument } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import DocumentsTab from "./DocumentsTab";

const report = makeDocument({ vision_pages: 3 });
const handbook = makeDocument({
  id: 102,
  filename: "employee-handbook.docx",
  scope: "shared",
  owner_id: 1,
  owner_username: "admin",
  content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  is_stale: true,
});
const notes = makeDocument({ id: 103, filename: "notes.md", owner_id: 9, owner_username: "sam.ortiz", status: "pending", num_chunks: 0 });
const deck = makeDocument({ id: 104, filename: "pitch.pptx", owner_id: 9, owner_username: "sam.ortiz", status: "processing" });
const scan = makeDocument({ id: 105, filename: "scan.pdf", status: "failed", error: "The PDF has no readable text.", num_chunks: 0 });
const all = [report, handbook, notes, deck, scan];

function stubDocuments(documents: DocumentItem[] | Route = all, extra: Record<string, Route> = {}) {
  return stubFetch({ "GET /admin/documents": documents, ...extra });
}

async function renderTab() {
  const onChanged = vi.fn();
  render(<DocumentsTab onChanged={onChanged} />);
  await screen.findByRole("table");
  return onChanged;
}

const row = (filename: string) => within(screen.getByRole("row", { name: new RegExp(filename.replace(".", "\\.")) }));
const filenames = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((tr) => all.find((doc) => within(tr).queryByText(doc.filename))?.filename);

afterEach(() => {
  vi.useRealTimers();
});

describe("DocumentsTab", () => {
  it("lists every document with its owner, scope and status", async () => {
    stubDocuments();
    await renderTab();

    expect(screen.getByRole("region", { name: "Documents" })).toBeInTheDocument();
    expect(screen.getByText("5 documents")).toBeInTheDocument();
    expect(row("quarterly-report.pdf").getByText("Ready")).toBeInTheDocument();
    expect(row("quarterly-report.pdf").getByText("3 pages read with the vision model")).toBeInTheDocument();
    expect(row("quarterly-report.pdf").getByText(formatBytes(report.size_bytes))).toBeInTheDocument();
    expect(row("quarterly-report.pdf").getByText("Private")).toBeInTheDocument();
    expect(row("employee-handbook.docx").getByText("Shared")).toBeInTheDocument();
    expect(row("employee-handbook.docx").getByText("Needs re-index")).toBeInTheDocument();
    expect(row("employee-handbook.docx").queryByText("Ready")).not.toBeInTheDocument();
    expect(row("notes.md").getByText("Queued")).toBeInTheDocument();
    expect(row("pitch.pptx").getByText("Indexing…")).toBeInTheDocument();
    expect(row("scan.pdf").getByText("Failed")).toHaveAttribute("title", "The PDF has no readable text.");
    expect(row("scan.pdf").getByText(": The PDF has no readable text.")).toBeInTheDocument();
  });

  it("filters by file name or owner and by status", async () => {
    const user = userEvent.setup();
    stubDocuments();
    await renderTab();
    const search = screen.getByRole("searchbox", { name: "Filter by file name or owner" });
    const status = screen.getByRole("combobox", { name: "Filter by status" });

    await user.type(search, "SAM");
    expect(filenames()).toEqual(["notes.md", "pitch.pptx"]);
    expect(screen.getByText("2 of 5")).toBeInTheDocument();

    await user.clear(search);
    await user.selectOptions(status, "Needs re-index");
    expect(filenames()).toEqual(["employee-handbook.docx"]);
    await user.selectOptions(status, "Failed");
    expect(filenames()).toEqual(["scan.pdf"]);

    await user.type(search, "report");
    expect(screen.getByText("No documents match these filters.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(filenames()).toHaveLength(5);
    expect(search).toHaveValue("");
    expect(status).toHaveValue("all");
  });

  it("deletes a document after confirmation, treating one already gone as deleted", async () => {
    const user = userEvent.setup();
    const calls = stubDocuments(all, {
      "DELETE /admin/documents/101": () => new Response(null, { status: 204 }),
      "DELETE /admin/documents/102": () => errorResponse(404, "Document not found"),
    });
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Delete quarterly-report.pdf" }));
    const confirm = within(screen.getByRole("group", { name: /^Delete quarterly-report\.pdf/ }));
    expect(confirm.getByText(/\(owned by maya\.chen\)\?/)).toBeInTheDocument();
    expect(confirm.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(confirm.getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Deleted quarterly-report.pdf.")).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /quarterly-report/ })).not.toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Delete employee-handbook.docx" }));
    expect(screen.getByRole("group", { name: /from the shared knowledge base\?$/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Deleted employee-handbook.docx.")).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "DELETE").map((call) => call.path)).toEqual([
      "/admin/documents/101",
      "/admin/documents/102",
    ]);
  });

  it("keeps a document whose delete failed and says why", async () => {
    const user = userEvent.setup();
    stubDocuments(all, { "DELETE /admin/documents/105": () => errorResponse(500, "Qdrant is unreachable.") });
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Delete scan.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Qdrant is unreachable.");
    expect(screen.getByRole("row", { name: /scan\.pdf/ })).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("re-indexes every document after confirmation", async () => {
    const user = userEvent.setup();
    const calls = stubDocuments(all, { "POST /admin/reindex": { queued: 5 } });
    const onChanged = await renderTab();

    await user.click(screen.getByRole("button", { name: "Re-index all documents" }));
    expect(screen.getByRole("group", { name: /^Re-embed every document/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Re-index all" }));

    expect(await screen.findByText("Queued 5 documents for re-indexing.")).toBeInTheDocument();
    expect(calls.find((call: FetchCall) => call.method === "POST")?.body).toEqual({ only_stale: false });
    expect(onChanged).toHaveBeenCalled();
    await waitFor(() => expect(calls.filter((call) => call.path === "/admin/documents")).toHaveLength(2));
  });

  it("has nothing to re-index without documents", async () => {
    stubDocuments([]);
    render(<DocumentsTab onChanged={() => {}} />);
    expect(await screen.findByText("No documents yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-index all documents" })).toBeDisabled();
  });

  it("polls while documents are indexing and reports when they are done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let list: DocumentItem[] = [report, deck];
    const calls = stubDocuments(() => list);
    const onChanged = await renderTab();

    list = [report, { ...deck, status: "ready" }];
    await act(() => vi.advanceTimersByTimeAsync(4_000));
    await waitFor(() => expect(row("pitch.pptx").getByText("Ready")).toBeInTheDocument());
    expect(onChanged).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(8_000));
    expect(calls).toHaveLength(2);
  });

  it("shows a load error with a retry", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubDocuments(() => (fail ? errorResponse(503, "The database is starting.") : all));
    render(<DocumentsTab onChanged={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The database is starting.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("5 documents")).toBeInTheDocument();
  });
});
