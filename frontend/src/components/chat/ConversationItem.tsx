import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { ConversationSummary } from "../../api/types";
import Menu, { MenuItem } from "../ui/Menu";
import { ButtonSpinner } from "../ui/Spinner";
import { MoreIcon, PencilIcon, TrashIcon } from "./icons";

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

/** One sidebar row: link to the conversation, a "⋯" menu with Rename (inline) and Delete (inline confirmation). */
export default function ConversationItem({ conversation, active, onOpen, onRename, onDelete }: ConversationItemProps) {
  const [mode, setMode] = useState<Mode>("view");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const confirmId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelDeleteRef = useRef<HTMLButtonElement>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const saving = useRef(false);
  // Guards against a blur fired while the input is being removed after Enter or Escape.
  const editing = useRef(false);
  // The "⋯" button gets focus back when the row returns to "view" from the keyboard.
  const returnFocusTo = useRef<"rename" | "delete" | null>(null);

  useEffect(() => {
    if (mode === "rename") {
      inputRef.current?.focus();
      inputRef.current?.select();
    } else if (mode === "delete") {
      cancelDeleteRef.current?.focus();
    } else if (returnFocusTo.current) {
      moreButtonRef.current?.focus();
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
        <div className="chat-conv-confirm-box" role="group" aria-labelledby={confirmId} onKeyDown={onConfirmKeyDown}>
          <span id={confirmId} className="chat-conv-confirm-title">
            Delete this chat?
          </span>
          <span className="chat-conv-confirm-text">
            “{conversation.title}” and its messages go. Your documents stay.
          </span>
          <div className="chat-conv-confirm-actions">
            <button
              ref={cancelDeleteRef}
              type="button"
              className="btn btn-ghost btn-xs"
              onClick={() => leave("delete")}
              disabled={busy}
            >
              Cancel
            </button>
            <button type="button" className="btn btn-danger btn-xs" onClick={confirmDelete} disabled={busy} aria-busy={busy || undefined}>
              {busy ? <ButtonSpinner size={12} /> : null}
              Delete chat
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
        <Menu
          label={`Chat: ${conversation.title}`}
          placement="bottom-end"
          offset={6}
          className="chat-conv-menu"
          triggerRef={moreButtonRef}
          trigger={(props) => (
            <button
              {...props}
              type="button"
              className="chat-conv-action"
              aria-label={`More actions for “${conversation.title}”`}
              title="Rename or delete"
            >
              <MoreIcon size={18} />
            </button>
          )}
        >
          <MenuItem icon={<PencilIcon size={17} />} onSelect={() => enter("rename")}>
            Rename
          </MenuItem>
          <MenuItem icon={<TrashIcon size={17} />} className="chat-conv-menu-danger" onSelect={() => enter("delete")}>
            Delete
          </MenuItem>
        </Menu>
      </div>
    </li>
  );
}
