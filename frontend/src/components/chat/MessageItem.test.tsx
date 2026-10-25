import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { makeEmailSource, makeGeneratedFile, makeMessage, makePendingSend, makeSource } from "../../test/fixtures";
import MessageItem from "./MessageItem";
import type { ThreadMessage, ThreadPluginCard } from "./useChatStream";

function threadMessage(overrides: Partial<ThreadMessage> = {}): ThreadMessage {
  const { plugin_cards: _cards, ...message }: ChatMessage = makeMessage();
  return { ...message, key: "m501", streaming: false, stopped: false, warnings: [], cards: [], pluginCards: [], ...overrides };
}

type RenderOptions = { prompt?: string | null; retryDisabled?: boolean };

function renderMessage(message: ThreadMessage, { prompt = "How did revenue develop?", retryDisabled = false }: RenderOptions = {}) {
  const onRetry = vi.fn();
  render(<MessageItem message={message} prompt={prompt} retryDisabled={retryDisabled} onRetry={onRetry} />);
  return { onRetry };
}

describe("MessageItem for the user", () => {
  it("shows the message as plain text", () => {
    renderMessage(threadMessage({ role: "user", content: "Summarize **Q3** <b>now</b>", sources: [] }));
    const article = screen.getByRole("article", { name: "You" });
    expect(article).toHaveTextContent("Summarize **Q3** <b>now</b>");
    expect(article.querySelector("strong, b")).toBeNull();
  });
});

describe("MessageItem for the assistant", () => {
  it("renders the answer as markdown with the model and token count", () => {
    renderMessage(threadMessage({ content: "Revenue grew **18%** [1]." }));
    const article = screen.getByRole("article", { name: "Assistant" });
    expect(article).toHaveAttribute("aria-busy", "false");
    expect(within(article).getByText("18%").tagName).toBe("STRONG");
    expect(article).toHaveTextContent(`Claude Opus 5.5 · ${formatNumber(1_380)} tokens`);
    expect(within(article).getByText(`${formatNumber(1_380)} tokens`)).toHaveAttribute(
      "title",
      `${formatNumber(1_200)} prompt + ${formatNumber(180)} completion tokens`,
    );
  });

  it("opens and closes the sources list", async () => {
    const user = userEvent.setup();
    renderMessage(threadMessage({ sources: [makeSource(), makeEmailSource()] }));
    const toggle = screen.getByRole("button", { name: "2 sources" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByRole("list");
    expect(toggle).toHaveAttribute("aria-controls", list.id);
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);

    await user.click(toggle);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("opens the sources at the cited one when a citation is clicked", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderMessage(threadMessage({ content: "Revenue grew [1].", sources: [makeSource()] }));
    await user.click(screen.getByRole("button", { name: "Source 1: quarterly-report.pdf, page 3" }));
    expect(screen.getByRole("button", { name: "1 source" })).toHaveAttribute("aria-expanded", "true");
    expect(scroll.mock.contexts[0]).toBe(screen.getByRole("listitem"));
  });

  it("copies the answer's markdown", async () => {
    const user = userEvent.setup();
    renderMessage(threadMessage({ content: "Revenue grew **18%** [1]." }));
    await user.click(screen.getByRole("button", { name: "Copy answer" }));
    expect(await navigator.clipboard.readText()).toBe("Revenue grew **18%** [1].");
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(screen.getByText("Answer copied to the clipboard")).toBeInTheDocument();
  });

  it("says when the answer couldn't be copied", async () => {
    const user = userEvent.setup();
    renderMessage(threadMessage());
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    await user.click(screen.getByRole("button", { name: "Copy answer" }));
    expect(screen.getByRole("button", { name: "Couldn't copy" })).toBeInTheDocument();
  });

  it("shows warnings and an error with Retry", async () => {
    const user = userEvent.setup();
    const { onRetry } = renderMessage(
      threadMessage({
        content: "Partial answer",
        error: "The model provider returned an error.",
        warnings: ["Document search unavailable: Qdrant timed out"],
      }),
    );
    expect(screen.getByText("Document search unavailable: Qdrant timed out")).toBeInTheDocument();
    expect(screen.getByText("The model provider returned an error.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledWith("How did revenue develop?");
  });

  it("keeps Retry focusable but inert while another answer runs", async () => {
    const user = userEvent.setup();
    const { onRetry } = renderMessage(threadMessage({ stopped: true }), { retryDisabled: true });
    expect(screen.getByText("Stopped")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toHaveAttribute("aria-disabled", "true");
    await user.click(retry);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("offers no Retry without the prompt it answered", () => {
    renderMessage(threadMessage({ error: "Interrupted." }), { prompt: null });
    expect(screen.getByText("Interrupted.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("hides the footer details while streaming", () => {
    renderMessage(threadMessage({ content: "Revenue gr", sources: [], streaming: true }));
    const article = screen.getByRole("article", { name: "Assistant" });
    expect(article).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button", { name: "Copy answer" })).not.toBeInTheDocument();
    expect(article).not.toHaveTextContent("tokens");
  });

  it("names the list of cards by what they are", () => {
    const file = { key: "d205", kind: "created", file: makeGeneratedFile(), alreadyExisted: false } as const;
    const image = {
      key: "d310",
      kind: "created",
      file: makeGeneratedFile({
        document_id: 310,
        filename: "sunset.png",
        format: "png",
        image: { width: 1024, height: 1024, alt_text: "A sunset", quality: "low" },
      }),
      alreadyExisted: false,
    } as const;
    const { rerender } = render(
      <MessageItem message={threadMessage({ cards: [file] })} prompt={null} retryDisabled={false} onRetry={vi.fn()} />,
    );
    expect(screen.getByRole("list", { name: "Files" })).toBeInTheDocument();
    rerender(<MessageItem message={threadMessage({ cards: [image] })} prompt={null} retryDisabled={false} onRetry={vi.fn()} />);
    expect(screen.getByRole("list", { name: "Images" })).toBeInTheDocument();
    rerender(<MessageItem message={threadMessage({ cards: [file, image] })} prompt={null} retryDisabled={false} onRetry={vi.fn()} />);
    const list = screen.getByRole("list", { name: "Files and images" });
    expect(within(list).getByRole("group", { name: "summary.docx" })).toBeInTheDocument();
    expect(within(list).getByRole("group", { name: "sunset.png" })).toBeInTheDocument();
  });

  it("shows known plugin cards and ignores unknown types", () => {
    const send: ThreadPluginCard = {
      card_id: "g1",
      plugin_id: "gmail",
      type: "gmail_send",
      status: "failed",
      pending: null,
      error: "The request check refused this email.",
      stopped: false,
    };
    const unknown = { ...send, card_id: "x1", type: "calendar_event" } as unknown as ThreadPluginCard;
    const { rerender } = render(
      <MessageItem message={threadMessage({ pluginCards: [unknown] })} prompt={null} retryDisabled={false} onRetry={vi.fn()} />,
    );
    expect(screen.queryByRole("list", { name: "Plugin activity" })).not.toBeInTheDocument();

    rerender(<MessageItem message={threadMessage({ pluginCards: [unknown, send] })} prompt={null} retryDisabled={false} onRetry={vi.fn()} />);
    const list = screen.getByRole("list", { name: "Plugin activity" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByRole("group", { name: "Email to send" })).toHaveTextContent("Couldn't prepare the email");
  });

  it("renders a proposed email card from a loaded conversation", () => {
    const card: ThreadPluginCard = {
      card_id: "p1",
      plugin_id: "gmail",
      type: "gmail_send",
      status: "proposed",
      pending: makePendingSend({ status: "sent", sent_at: "2026-10-20T09:40:00Z" }),
      error: null,
      stopped: false,
    };
    renderMessage(threadMessage({ pluginCards: [card] }));
    expect(screen.getByRole("group", { name: "Email to send: Q3 summary" })).toHaveTextContent("Email sent");
  });
});
