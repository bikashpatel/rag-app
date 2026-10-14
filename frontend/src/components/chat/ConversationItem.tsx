import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { ConversationSummary } from "../../api/types";
import Spinner from "../ui/Spinner";
import { PencilIcon, TrashIcon } from "./icons";

const MAX_TITLE_CHARS = 200;

type Mode = "view" | "rename" | "delete";

type ConversationItemProps = {
  conversation: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onRename: (id: number, title: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
};

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** One sidebar row: link to the conversation, inline rename and delete with inline confirmation. */
export default function ConversationItem({ conversation, active, onOpen, onRename, onDelete }: ConversationItemProps) {
  const [mode, setMode] = useState<Mode>("view");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelDeleteRef = useRef<HTMLButtonElement>(null);
  const renameButtonRef = useRef<HTMLButtonElement>(null);
  const deleteButtonRef = useRef<HTMLButtonElement>(null);
  const saving = useRef(false);
  // Guards against a blur fired while the input is being removed after Enter or Escape.
  const editing = useRef(false);
  // Which action button gets focus back when the row returns to "view" from the keyboard.
  const returnFocusTo = useRef<"rename" | "delete" | null>(null);

  useEffect(() => {
    if (mode === "rename") {
      inputRef.current?.focus();
      inputRef.current?.select();
    } else if (mode === "delete") {
      cancelDeleteRef.current?.focus();
    } else if (returnFocusTo.current) {
      (returnFocusTo.current === "rename" ? renameButtonRef : deleteButtonRef).current?.focus();
      returnFocusTo.current = null;
    }
  }, [mode]);

  function enter(next: Mode) {
    editing.current = next === "rename";
    setDraft(conversation.title);
    setError(null);
    setMode(next);
  }

  function leave(focus: "rename" | "delete" | null) {
    editing.current = false;
    returnFocusTo.current = focus;
    setError(null);
    setMode("view");
  }

  async function saveTitle(focus: "rename" | null) {
    if (saving.current || !editing.current) return;
    const title = draft.replace(/\s+/g, " ").trim();
    if (!title || title === conversation.title) {
      leave(focus);
      return;
    }
    saving.current = true;
    setBusy(true);
    try {
      await onRename(conversation.id, title);
      leave(focus);
    } catch (err) {
      setError(errorMessage(err, "Couldn't rename the conversation."));
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  async function confirmDelete() {
    setBusy(true);
    setError(null);
    try {
      await onDelete(conversation.id);
    } catch (err) {
      setError(errorMessage(err, "Couldn't delete the conversation."));
      setBusy(false);
    }
  }

  function onRenameKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void saveTitle("rename");
    } else if (e.key === "Escape") {
      e.preventDefault();
      leave("rename");
    }
  }

  function onConfirmKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape" && !busy) {
      e.preventDefault();
      leave("delete");
    }
  }

  const errorNote = error ? (
    <div id={errorId} className="chat-conv-error" role="alert">
      {error}
    </div>
  ) : null;

  if (mode === "rename") {
    return (
      <li className="chat-conv chat-conv-editing">
        <input
          ref={inputRef}
          className="input chat-conv-input"
          value={draft}
          maxLength={MAX_TITLE_CHARS}
          readOnly={busy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onRenameKeyDown}
          onBlur={() => void saveTitle(null)}
          aria-label="Conversation title"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
        {errorNote}
      </li>
    );
  }

  if (mode === "delete") {
    return (
      <li className="chat-conv chat-conv-confirm">
        <div className="chat-conv-confirm-box" role="group" aria-label="Confirm delete" onKeyDown={onConfirmKeyDown}>
          <span className="chat-conv-confirm-text">
            Delete “<span className="chat-conv-confirm-title">{conversation.title}</span>”?
          </span>
          <div className="chat-conv-confirm-actions">
            <button type="button" className="btn btn-danger btn-sm" onClick={confirmDelete} disabled={busy}>
              {busy ? <Spinner size={12} label="Deleting" /> : null}
              Delete
            </button>
            <button
              ref={cancelDeleteRef}
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => leave("delete")}
              disabled={busy}
            >
              Cancel
            </button>
          </div>
        </div>
        {errorNote}
      </li>
    );
  }

  return (
    <li className={active ? "chat-conv chat-conv-active" : "chat-conv"}>
      <Link
        to={`/c/${conversation.id}`}
        className="chat-conv-link"
        aria-current={active ? "page" : undefined}
        onClick={onOpen}
        title={conversation.title}
      >
        {conversation.title}
      </Link>
      <div className="chat-conv-actions">
        <button
          ref={renameButtonRef}
          type="button"
          className="chat-conv-action"
          onClick={() => enter("rename")}
          aria-label={`Rename “${conversation.title}”`}
          title="Rename"
        >
          <PencilIcon size={14} />
        </button>
        <button
          ref={deleteButtonRef}
          type="button"
          className="chat-conv-action chat-conv-action-danger"
          onClick={() => enter("delete")}
          aria-label={`Delete “${conversation.title}”`}
          title="Delete"
        >
          <TrashIcon size={14} />
        </button>
      </div>
    </li>
  );
}
