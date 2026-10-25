import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentItem, GeneratedFile, GeneratedImageDetails } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { makeDocument, makeGeneratedFile } from "../../test/fixtures";
import { stubFetch } from "../../test/http";
import { publishDocuments } from "../documents/documentEvents";
import GeneratedImageCard from "./GeneratedImageCard";
import type { FileCard } from "./useChatStream";

type CreatedCard = Extract<FileCard, { kind: "created" }>;

const SUNSET = { width: 1536, height: 1024, alt_text: "A sunset over the bay with sailboats", quality: "medium" } as const;

function createdImage(file: Partial<GeneratedFile> = {}): CreatedCard {
  return {
    key: `d${file.document_id ?? 310}`,
    kind: "created",
    file: makeGeneratedFile({
      document_id: 310,
      filename: "sunset.png",
      format: "png",
      size_bytes: 1_245_000,
      image: { ...SUNSET },
      ...file,
    }),
    alreadyExisted: false,
  };
}

function documentFor(file: GeneratedFile, overrides: Partial<DocumentItem> = {}): DocumentItem {
  return makeDocument({
    id: file.document_id ?? 0,
    filename: file.filename,
    content_type: "image/png",
    size_bytes: file.size_bytes,
    generated: true,
    image: { ...SUNSET },
    ...overrides,
  });
}

function publish(list: DocumentItem[] | null) {
  act(() => publishDocuments(list));
}

afterEach(() => {
  publish(null);
  vi.useRealTimers();
});

describe("GeneratedImageCard while the image is made", () => {
  it("says an image is being created while its prompt is written", () => {
    render(<GeneratedImageCard card={{ key: "ci1", kind: "writing", fileKind: "image", title: null, format: null, chars: 40 }} />);
    expect(screen.getByRole("group", { name: "Image being generated" })).toHaveTextContent("Creating an image…");
  });

  it("names the image once the title is known", () => {
    render(<GeneratedImageCard card={{ key: "ci1", kind: "writing", fileKind: "image", title: "Sunset", format: null, chars: 40 }} />);
    expect(screen.getByText("Creating Sunset…")).toBeInTheDocument();
  });

  it("counts the seconds while the image model works", () => {
    vi.useFakeTimers();
    const startedAt = Date.now() - 12_000;
    render(
      <GeneratedImageCard
        card={{ key: "ci1", kind: "generating", title: "Sunset over the bay", width: 1536, height: 1024, quality: "medium", startedAt }}
      />,
    );
    const card = screen.getByRole("group", { name: "Image being generated" });
    expect(within(card).getByText("Generating Sunset over the bay…")).toBeInTheDocument();
    expect(within(card).getByText(/Creating the image… this takes up to a minute/)).toBeInTheDocument();
    expect(within(card).getByText("12 s")).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(2_000));
    expect(within(card).getByText("14 s")).toBeInTheDocument();
    expect(within(card).queryByRole("img")).not.toBeInTheDocument();
  });
});

describe("GeneratedImageCard once created", () => {
  it("shows the preview, size, status and a download link", () => {
    render(<GeneratedImageCard card={createdImage()} />);
    const card = screen.getByRole("group", { name: "sunset.png" });
    const open = within(card).getByRole("button", { name: "Open sunset.png full size" });
    expect(within(open).getByRole("img", { name: SUNSET.alt_text })).toHaveAttribute("src", "/api/documents/310/image?size=preview");
    expect(within(card).getByText("1536 × 1024")).toBeInTheDocument();
    expect(within(card).getByText(formatBytes(1_245_000))).toBeInTheDocument();
    expect(within(card).getByText("Ready")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Download sunset.png" })).toHaveAttribute("href", "/api/documents/310/download");
  });

  it("opens the full-size viewer with the prompt and closes it again", async () => {
    const details: GeneratedImageDetails = {
      prompt: "A watercolor sunset over a bay with sailboats",
      revised_prompt: null,
      model_name: "openai/gpt-image-2",
      created_at: "2026-10-20T09:31:00Z",
    };
    const calls = stubFetch({ "GET /documents/311/image/details": details });
    const user = userEvent.setup();
    render(<GeneratedImageCard card={createdImage({ document_id: 311 })} />);
    const open = screen.getByRole("button", { name: "Open sunset.png full size" });
    await user.click(open);

    const viewer = screen.getByRole("dialog", { name: "sunset.png" });
    expect(within(viewer).getByRole("img", { name: SUNSET.alt_text })).toHaveAttribute("src", "/api/documents/311/image?size=full");
    expect(await within(viewer).findByText(details.prompt)).toBeInTheDocument();
    expect(within(viewer).getByText(/Made with openai\/gpt-image-2/)).toBeInTheDocument();
    expect(calls.map((call) => call.path)).toEqual(["/documents/311/image/details"]);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(open).toHaveFocus();
  });

  it("offers to retry a preview that failed to load", async () => {
    const user = userEvent.setup();
    render(<GeneratedImageCard card={createdImage({ document_id: 312 })} />);
    fireEvent.error(screen.getByRole("img", { name: SUNSET.alt_text }));
    expect(screen.getByText("Couldn't load the image")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByRole("img", { name: SUNSET.alt_text })).toBeInTheDocument();
  });

  it("takes the image details from the panel's list when the card has none", () => {
    const card = createdImage({ document_id: 313, image: null });
    publish([documentFor(card.file)]);
    render(<GeneratedImageCard card={card} />);
    expect(screen.getByRole("button", { name: "Open sunset.png full size" })).toBeInTheDocument();
  });

  it("shows an image deleted from the panel's list as deleted, without preview or download", () => {
    const card = createdImage({ document_id: 314 });
    publish([documentFor(card.file, { status: "processing" })]);
    render(<GeneratedImageCard card={card} />);
    expect(screen.getByText("Indexing…")).toBeInTheDocument();

    publish([]);
    expect(screen.getByText("Deleted from your documents")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /full size/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("GeneratedImageCard without an image", () => {
  it("explains a failed image", () => {
    render(
      <GeneratedImageCard
        card={{ key: "ci2", kind: "failed", fileKind: "image", title: "Sunset", format: null, error: "The prompt was refused." }}
      />,
    );
    const card = screen.getByRole("group", { name: "Sunset" });
    expect(within(card).getByText("Couldn't generate Sunset")).toBeInTheDocument();
    expect(within(card).getByText("The prompt was refused.")).toBeInTheDocument();
  });

  it("says a stopped image that was being generated is still saved", () => {
    render(<GeneratedImageCard card={{ key: "ci3", kind: "stopped", fileKind: "image", title: "Sunset", format: null, wasGenerating: true }} />);
    expect(
      screen.getByText("Sunset: stopped. An image that was already being generated is still finished and saved to your documents."),
    ).toBeInTheDocument();
  });

  it("says a stopped image that wasn't generated yet is simply stopped", () => {
    render(<GeneratedImageCard card={{ key: "ci3", kind: "stopped", fileKind: "image", title: null, format: null, wasGenerating: false }} />);
    expect(screen.getByRole("group", { name: "Image" })).toHaveTextContent("Image: stopped.");
  });
});
