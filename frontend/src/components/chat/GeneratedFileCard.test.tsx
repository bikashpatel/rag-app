import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentItem, GeneratedFile } from "../../api/types";
import { formatBytes, formatNumber } from "../../lib/format";
import { makeDocument, makeGeneratedFile } from "../../test/fixtures";
import { publishDocuments, subscribeToReloadRequests } from "../documents/documentEvents";
import GeneratedFileCard, { kindName } from "./GeneratedFileCard";
import type { FileCard } from "./useChatStream";

type CreatedCard = Extract<FileCard, { kind: "created" }>;

function created(file: Partial<GeneratedFile> = {}, alreadyExisted = false): CreatedCard {
  const generated = makeGeneratedFile(file);
  return { key: `d${generated.document_id}`, kind: "created", file: generated, alreadyExisted };
}

function documentFor(file: GeneratedFile, overrides: Partial<DocumentItem> = {}): DocumentItem {
  return makeDocument({
    id: file.document_id ?? 0,
    filename: file.filename,
    size_bytes: file.size_bytes,
    content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    generated: true,
    ...overrides,
  });
}

function publish(list: DocumentItem[] | null) {
  act(() => publishDocuments(list));
}

afterEach(() => publish(null));

describe("GeneratedFileCard while writing", () => {
  it("says a file is being created before its title is known", () => {
    render(<GeneratedFileCard card={{ key: "cf1", kind: "writing", fileKind: "file", title: null, format: null, chars: 0 }} />);
    const card = screen.getByRole("group", { name: "File being created" });
    expect(within(card).getByText("Creating a file…")).toBeInTheDocument();
    expect(within(card).getByText("Writing the file…")).toBeInTheDocument();
    expect(within(card).queryByText(/characters/)).not.toBeInTheDocument();
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
  });

  it("names the file and counts the characters written", () => {
    render(
      <GeneratedFileCard card={{ key: "cf1", kind: "writing", fileKind: "file", title: "Q3 plan", format: "docx", chars: 12_345 }} />,
    );
    expect(screen.getByText("Creating Q3 plan…")).toBeInTheDocument();
    expect(screen.getByText(`${formatNumber(12_345)} characters`)).toBeInTheDocument();
  });
});

describe("GeneratedFileCard once created", () => {
  it("shows the type, size, status and a download link", () => {
    render(<GeneratedFileCard card={created({ document_id: 205 })} />);
    const card = screen.getByRole("group", { name: "summary.docx" });
    expect(within(card).getByText("Word")).toBeInTheDocument();
    expect(within(card).getByText(formatBytes(18_432))).toBeInTheDocument();
    expect(within(card).getByText("Ready")).toBeInTheDocument();
    const download = within(card).getByRole("link", { name: "Download summary.docx" });
    expect(download).toHaveAttribute("href", "/api/documents/205/download");
    expect(download).toHaveAttribute("download");
  });

  it("says when an identical document was linked instead", () => {
    render(<GeneratedFileCard card={created({ document_id: 206 }, true)} />);
    expect(screen.getByText("Already in your documents")).toBeInTheDocument();
  });

  it.each([
    ["pending", "Queued"],
    ["processing", "Indexing…"],
    ["failed", "Saved, but indexing failed"],
  ] as const)("shows the server's %s status until the panel's list has the document", (status, label) => {
    render(<GeneratedFileCard card={created({ document_id: 207, status })} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download summary.docx" })).toBeInTheDocument();
  });

  it("follows the documents panel's list", () => {
    const card = created({ document_id: 208, status: "processing" });
    publish([documentFor(card.file, { status: "processing" })]);
    render(<GeneratedFileCard card={card} />);
    expect(screen.getByText("Indexing…")).toBeInTheDocument();

    publish([documentFor(card.file, { status: "ready" })]);
    expect(screen.getByText("Ready")).toBeInTheDocument();

    publish([documentFor(card.file, { status: "ready", is_stale: true })]);
    expect(screen.getByText("Needs re-index")).toBeInTheDocument();

    publish([documentFor(card.file, { status: "failed", error: "The file has no text." })]);
    expect(screen.getByText("Saved, but indexing failed")).toHaveAttribute("title", "The file has no text.");
  });

  it("shows a document deleted from the panel's list as deleted, without a download", () => {
    const card = created({ document_id: 209 });
    publish([documentFor(card.file)]);
    render(<GeneratedFileCard card={card} />);
    expect(screen.getByRole("link", { name: "Download summary.docx" })).toBeInTheDocument();

    publish([]);
    expect(screen.getByText("Deleted from your documents")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows a file whose document is gone as deleted", () => {
    render(<GeneratedFileCard card={created({ document_id: null, status: null })} />);
    expect(screen.getByText("Deleted from your documents")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("asks the panel once to catch up on a document its list doesn't know yet", async () => {
    const onReload = vi.fn();
    const unsubscribe = subscribeToReloadRequests(onReload);
    publish([makeDocument({ id: 120 })]);
    render(<GeneratedFileCard card={created({ document_id: 210 })} />);
    await waitFor(() => expect(onReload).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Ready")).toBeInTheDocument();
    unsubscribe();
  });
});

describe("GeneratedFileCard without a file", () => {
  it("explains a failed file", () => {
    render(
      <GeneratedFileCard
        card={{ key: "cf2", kind: "failed", fileKind: "file", title: "Q3 plan", format: "pdf", error: "The table was too wide." }}
      />,
    );
    const card = screen.getByRole("group", { name: "Q3 plan" });
    expect(within(card).getByText("Couldn't create Q3 plan")).toBeInTheDocument();
    expect(within(card).getByText("The table was too wide.")).toBeInTheDocument();
  });

  it("explains a failed file without a title", () => {
    render(<GeneratedFileCard card={{ key: "cf2", kind: "failed", fileKind: "file", title: null, format: null, error: "Too long." }} />);
    expect(screen.getByRole("group", { name: "File" })).toHaveTextContent("Couldn't create the file");
  });

  it("explains a stopped file", () => {
    render(<GeneratedFileCard card={{ key: "cf3", kind: "stopped", fileKind: "file", title: "Q3 plan", format: "xlsx", wasGenerating: false }} />);
    expect(screen.getByText("Q3 plan: stopped. If it was already being saved, it's in your documents.")).toBeInTheDocument();
  });
});

describe("kindName", () => {
  it("names office formats and falls back to the type tag", () => {
    expect(kindName("plan.docx")).toBe("Word");
    expect(kindName("budget.XLSX")).toBe("Excel");
    expect(kindName("notes.md")).toBe("Markdown");
    expect(kindName("report.pdf")).toBe("PDF");
    expect(kindName("sunset.png")).toBe("PNG");
  });
});
