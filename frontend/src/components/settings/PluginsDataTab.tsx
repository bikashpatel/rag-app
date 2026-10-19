import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, deleteAllConversations, downloadMyData, getAccount, saveBlob } from "../../api/client";
import type { PluginInfo } from "../../api/types";
import { announceConversationsDeleted } from "../../lib/appEvents";
import { formatBytes } from "../../lib/format";
import ConnectionsPanel, { type OAuthReturn } from "../connections/ConnectionsPanel";
import { AlertIcon, CheckCircleIcon, DownloadIcon, TrashIcon } from "../ui/icons";
import { PanelHead, plural } from "./PanelParts";
import { useLoad } from "./useLoad";

/** What opened the tab with a purpose: a banner to show and a card to focus (navigation state, consumed once). */
export type PluginsIntent = {
  oauthReturn: OAuthReturn | null;
  closedPluginId: string | null;
  focusPluginId: string | null;
};

const CONFIRM_WORD = "DELETE";

type Outcome = { tone: "success" | "warning"; text: string };

/** A result line that takes focus when it appears, so keyboard and screen reader users land on what happened. */
function FocusedNotice({ outcome }: { outcome: Outcome }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <p ref={ref} className={`ui-notice ui-notice-${outcome.tone} settings-result`} role="status" tabIndex={-1}>
      {outcome.tone === "success" ? <CheckCircleIcon size={15} /> : <AlertIcon size={15} />}
      <span className="ui-notice-text">{outcome.text}</span>
    </p>
  );
}

function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <p className="error-text settings-error" role="alert">
      <AlertIcon size={13} />
      {children}
    </p>
  );
}

function ExportRow() {
  const [working, setWorking] = useState(false);
  const [saved, setSaved] = useState<{ text: string; omitted: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function exportData() {
    if (working) return;
    setWorking(true);
    setSaved(null);
    setError(null);
    try {
      // Not aborted when another tab is selected: the download still arrives (the server's lock lasts until it ends).
      const result = await downloadMyData();
      saveBlob(result.blob, result.filename);
      setSaved({
        text: `Saved ${result.filename} (${formatBytes(result.blob.size)}).`,
        omitted:
          result.omitted > 0
            ? `The export holds your newest ${plural(result.conversations, "conversation", "conversations")}; ${plural(result.omitted, "older one", "older ones")} didn't fit.`
            : null,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't prepare the export. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="settings-data-row">
      <span className="settings-data-icon" aria-hidden="true">
        <DownloadIcon size={18} />
      </span>
      <div className="settings-data-main">
        <h3 className="settings-label-title">Export my data</h3>
        <p className="settings-desc">
          A ZIP with your conversations (Markdown and JSON), the list of your documents, your settings and your sign-in
          history. Document files aren't included: download them from the documents panel.
        </p>
        {working ? (
          <p className="settings-progress" role="status">
            <span className="spinner" aria-hidden="true" />
            Preparing your export…
          </p>
        ) : null}
        {saved ? (
          <>
            <p className="ui-notice ui-notice-success settings-result" role="status">
              <CheckCircleIcon size={15} />
              <span className="ui-notice-text">{saved.text}</span>
            </p>
            {saved.omitted ? (
              <p className="ui-notice ui-notice-warning settings-result">
                <AlertIcon size={15} />
                <span className="ui-notice-text">{saved.omitted}</span>
              </p>
            ) : null}
          </>
        ) : null}
        {error ? <ErrorLine>{error}</ErrorLine> : null}
      </div>
      <button type="button" className="btn btn-sm" aria-disabled={working || undefined} onClick={exportData}>
        Export
      </button>
    </div>
  );
}

type DeleteRowProps = {
  /** null while the account loads (or when it couldn't). */
  count: number | null;
  onDeleted: (deleted: number) => void;
};

function DeleteAllRow({ count, onDeleted }: DeleteRowProps) {
  const id = useId();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const openRef = useRef<HTMLButtonElement>(null);
  /** Cancel puts focus back on "Delete all chats…", which renders again only after the confirmation closes. */
  const refocus = useRef(false);
  const matches = typed.trim().toUpperCase() === CONFIRM_WORD;
  const none = count === 0;
  const action = count === null ? "Delete all chats" : `Delete ${plural(count, "chat", "chats")}`;

  useEffect(() => {
    if (confirming || !refocus.current) return;
    refocus.current = false;
    openRef.current?.focus();
  }, [confirming]);

  function close() {
    refocus.current = true;
    setConfirming(false);
    setTyped("");
    setError(null);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!matches || working) return;
    setWorking(true);
    setError(null);
    try {
      const { deleted } = await deleteAllConversations();
      setConfirming(false);
      setTyped("");
      setOutcome({ tone: "success", text: `Deleted ${plural(deleted, "conversation", "conversations")}.` });
      onDeleted(deleted);
      announceConversationsDeleted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete your chats. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  let description: string;
  if (count === null) {
    description =
      "Deletes every conversation and its messages. Your documents stay, including files and images created in chat. Drafts and sent emails stay in Gmail.";
  } else if (none) {
    description = "You have no conversations right now.";
  } else {
    description = `Deletes every conversation (${new Intl.NumberFormat().format(count)} now) and its messages. Your documents stay, including files and images created in chat. Drafts and sent emails stay in Gmail.`;
  }

  return (
    <div className="settings-data-row settings-data-danger">
      <span className="settings-data-icon" aria-hidden="true">
        <TrashIcon size={18} />
      </span>
      <div className="settings-data-main">
        <h3 className="settings-label-title">Delete all chats</h3>
        <p className="settings-desc" id={`${id}-desc`}>
          {description}
        </p>
        {confirming ? (
          <form className="settings-typed" onSubmit={onSubmit} aria-label="Confirm deleting all chats" noValidate>
            <label className="field-label" htmlFor={`${id}-word`}>
              Type <code>{CONFIRM_WORD}</code> to confirm
            </label>
            <input
              id={`${id}-word`}
              className="input"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              autoFocus
              aria-describedby={`${id}-desc`}
            />
            <div className="settings-actions settings-actions-end">
              <button type="button" className="btn btn-sm" onClick={close} disabled={working}>
                Cancel
              </button>
              <button type="submit" className="btn btn-sm btn-danger" disabled={!matches || working}>
                {working ? <span className="spinner" aria-hidden="true" /> : null}
                {action}
              </button>
            </div>
            {working ? (
              <p className="sr-only" role="status">
                Deleting your chats…
              </p>
            ) : null}
            {error ? <ErrorLine>{error}</ErrorLine> : null}
          </form>
        ) : null}
        {outcome ? <FocusedNotice outcome={outcome} /> : null}
      </div>
      {confirming ? null : (
        <button
          ref={openRef}
          type="button"
          className="btn btn-sm"
          aria-disabled={none || undefined}
          onClick={() => {
            if (none) return;
            setOutcome(null);
            setConfirming(true);
          }}
        >
          Delete all chats…
        </button>
      )}
    </div>
  );
}

type PluginsDataTabProps = {
  intent: PluginsIntent;
  /** A new key remounts the connection cards, which read the banner and the focus target when they mount. */
  intentKey: string;
  onBrowse: (plugin: PluginInfo) => void;
  /** Every chat was deleted: closing must not go back to a page that may show one. */
  onAllChatsDeleted: () => void;
  onClose: () => void;
};

/** Settings → Plugins & data: the plugin connection cards, the data export and "Delete all chats". */
export default function PluginsDataTab({ intent, intentKey, onBrowse, onAllChatsDeleted, onClose }: PluginsDataTabProps) {
  const id = useId();
  const account = useLoad((signal) => getAccount(signal), "Couldn't load your conversation count.");
  const [deleted, setDeleted] = useState<number | null>(null);
  const count = account.data ? (deleted === null ? account.data.conversation_count : 0) : null;

  return (
    <>
      <PanelHead title="Plugins & data" onClose={onClose} />
      <div className="settings-body">
        <div role="group" aria-labelledby={`${id}-plugins`}>
          <p className="settings-sub" id={`${id}-plugins`}>
            Plugins
          </p>
          <ConnectionsPanel
            key={intentKey}
            oauthReturn={intent.oauthReturn}
            closedPluginId={intent.closedPluginId}
            focusPluginId={intent.focusPluginId}
            onBrowse={onBrowse}
          />
        </div>
        <div role="group" aria-labelledby={`${id}-data`}>
          <p className="settings-sub" id={`${id}-data`}>
            Your data
          </p>
          <ExportRow />
          <DeleteAllRow
            count={count}
            onDeleted={(n) => {
              setDeleted(n);
              onAllChatsDeleted();
            }}
          />
        </div>
      </div>
    </>
  );
}
