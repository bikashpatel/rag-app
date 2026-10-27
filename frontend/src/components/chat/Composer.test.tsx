import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef, useRef, useState, type ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import Composer, { type ComposerHandle } from "./Composer";
import type { AttachmentView } from "./useAttachments";

type Props = ComponentProps<typeof Composer>;

function props(overrides: Partial<Props> = {}): Props {
  return {
    streaming: false,
    disabledReason: null,
    replying: false,
    selection: null,
    canCreateFiles: false,
    canCreateImages: false,
    enterToSend: true,
    plusMenu: null,
    attachments: [],
    onRemoveAttachment: vi.fn(),
    waiting: null,
    onSend: vi.fn(),
    onStop: vi.fn(),
    ...overrides,
  };
}

function setup(overrides: Partial<Props> = {}) {
  const user = userEvent.setup();
  const initial = props(overrides);
  const view = render(<Composer {...initial} />);
  const rerender = (next: Partial<Props>) => view.rerender(<Composer {...initial} {...next} />);
  return { user, props: initial, rerender, input: screen.getByRole("textbox", { name: "Message" }) };
}

const sendButton = () => screen.getByRole("button", { name: "Send message" });

describe("Composer", () => {
  it("sends the trimmed message on Enter and clears the draft", async () => {
    const { user, props, input } = setup();
    expect(input).toHaveAccessibleDescription(/Enter to send · Shift Enter for a new line/);
    await user.type(input, "  What changed in Q3?  {Enter}");
    expect(props.onSend).toHaveBeenCalledExactlyOnceWith("What changed in Q3?");
    expect(input).toHaveValue("");
  });

  it("reports the unsent text after each change", async () => {
    const onDraftChange = vi.fn();
    const { user, input } = setup({ onDraftChange });
    await user.type(input, "Hi{Enter}");
    expect(onDraftChange.mock.calls.map(([text]) => text)).toEqual(["", "H", "Hi", ""]);
  });

  it("adds a line on Shift+Enter when Enter sends", async () => {
    const { user, props, input } = setup();
    await user.type(input, "First line{Shift>}{Enter}{/Shift}Second line");
    expect(input).toHaveValue("First line\nSecond line");
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("adds a line on Enter and sends on Ctrl+Enter or Cmd+Enter when Enter-to-send is off", async () => {
    const { user, props, input } = setup({ enterToSend: false });
    expect(input).toHaveAccessibleDescription(/Enter to send · Enter for a new line/);
    await user.type(input, "Summarize{Enter}the report");
    expect(input).toHaveValue("Summarize\nthe report");
    expect(props.onSend).not.toHaveBeenCalled();

    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(props.onSend).toHaveBeenLastCalledWith("Summarize\nthe report");

    await user.type(input, "Next question");
    await user.keyboard("{Meta>}{Enter}{/Meta}");
    expect(props.onSend).toHaveBeenLastCalledWith("Next question");
    expect(props.onSend).toHaveBeenCalledTimes(2);
  });

  it("sends from the Send button, which is unavailable while the draft is empty", async () => {
    const { user, props, input } = setup();
    expect(sendButton()).toHaveAttribute("aria-disabled", "true");
    await user.click(sendButton());
    await user.type(input, "   ");
    await user.keyboard("{Enter}");
    expect(props.onSend).not.toHaveBeenCalled();

    await user.type(input, "Hello");
    expect(sendButton()).not.toHaveAttribute("aria-disabled");
    await user.click(sendButton());
    expect(props.onSend).toHaveBeenCalledExactlyOnceWith("Hello");
  });

  it("waits for attachments before sending, and says why", async () => {
    const waiting = "Waiting for report.pdf to finish indexing…";
    const { user, props, input, rerender } = setup({ waiting });
    expect(input).toHaveAccessibleDescription(waiting);
    expect(screen.getByRole("button", { name: "Send message" })).toHaveAttribute("aria-disabled", "true");
    await user.type(input, "Compare the two reports{Enter}");
    await user.click(sendButton());
    expect(props.onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("Compare the two reports");

    rerender({ waiting: null });
    await user.click(sendButton());
    expect(props.onSend).toHaveBeenCalledExactlyOnceWith("Compare the two reports");
  });

  it("shows Stop instead of Send while streaming and doesn't send meanwhile", async () => {
    const { user, props, input } = setup({ streaming: true });
    expect(screen.queryByRole("button", { name: "Send message" })).not.toBeInTheDocument();
    await user.type(input, "Follow-up{Enter}");
    expect(props.onSend).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(props.onStop).toHaveBeenCalledTimes(1);
  });

  it("keeps a draft restored by Stop without sending it", async () => {
    const onSend = vi.fn();
    function Harness() {
      const [streaming, setStreaming] = useState(true);
      const composer = useRef<ComposerHandle>(null);
      return (
        <Composer
          {...props({ onSend })}
          ref={composer}
          streaming={streaming}
          onStop={() => {
            setStreaming(false);
            composer.current?.restore("Explain the chart");
          }}
        />
      );
    }
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("Explain the chart");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("is disabled with the reason as its placeholder", () => {
    const { input } = setup({ disabledReason: "No chat model is available." });
    expect(input).toBeDisabled();
    expect(input).toHaveAttribute("placeholder", "No chat model is available.");
  });

  it("asks for a reply once the thread has messages", () => {
    const { input, rerender } = setup();
    expect(input).toHaveAttribute("placeholder", "Ask a question…");
    rerender({ replying: true });
    expect(input).toHaveAttribute("placeholder", "Reply…");
  });

  it("says what the model can create", () => {
    const { input, rerender } = setup({ canCreateFiles: true, canCreateImages: true });
    expect(input).toHaveAccessibleDescription(/Ask for a PDF, Word, Excel or Markdown file, or an image$/);
    rerender({ canCreateFiles: true, canCreateImages: false });
    expect(input).toHaveAccessibleDescription(/Ask for a PDF, Word, Excel or Markdown file$/);
    rerender({ canCreateFiles: false, canCreateImages: true });
    expect(input).toHaveAccessibleDescription(/Ask for an image$/);
  });

  it.each<[Props["selection"], string]>([
    [null, "All documents"],
    [[], "No documents"],
    [[101], "1 document"],
    [[101, 102, 103], "3 documents"],
  ])("labels the document scope %j as %s", (selection, label) => {
    setup({ selection });
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("makes the scope a button that shows the documents when asked", async () => {
    const onScopeClick = vi.fn();
    const { user } = setup({ selection: [101], onScopeClick });
    await user.click(screen.getByRole("button", { name: "Document scope: 1 document. Show documents" }));
    expect(onScopeClick).toHaveBeenCalledTimes(1);
  });

  it("counts characters near the limit and refuses a message over it", async () => {
    const { user, props, input } = setup();
    await user.click(input);
    await user.paste("x".repeat(18_000));
    expect(screen.getByText(/18,000 \/ 20,000/)).toBeInTheDocument();
    expect(input).not.toHaveAttribute("aria-invalid");

    await user.paste("x".repeat(2_001));
    expect(screen.getByText(/20,001 \/ 20,000/)).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(sendButton()).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Enter}");
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("sends a message of exactly 20,000 characters", async () => {
    const { user, props, input } = setup();
    await user.click(input);
    await user.paste("x".repeat(20_000));
    expect(input).not.toHaveAttribute("aria-invalid");
    await user.keyboard("{Enter}");
    expect(props.onSend).toHaveBeenCalledTimes(1);
  });

  it("doesn't send the Enter that confirms an IME composition", () => {
    const { props, input } = setup();
    fireEvent.change(input, { target: { value: "東京の売上" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(props.onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("東京の売上");
  });

  it("puts a starter before the draft once, replacing another starter, with the caret at the end", async () => {
    const ref = createRef<ComposerHandle>();
    const user = userEvent.setup();
    render(<Composer {...props()} ref={ref} />);
    const input = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message" });
    await user.type(input, "a sunset over the bay");

    act(() => ref.current?.insertStarter("Create an image: "));
    expect(input).toHaveValue("Create an image: a sunset over the bay");
    expect(input).toHaveFocus();
    expect(input.selectionStart).toBe(input.value.length);

    act(() => ref.current?.insertStarter("Create an image: "));
    expect(input).toHaveValue("Create an image: a sunset over the bay");

    act(() => ref.current?.insertStarter("Create a PDF document: "));
    expect(input).toHaveValue("Create a PDF document: a sunset over the bay");
  });

  it("restores a rejected message only when the user hasn't typed a new one", async () => {
    const ref = createRef<ComposerHandle>();
    const user = userEvent.setup();
    render(<Composer {...props()} ref={ref} />);
    const input = screen.getByRole("textbox", { name: "Message" });

    act(() => ref.current?.restore("Original question"));
    expect(input).toHaveValue("Original question");

    await user.clear(input);
    await user.type(input, "New question");
    act(() => ref.current?.restore("Original question"));
    expect(input).toHaveValue("New question");
  });

  it("lists attachments and removes one", async () => {
    const attachments: AttachmentView[] = [
      { id: 4, name: "report.pdf", state: { kind: "indexing" }, documentId: 210 },
    ];
    const { user, props } = setup({ attachments });
    expect(screen.getByRole("list", { name: "Attached files" })).toHaveTextContent("report.pdf");
    await user.click(screen.getByRole("button", { name: "Remove report.pdf from this message; it stays in your documents" }));
    expect(props.onRemoveAttachment).toHaveBeenCalledExactlyOnceWith(4);
  });
});
