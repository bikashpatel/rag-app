import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentConfig, DocumentItem, DocumentSelection, GmailThreadPage, Me } from "../../api/types";
import { controlUploads, makeFile, type FakeUpload } from "../../test/documents";
import {
  makeAdminMe,
  makeConnection,
  makeDocument,
  makeDocumentConfig,
  makeGmailPlugin,
  makeMe,
} from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import { onConnectionsRequest, reloadPlugins } from "../connections/pluginStore";
import DocumentPanel from "./DocumentPanel";
import { uploadDocument } from "./uploadDocument";
import { uploadManager } from "./uploadManager";

vi.mock("./uploadDocument", () => ({ uploadDocument: vi.fn() }));

const report = makeDocument();
const plan = makeDocument({ id: 102, filename: "launch-plan.docx", content_type: "application/msword", num_pages: null });
const indexing = makeDocument({ id: 103, filename: "minutes.md", status: "processing", num_chunks: 0, num_pages: null });
const handbook = makeDocument({
  id: 104,
  filename: "handbook.pdf",
  scope: "shared",
  owner_id: 1,
  owner_username: "admin",
  can_delete: false,
});

let uploads: FakeUpload[];

type Setup = {
  me?: Me;
  documents?: DocumentItem[];
  config?: DocumentConfig;
  selection?: DocumentSelection;
  onClose?: () => void;
  routes?: Record<string, Route>;
};

function Harness({ initial, onClose }: { initial: DocumentSelection; onClose?: () => void }) {
  const [selection, setSelection] = useState<DocumentSelection>(initial);
  return <DocumentPanel selection={selection} onSelectionChange={setSelection} onClose={onClose} />;
}

async function setup({ me = makeMe(), documents = [report, plan], config = makeDocumentConfig(), selection = null, onClose, routes }: Setup = {}) {
  const state = { documents, config };
  const calls: FetchCall[] = stubFetch({
    "GET /auth/me": me,
    "GET /documents": () => state.documents,
    "GET /documents/config": () => state.config,
    ...routes,
  });
  const user = userEvent.setup({ applyAccept: false });
  const view = await renderWithAuth(<Harness initial={selection} onClose={onClose} />);
  const fileInput = view.container.ownerDocument.querySelector<HTMLInputElement>('input[type="file"]') as HTMLInputElement;
  return { user, state, calls, fileInput };
}

const summary = () => screen.getByRole("checkbox", { name: "Use all documents in chat" });
const rowCheckbox = (doc: DocumentItem) => screen.getByRole("checkbox", { name: `Use ${doc.filename} in chat` });

beforeEach(() => {
  uploads = controlUploads(vi.mocked(uploadDocument));
});

afterEach(() => uploadManager.clear());

describe("DocumentPanel", () => {
  it("lists private and shared documents, and says what chat searches", async () => {
    await setup({ documents: [report, plan, indexing, handbook] });
    const mine = await screen.findByRole("region", { name: "My documents (3)" });
    expect(within(mine).getAllByRole("listitem")).toHaveLength(3);
    expect(within(mine).getByText("Indexing…")).toBeInTheDocument();
    const shared = screen.getByRole("region", { name: "Shared knowledge base (1)" });
    expect(within(shared).getByText("handbook.pdf")).toBeInTheDocument();
    expect(summary()).toBeChecked();
    expect(summary()).toHaveAccessibleDescription("Searching all 3 documents");
    expect(screen.getByRole("complementary", { name: "Documents" })).toBeInTheDocument();
  });

  it("shows a placeholder while the list loads", async () => {
    const list = deferred<DocumentItem[]>();
    await setup({ routes: { "GET /documents": () => list.promise } });
    expect(screen.getByRole("status")).toHaveTextContent("Loading documents…");
    await act(async () => list.resolve([report]));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("quarterly-report.pdf")).toBeInTheDocument();
  });

  it("narrows and widens the documents chat uses", async () => {
    const { user } = await setup();
    await screen.findByText("launch-plan.docx");

    await user.click(rowCheckbox(plan));
    expect(rowCheckbox(plan)).not.toBeChecked();
    expect(summary()).toBePartiallyChecked();
    expect(summary()).toHaveAccessibleDescription("Searching 1 of 2 documents");

    await user.click(summary());
    expect(summary()).toBeChecked();
    expect(summary()).toHaveAccessibleDescription("Searching all 2 documents");

    await user.click(summary());
    expect(summary()).not.toBeChecked();
    expect(summary()).toHaveAccessibleDescription("Document search off");
    expect(rowCheckbox(report)).not.toBeChecked();

    await user.click(rowCheckbox(report));
    await user.click(rowCheckbox(plan));
    expect(summary()).toHaveAccessibleDescription("Searching all 2 documents");
  });

  it("drops documents that no longer exist from the selection", async () => {
    await setup({ selection: [report.id, 999] });
    await screen.findByText("launch-plan.docx");
    expect(summary()).toHaveAccessibleDescription("Searching 1 of 2 documents");
  });

  it("retries a failed document", async () => {
    const failed = makeDocument({ id: 105, filename: "scan.pdf", status: "failed", error: "No text found.", num_chunks: 0 });
    const queued: DocumentItem = { ...failed, status: "pending", error: null };
    const { user, state, calls } = await setup({
      documents: [failed],
      routes: { "POST /documents/105/reprocess": () => ((state.documents = [queued]), queued) },
    });
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Queued")).toBeInTheDocument();
    expect(screen.queryByText("No text found.")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual(["/documents/105/reprocess"]);
  });

  it("deletes a document after confirmation", async () => {
    const { user, state, calls } = await setup({
      routes: {
        "DELETE /documents/101": () => {
          state.documents = [plan];
          return new Response(null, { status: 204 });
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: "Delete quarterly-report.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete document" }));
    await waitFor(() => expect(screen.queryByText("quarterly-report.pdf")).not.toBeInTheDocument());
    expect(screen.getByRole("region", { name: "My documents (1)" })).toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE" && call.path === "/documents/101")).toBe(true);
  });

  it("treats a document already deleted elsewhere as deleted", async () => {
    const { user, state } = await setup({
      routes: {
        "DELETE /documents/101": () => {
          state.documents = [plan];
          return errorResponse(404, "Document not found");
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: "Delete quarterly-report.pdf" }));
    await user.click(screen.getByRole("button", { name: "Delete document" }));
    await waitFor(() => expect(screen.queryByText("quarterly-report.pdf")).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("uploads chosen files to My documents and lists them once created", async () => {
    const { user, state, fileInput } = await setup();
    await screen.findByText("launch-plan.docx");
    await user.upload(fileInput, makeFile("brief.pdf"));

    const queue = screen.getByRole("list", { name: "Uploads" });
    expect(within(queue).getByText("Uploading 0%")).toBeInTheDocument();
    expect(uploads.map((upload) => [upload.file.name, upload.scope])).toEqual([["brief.pdf", "private"]]);
    act(() => uploads[0].progress(55));
    expect(within(queue).getByText("Uploading 55%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Upload progress of brief.pdf" })).toHaveAttribute("aria-valuenow", "55");
    act(() => uploads[0].progress(100));
    expect(within(queue).getByText("Finishing…")).toBeInTheDocument();

    const created = makeDocument({ id: 106, filename: "brief.pdf", status: "pending", num_chunks: 0 });
    state.documents = [created, report, plan];
    state.config = makeDocumentConfig({ document_count: 5 });
    await act(async () => uploads[0].succeed(created));

    expect(await screen.findByText("brief.pdf uploaded. It can be searched once indexing finishes.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Uploads" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "My documents (3)" })).toHaveTextContent("Queued");
    await waitFor(() => expect(screen.getByRole("group", { name: "Your document quota" })).toHaveTextContent("5 / 100"));
  });

  it("lists a refused file as failed until dismissed", async () => {
    const { user, fileInput } = await setup();
    await screen.findByText("launch-plan.docx");
    await user.upload(fileInput, makeFile("setup.exe"));
    expect(screen.getByRole("alert")).toHaveTextContent(".exe files aren't supported.");
    expect(uploads).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Dismiss setup.exe" }));
    expect(screen.queryByRole("list", { name: "Uploads" })).not.toBeInTheDocument();
  });

  it("cancels an upload in progress", async () => {
    const { user, fileInput } = await setup();
    await screen.findByText("launch-plan.docx");
    await user.upload(fileInput, makeFile("brief.pdf"));
    await user.click(screen.getByRole("button", { name: "Cancel upload of brief.pdf" }));
    expect(uploads[0].signal.aborted).toBe(true);
    await waitFor(() => expect(screen.queryByRole("list", { name: "Uploads" })).not.toBeInTheDocument());
  });

  it("reports dropped folders and uploads the dropped files", async () => {
    await setup();
    await screen.findByText("launch-plan.docx");
    const panel = screen.getByRole("complementary", { name: "Documents" });
    const file = makeFile("notes.md");
    const dataTransfer = {
      types: ["Files"],
      dropEffect: "none",
      items: [
        { kind: "file", webkitGetAsEntry: () => ({ isDirectory: true, name: "Invoices" }), getAsFile: () => null },
        { kind: "file", webkitGetAsEntry: () => ({ isDirectory: false, name: "notes.md" }), getAsFile: () => file },
      ],
    };

    fireEvent.dragEnter(panel, { dataTransfer });
    expect(screen.getByText("Drop files to upload")).toBeInTheDocument();
    fireEvent.drop(panel, { dataTransfer });

    expect(screen.queryByText("Drop files to upload")).not.toBeInTheDocument();
    const queue = screen.getByRole("list", { name: "Uploads" });
    expect(within(queue).getByRole("alert")).toHaveTextContent(
      "Folders can't be uploaded. Open the folder and select the files instead.",
    );
    expect(uploads.map((upload) => upload.file.name)).toEqual(["notes.md"]);
  });

  it("lets an admin upload to the shared knowledge base", async () => {
    const { user, fileInput } = await setup({
      me: makeAdminMe(),
      documents: [],
      config: makeDocumentConfig({ can_upload_shared: true, max_documents: 0, max_storage_mb: 0 }),
    });
    const scope = await screen.findByRole("radiogroup", { name: "Upload to" });
    expect(within(scope).getByRole("radio", { name: "Private" })).toBeChecked();
    expect(scope).toHaveAccessibleDescription("Only you can see and search your private documents.");
    expect(screen.getByText(/As an admin you can also add them to the shared knowledge base/)).toBeInTheDocument();

    await user.click(within(scope).getByRole("radio", { name: "Shared knowledge base" }));
    expect(scope).toHaveAccessibleDescription("Every user can see and search shared documents.");
    await user.upload(fileInput, makeFile("handbook.pdf"));
    expect(uploads.map((upload) => upload.scope)).toEqual(["shared"]);
    expect(screen.getByText("Uploading 0% · Shared")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Your document quota" })).toHaveTextContent(/Documents4 \/ Unlimited/);
  });

  it("shows an empty state naming the supported types", async () => {
    await setup({ documents: [] });
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
    expect(
      screen.getByText(/^Upload PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, TXT or JSON files and ask about them in chat\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Drop files here or browse/ })).toHaveAccessibleDescription(
      "PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, TXT, JSON · up to 25 MB each",
    );
    expect(screen.queryByRole("checkbox", { name: "Use all documents in chat" })).not.toBeInTheDocument();
  });

  it("shows the quota and warns when the document limit is reached", async () => {
    await setup({ config: makeDocumentConfig({ document_count: 100, storage_bytes: 450 * 1024 * 1024 }) });
    const quota = await screen.findByRole("group", { name: "Your document quota" });
    expect(quota).toHaveTextContent("Documents100 / 100Limit reached");
    expect(quota).toHaveTextContent("Storage450 MB / 500 MBAlmost used up");
    expect(screen.getByRole("button", { name: /Drop files here or browse/ })).toHaveAccessibleDescription(
      "You've reached your document limit. Delete a document to upload another.",
    );
  });

  it("warns when the storage is full", async () => {
    await setup({ config: makeDocumentConfig({ storage_bytes: 500 * 1024 * 1024 }) });
    expect(await screen.findByRole("button", { name: /Drop files here or browse/ })).toHaveAccessibleDescription(
      "Your storage is full. Delete a document to free up space.",
    );
  });

  it("disables uploads until the upload settings load, and retries them", async () => {
    let attempts = 0;
    const { user } = await setup({
      routes: {
        "GET /documents/config": () => (++attempts === 1 ? errorResponse(503, "Upload settings are unavailable.") : makeDocumentConfig()),
      },
    });
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("Upload settings are unavailable.");
    const dropzone = screen.getByRole("button", { name: /Drop files here or browse/ });
    expect(dropzone).toBeDisabled();
    expect(dropzone).toHaveAccessibleDescription("Uploads are unavailable until the upload settings load.");

    await user.click(within(notice).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(dropzone).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Your document quota" })).toBeInTheDocument();
  });

  it("explains a failed list load and retries it", async () => {
    let attempts = 0;
    const { user } = await setup({
      routes: { "GET /documents": () => (++attempts === 1 ? errorResponse(500, null) : [report]) },
    });
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("Documents unavailable");
    expect(error).toHaveTextContent("The server ran into a problem. Please try again.");
    await user.click(within(error).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("quarterly-report.pdf")).toBeInTheDocument();
  });

  it("closes from its close button and on Escape", async () => {
    const onClose = vi.fn();
    const { user } = await setup({ onClose });
    await user.click(await screen.findByRole("button", { name: "Close documents panel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    rowCheckbox(report).focus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("offers Gmail import when Gmail is connected", async () => {
    const page: GmailThreadPage = { threads: [], next_page_token: null, result_size_estimate: 0 };
    const { user } = await setup({
      routes: { "GET /plugins": [makeGmailPlugin()], "POST /plugins/gmail/threads/search": page },
    });
    await screen.findByText("launch-plan.docx");
    await act(() => reloadPlugins());
    await user.click(screen.getByRole("button", { name: "Import from Gmail" }));
    expect(screen.getByRole("dialog", { name: "Import from Gmail" })).toBeInTheDocument();
  });

  it("offers to reconnect Gmail when its connection needs it", async () => {
    const requests = vi.fn();
    const unsubscribe = onConnectionsRequest(requests);
    const plugin = makeGmailPlugin({
      connection: makeConnection({ status: "reconnect_needed", status_reason: "revoked", status_message: "Access was revoked." }),
    });
    const { user } = await setup({ routes: { "GET /plugins": [plugin] } });
    await screen.findByText("launch-plan.docx");
    await act(() => reloadPlugins());
    expect(screen.queryByRole("button", { name: "Import from Gmail" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reconnect Gmail" }));
    expect(requests).toHaveBeenCalledWith(null);
    unsubscribe();
  });
});
