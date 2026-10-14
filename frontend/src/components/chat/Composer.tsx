import {
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type Ref,
} from "react";
import type { DocumentSelection } from "../../api/types";
import { formatNumber } from "../../lib/format";
import { DocumentsIcon, SendIcon, StopIcon } from "./icons";

const MAX_MESSAGE_CHARS = 20_000;
const SHOW_COUNTER_FROM = 18_000;
const MAX_HEIGHT_PX = 200;

export type ComposerHandle = {
  /** Put text back after a rejected send, unless the user already typed something new. */
  restore: (text: string) => void;
  focus: () => void;
};

type ComposerProps = {
  ref?: Ref<ComposerHandle>;
  streaming: boolean;
  /** Why the composer is unavailable (shown as its placeholder), or null. */
  disabledReason: string | null;
  selection: DocumentSelection;
  onSend: (text: string) => void;
  onStop: () => void;
  /** When given, the document-scope chip becomes a button (opens the documents slide-over). */
  onScopeClick?: () => void;
};

function scopeLabel(selection: DocumentSelection): string {
  if (selection === null) return "All documents";
  if (selection.length === 0) return "Documents off";
  return selection.length === 1 ? "1 document" : `${formatNumber(selection.length)} documents`;
}

export default function Composer({
  ref,
  streaming,
  disabledReason,
  selection,
  onSend,
  onStop,
  onScopeClick,
}: ComposerProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const inputId = useId();
  const counterId = useId();
  const hintId = useId();

  useImperativeHandle(
    ref,
    () => ({
      restore: (value) => setText((current) => (current.trim() ? current : value)),
      focus: () => textareaRef.current?.focus(),
    }),
    [],
  );

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  const message = text.trim();
  const tooLong = message.length > MAX_MESSAGE_CHARS;
  const showCounter = text.length >= SHOW_COUNTER_FROM;
  const canSend = disabledReason === null && !streaming && message.length > 0 && !tooLong;

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
    if (e.key !== "Enter" || e.shiftKey) return;
    // keyCode 229: Safari fires the Enter that confirms an IME composition after compositionend.
    if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    e.preventDefault();
    submit();
  }

  const scope = scopeLabel(selection);
  const scopeClass = selection !== null && selection.length === 0 ? "chat-scope chat-scope-off" : "chat-scope";

  return (
    <div className="chat-composer-wrap">
      <form className="chat-composer" onSubmit={onSubmit}>
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
          placeholder={disabledReason ?? "Ask a question…"}
          disabled={disabledReason !== null}
          aria-describedby={showCounter ? `${hintId} ${counterId}` : hintId}
          aria-invalid={tooLong || undefined}
        />
        <div className="chat-composer-bar">
          {onScopeClick ? (
            <button
              type="button"
              className={scopeClass}
              onClick={onScopeClick}
              aria-label={`Document scope: ${scope}. Open documents`}
            >
              <DocumentsIcon size={13} />
              {scope}
            </button>
          ) : (
            <span className={scopeClass} title="Documents searched for answers (change them in the documents panel)">
              <DocumentsIcon size={13} />
              {scope}
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
              className="chat-send chat-stop"
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
              <StopIcon size={14} />
            </button>
          ) : (
            <button type="submit" className="chat-send" disabled={!canSend} aria-label="Send message" title="Send">
              <SendIcon size={16} />
            </button>
          )}
        </div>
      </form>
      <p id={hintId} className="chat-composer-hint">
        <kbd>Enter</kbd> to send, <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line
      </p>
    </div>
  );
}
