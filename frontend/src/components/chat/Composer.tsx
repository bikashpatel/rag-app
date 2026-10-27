import {
  useEffect,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";
import type { DocumentSelection } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { useFileDrop } from "../documents/useFileDrop";
import { ArrowUpIcon, DocsIcon, StopIcon } from "../ui/icons";
import AttachmentChips from "./AttachmentChips";
import { withStarter } from "./starters";
import type { AttachmentView } from "./useAttachments";

const MAX_MESSAGE_CHARS = 20_000;
const SHOW_COUNTER_FROM = 18_000;
const MAX_HEIGHT_PX = 200;
/** ⌘ on Apple keyboards, Ctrl elsewhere (functional brief 9.4). */
const APPLE = typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);

export type ComposerHandle = {
  /** Put text back after a rejected send, unless the user already typed something new. */
  restore: (text: string) => void;
  focus: () => void;
  /** Puts a "+" menu starter before the draft (once; replacing another starter), caret at the end; never sends. */
  insertStarter: (prefix: string) => void;
};

type ComposerProps = {
  ref?: Ref<ComposerHandle>;
  streaming: boolean;
  /** Why the composer is unavailable (shown as its placeholder), or null. */
  disabledReason: string | null;
  /** The thread has messages: the placeholder says "Reply…". */
  replying: boolean;
  selection: DocumentSelection;
  /** The selected model can create files and the plan includes them: the hint says so. */
  canCreateFiles: boolean;
  /** The selected model can generate images and the plan includes them: the hint says so. */
  canCreateImages: boolean;
  /** Preferences.enter_to_send: Enter sends (else Ctrl/Cmd+Enter does, and Enter adds a line). */
  enterToSend: boolean;
  /** The "+" menu, at the start of the bar. */
  plusMenu: ReactNode;
  attachments: AttachmentView[];
  onRemoveAttachment: (id: number) => void;
  /** Send waits for attached files ("Waiting for report.pdf to finish indexing…"), or null. */
  waiting: string | null;
  /** Files dropped on the composer (attached like the "+" menu's picker); omit to not accept drops. */
  onDropFiles?: (files: File[], folders: string[]) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  /** When given, the document-scope chip becomes a button (opens or expands the documents panel). */
  onScopeClick?: () => void;
  /** The unsent text after each change (kept when a plan ends on the page, 8.7). */
  onDraftChange?: (text: string) => void;
};

/** What the assistant can make with the selected model, or null. */
function makeHint(files: boolean, images: boolean): string | null {
  if (files && images) return "Ask for a PDF, Word, Excel or Markdown file, or an image";
  if (files) return "Ask for a PDF, Word, Excel or Markdown file";
  return images ? "Ask for an image" : null;
}

function scopeLabel(selection: DocumentSelection): string {
  if (selection === null) return "All documents";
  if (selection.length === 0) return "No documents";
  return selection.length === 1 ? "1 document" : `${formatNumber(selection.length)} documents`;
}

function KeyHint({ enterToSend }: { enterToSend: boolean }) {
  const modifier = APPLE ? "⌘" : "Ctrl";
  return enterToSend ? (
    <span className="chat-composer-hint-keys">
      <kbd>Enter</kbd> to send · <kbd>Shift</kbd> <kbd>Enter</kbd> for a new line
    </span>
  ) : (
    <span className="chat-composer-hint-keys">
      <kbd>{modifier}</kbd> <kbd>Enter</kbd> to send · <kbd>Enter</kbd> for a new line
    </span>
  );
}

export default function Composer({
  ref,
  streaming,
  disabledReason,
  replying,
  selection,
  canCreateFiles,
  canCreateImages,
  enterToSend,
  plusMenu,
  attachments,
  onRemoveAttachment,
  waiting,
  onDropFiles,
  onSend,
  onStop,
  onScopeClick,
  onDraftChange,
}: ComposerProps) {
  const [text, setText] = useState("");
  const draftRef = useRef(onDraftChange);
  draftRef.current = onDraftChange;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const caretToEnd = useRef(false);
  const inputId = useId();
  const counterId = useId();
  const hintId = useId();

  useImperativeHandle(
    ref,
    () => ({
      restore: (value) => setText((current) => (current.trim() ? current : value)),
      focus: () => textareaRef.current?.focus(),
      insertStarter: (prefix) => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        const next = withStarter(textarea.value, prefix);
        if (next !== textarea.value) {
          caretToEnd.current = true; // the effect below places the caret once the new draft has rendered
          setText(next);
          return;
        }
        // Unchanged: no render follows, so place the caret here (an armed flag would move the next keystroke's caret).
        const end = next.length;
        textarea.focus();
        textarea.setSelectionRange(end, end);
        textarea.scrollTop = textarea.scrollHeight;
      },
    }),
    [],
  );

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT_PX)}px`;
    if (caretToEnd.current) {
      caretToEnd.current = false;
      const end = textarea.value.length;
      textarea.focus();
      textarea.setSelectionRange(end, end);
      textarea.scrollTop = textarea.scrollHeight;
    }
  }, [text]);

  useEffect(() => draftRef.current?.(text), [text]);

  const dropEnabled = onDropFiles !== undefined && disabledReason === null;
  const { dragActive, dropHandlers } = useFileDrop((files, folders) => onDropFiles?.(files, folders), dropEnabled);

  const message = text.trim();
  const tooLong = message.length > MAX_MESSAGE_CHARS;
  const showCounter = text.length >= SHOW_COUNTER_FROM;
  const canSend = disabledReason === null && !streaming && waiting === null && message.length > 0 && !tooLong;

  function submit() {
    if (!canSend) return;
    setText("");
    onSend(message);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter") return;
    // keyCode 229: Safari fires the Enter that confirms an IME composition after compositionend.
    if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    // Ctrl/Cmd+Enter sends in both modes; plain Enter sends only with Enter-to-send on (Shift+Enter: a new line).
    if (e.metaKey || e.ctrlKey) {
      e.preventDefault();
      submit();
    } else if (enterToSend && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      submit();
    }
  }

  const scope = scopeLabel(selection);
  const offer = makeHint(canCreateFiles, canCreateImages);
  const scopeClass = selection !== null && selection.length === 0 ? "chat-scope chat-scope-off" : "chat-scope";
  const formClass = [
    "chat-composer",
    disabledReason !== null ? "chat-composer-disabled" : null,
    dragActive ? "chat-composer-drop" : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="chat-composer-wrap" {...(onDropFiles ? dropHandlers : {})}>
      <form className={formClass} onSubmit={onSubmit}>
        <AttachmentChips views={attachments} onRemove={onRemoveAttachment} />
        <label htmlFor={inputId} className="sr-only">
          Message
        </label>
        <textarea
          ref={textareaRef}
          id={inputId}
          className="chat-composer-input"
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={disabledReason ?? (replying ? "Reply…" : "Ask a question…")}
          disabled={disabledReason !== null}
          aria-describedby={showCounter ? `${hintId} ${counterId}` : hintId}
          aria-invalid={tooLong || undefined}
        />
        <div className="chat-composer-bar">
          {plusMenu}
          {onScopeClick ? (
            <button
              type="button"
              className={scopeClass}
              onClick={onScopeClick}
              aria-label={`Document scope: ${scope}. Show documents`}
            >
              <DocsIcon size={16} />
              <span className="chat-scope-label">{scope}</span>
            </button>
          ) : (
            <span className={scopeClass} title="Documents searched for answers (change them in the documents panel)">
              <DocsIcon size={16} />
              <span className="chat-scope-label">{scope}</span>
            </span>
          )}
          <span className="spacer" />
          {showCounter ? (
            <span id={counterId} className={tooLong ? "chat-counter chat-counter-over" : "chat-counter"}>
              {formatNumber(message.length)} / {formatNumber(MAX_MESSAGE_CHARS)}
            </span>
          ) : null}
          {streaming ? (
            <button
              type="button"
              className="chat-stop"
              onClick={(e) => {
                // React reuses this element as the Send (submit) button. A stop before the answer started restores
                // the draft and re-renders before this click's default action runs, which would then submit the
                // form and send the message again.
                e.preventDefault();
                onStop();
              }}
              aria-label="Stop generating"
              title="Stop"
            >
              <StopIcon size={20} />
            </button>
          ) : (
            <button
              type="submit"
              className="chat-send"
              // Not `disabled`: this element is the Stop button while streaming, and a focused button that becomes
              // disabled drops focus to <body>. submit() ignores a send that isn't possible.
              aria-disabled={!canSend || undefined}
              aria-label="Send message"
              title={waiting ?? "Send"}
            >
              <ArrowUpIcon size={18} />
            </button>
          )}
        </div>
      </form>
      <p id={hintId} className={waiting ? "chat-composer-hint chat-composer-hint-wait" : "chat-composer-hint"}>
        {waiting ?? (
          <>
            <KeyHint enterToSend={enterToSend} />
            {offer ? (
              <span className="chat-composer-hint-files">
                <span className="chat-composer-hint-sep" aria-hidden="true">
                  {" · "}
                </span>
                {offer}
              </span>
            ) : null}
          </>
        )}
      </p>
      <span className="sr-only" aria-live="polite">
        {waiting ?? ""}
      </span>
    </div>
  );
}
