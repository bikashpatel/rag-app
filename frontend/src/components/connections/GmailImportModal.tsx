import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type RefObject } from "react";
import { ApiError, getGmailThread, importFromGmail, searchGmailThreads } from "../../api/client";
import type {
  GmailAttachmentInfo,
  GmailImportItem,
  GmailImportRequest,
  GmailMessageView,
  GmailThreadDetail,
  GmailThreadSummary,
  PluginInfo,
} from "../../api/types";
import { formatBytes } from "../../lib/format";
import { DocsIcon, ExternalLinkIcon, PaperclipIcon, SearchIcon } from "../ui/icons";
import { formatEmailDate, importStatusView, plural, safeHttpsUrl } from "../chat/plugins/email";
import { announceDocument, requestDocumentsReload } from "../documents/documentEvents";
import Modal from "../ui/Modal";
import Spinner, { ButtonSpinner } from "../ui/Spinner";
import "./connections.css";

const PAGE_SIZE = 20;
const SEARCH_MAX_CHARS = 500;
/** Longer email text is collapsed to 12 lines until "Show more". */
const COLLAPSED_LINES = 12;
const COLLAPSE_CHARS = 1200;

type ThreadLoad = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; detail: GmailThreadDetail };

type AttachmentRef = GmailImportRequest["threads"][number]["attachments"][number];
/** What is selected in one thread: the conversation itself and/or attachments (key `<message id>/<part id>`). */
type ThreadPick = { includeThread: boolean; attachments: Map<string, AttachmentRef> };

type Phase = { kind: "select" } | { kind: "importing"; count: number } | { kind: "done"; items: GmailImportItem[] };

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function pickCount(pick: ThreadPick): number {
  return (pick.includeThread ? 1 : 0) + pick.attachments.size;
}

function mergeThreads(list: GmailThreadSummary[], more: GmailThreadSummary[]): GmailThreadSummary[] {
  const known = new Set(list.map((thread) => thread.thread_id));
  return [...list, ...more.filter((thread) => !known.has(thread.thread_id))];
}

type GmailImportModalProps = { plugin: PluginInfo; onClose: () => void };

/**
 * The Gmail browser (brief 15.3): search with Gmail's syntax, open a conversation inline, pick conversations and
 * attachments, and import them as private documents. Email text is rendered as text only.
 */
export default function GmailImportModal({ plugin, onClose }: GmailImportModalProps) {
  const searchId = useId();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [threads, setThreads] = useState<GmailThreadSummary[] | null>(null);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const [estimate, setEstimate] = useState(0);
  const [searching, setSearching] = useState<"search" | "more" | null>("search");
  const [searchError, setSearchError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, ThreadLoad>>({});
  const [picks, setPicks] = useState<ReadonlyMap<string, ThreadPick>>(new Map());
  const [phase, setPhase] = useState<Phase>({ kind: "select" });
  const [importError, setImportError] = useState<string | null>(null);
  const latestSearch = useRef(0);
  const searchController = useRef<AbortController | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const summaryRef = useRef<HTMLParagraphElement>(null);
  const previousPhase = useRef<Phase["kind"]>(phase.kind);

  const maxItems = plugin.import_max_items;
  const selected = [...picks.values()].reduce((sum, pick) => sum + pickCount(pick), 0);
  const overLimit = maxItems !== null && selected > maxItems;

  const runSearch = useCallback(async (q: string, pageToken: string | null) => {
    const request = ++latestSearch.current;
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    setSearching(pageToken ? "more" : "search");
    setSearchError(null);
    try {
      const page = await searchGmailThreads({ q, pageToken, maxResults: PAGE_SIZE }, controller.signal);
      if (request !== latestSearch.current) return;
      setThreads((list) => (pageToken && list ? mergeThreads(list, page.threads) : page.threads));
      setNextPage(page.next_page_token);
      setEstimate(page.result_size_estimate);
    } catch (err) {
      if (isAbortError(err) || request !== latestSearch.current) return;
      setSearchError(errorText(err, "Couldn't search Gmail. Try again."));
      if (!pageToken) setThreads(null);
    } finally {
      if (request === latestSearch.current) setSearching(null);
    }
  }, []);

  useEffect(() => {
    void runSearch("", null);
    return () => searchController.current?.abort();
  }, [runSearch]);

  // Each phase replaces the dialog's content and footer, so the focused button is gone (or disabled): move focus to
  // the new content (the progress, the result, which is then read out, or the search box after "Import more" or a
  // failed import) instead of leaving it on <body>.
  useEffect(() => {
    const previous = previousPhase.current;
    previousPhase.current = phase.kind;
    if (previous === phase.kind) return;
    if (phase.kind === "importing") progressRef.current?.focus();
    else if (phase.kind === "done") summaryRef.current?.focus();
    else searchRef.current?.focus();
  }, [phase.kind]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const q = draft.trim();
    setQuery(q);
    setOpenId(null);
    void runSearch(q, null);
  }

  async function loadThread(threadId: string) {
    setDetails((all) => ({ ...all, [threadId]: { status: "loading" } }));
    try {
      const detail = await getGmailThread(threadId);
      setDetails((all) => ({ ...all, [threadId]: { status: "ready", detail } }));
    } catch (err) {
      const message = errorText(err, "Couldn't open this conversation. Try again.");
      setDetails((all) => ({ ...all, [threadId]: { status: "error", message } }));
    }
  }

  function toggleOpen(threadId: string) {
    if (openId === threadId) {
      setOpenId(null);
      return;
    }
    setOpenId(threadId);
    const known = details[threadId];
    if (!known || known.status === "error") void loadThread(threadId);
  }

  function updatePick(threadId: string, change: (pick: ThreadPick) => ThreadPick) {
    setPicks((previous) => {
      const next = new Map(previous);
      const updated = change(previous.get(threadId) ?? { includeThread: false, attachments: new Map() });
      if (pickCount(updated) === 0) next.delete(threadId);
      else next.set(threadId, updated);
      return next;
    });
  }

  function setThreadPicked(threadId: string, includeThread: boolean) {
    updatePick(threadId, (pick) => ({ ...pick, includeThread }));
  }

  function setAttachmentPicked(threadId: string, ref: AttachmentRef, picked: boolean) {
    updatePick(threadId, (pick) => {
      const attachments = new Map(pick.attachments);
      const key = `${ref.message_id}/${ref.part_id}`;
      if (picked) attachments.set(key, ref);
      else attachments.delete(key);
      return { ...pick, attachments };
    });
  }

  async function runImport() {
    const body: GmailImportRequest = {
      threads: [...picks].map(([thread_id, pick]) => ({
        thread_id,
        include_thread: pick.includeThread,
        attachments: [...pick.attachments.values()],
      })),
    };
    setPhase({ kind: "importing", count: selected });
    setImportError(null);
    try {
      const result = await importFromGmail(body);
      // Announced even when this dialog was closed meanwhile: the documents exist.
      result.items.forEach((item) => {
        if (item.document) announceDocument(item.document);
      });
      setPicks(new Map());
      setPhase({ kind: "done", items: result.items });
    } catch (err) {
      setImportError(errorText(err, "Couldn't reach the server. Some items may have been imported; check your documents."));
      setPhase({ kind: "select" });
      requestDocumentsReload();
    }
  }

  let footer;
  if (phase.kind === "done") {
    footer = (
      <>
        <button type="button" className="btn" onClick={() => setPhase({ kind: "select" })}>
          Import more
        </button>
        <button type="button" className="btn btn-primary" onClick={onClose}>
          Done
        </button>
      </>
    );
  } else {
    const importing = phase.kind === "importing";
    footer = (
      <div className="conn-footer">
        <span className={overLimit ? "conn-footer-count conn-footer-count-over" : "conn-footer-count"} aria-live="polite">
          {overLimit
            ? `${selected} selected: at most ${maxItems} per import`
            : selected === 0
              ? "Nothing selected"
              : `${plural(selected, "item")} selected`}
        </span>
        {selected > 0 && !importing ? (
          <button type="button" className="btn btn-ghost" onClick={() => setPicks(new Map())}>
            Clear
          </button>
        ) : null}
        <button type="button" className="btn" onClick={onClose}>
          {importing ? "Continue in background" : "Cancel"}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void runImport()}
          disabled={importing || selected === 0 || overLimit}
          aria-busy={importing || undefined}
        >
          {importing ? <ButtonSpinner size={14} /> : null}
          {importing ? "Importing…" : `Import ${plural(selected, "item")}`}
        </button>
      </div>
    );
  }

  return (
    <Modal open title="Import from Gmail" onClose={onClose} width={1000} className="conn-browser-dialog" footer={footer}>
      {phase.kind === "done" ? (
        <ImportResults items={phase.items} summaryRef={summaryRef} />
      ) : phase.kind === "importing" ? (
        <div ref={progressRef} className="conn-progress conn-focus-target" role="status" tabIndex={-1}>
          <Spinner size={20} label="Importing" />
          <span>
            Importing {plural(phase.count, "item")}… This can take up to a minute and a half. Imported items appear in
            your documents as “Imported from Gmail”.
          </span>
        </div>
      ) : (
        <div className="conn-browser">
          <form className="conn-search" role="search" onSubmit={submitSearch}>
            <label className="sr-only" htmlFor={searchId}>
              Search Gmail
            </label>
            <input
              ref={searchRef}
              id={searchId}
              type="search"
              className="input"
              value={draft}
              maxLength={SEARCH_MAX_CHARS}
              placeholder="Search Gmail, e.g. from:alice has:attachment"
              onChange={(event) => setDraft(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            <button type="submit" className="btn" disabled={searching === "search"}>
              <SearchIcon size={16} />
              Search
            </button>
          </form>
          {importError ? (
            <p className="error-text" role="alert">
              {importError}
            </p>
          ) : null}
          <p className="conn-search-meta" aria-live="polite">
            {searching === "search"
              ? "Searching…"
              : threads === null
                ? ""
                : threads.length === 0
                  ? query
                    ? "No conversations match this search."
                    : "Your inbox is empty."
                  : `${query ? `Results for “${query}”` : "Inbox"} · ${plural(threads.length, "conversation")} shown${
                      estimate > threads.length ? ` of about ${estimate}` : ""
                    }. Tick a conversation to import it, or open it to pick attachments.`}
          </p>
          {searchError ? (
            <p className="error-text" role="alert">
              {searchError}
            </p>
          ) : null}
          {searching === "search" && threads === null ? <ThreadSkeleton /> : null}
          {threads && threads.length > 0 ? (
            <ul className="conn-threads" aria-busy={searching === "search" || undefined}>
              {threads.map((thread) => (
                <ThreadItem
                  key={thread.thread_id}
                  thread={thread}
                  open={openId === thread.thread_id}
                  load={details[thread.thread_id]}
                  pick={picks.get(thread.thread_id)}
                  onToggleOpen={() => toggleOpen(thread.thread_id)}
                  onRetry={() => void loadThread(thread.thread_id)}
                  onPickThread={(value) => setThreadPicked(thread.thread_id, value)}
                  onPickAttachment={(ref, value) => setAttachmentPicked(thread.thread_id, ref, value)}
                />
              ))}
            </ul>
          ) : null}
          {nextPage && threads && threads.length > 0 ? (
            <button
              type="button"
              className="btn btn-sm conn-load-more"
              onClick={() => void runSearch(query, nextPage)}
              disabled={searching !== null}
              aria-busy={searching === "more" || undefined}
            >
              {searching === "more" ? <ButtonSpinner size={12} /> : null}
              Load more
            </button>
          ) : null}
        </div>
      )}
    </Modal>
  );
}

type ThreadItemProps = {
  thread: GmailThreadSummary;
  open: boolean;
  load: ThreadLoad | undefined;
  pick: ThreadPick | undefined;
  onToggleOpen: () => void;
  onRetry: () => void;
  onPickThread: (picked: boolean) => void;
  onPickAttachment: (ref: AttachmentRef, picked: boolean) => void;
};

function ThreadItem({ thread, open, load, pick, onToggleOpen, onRetry, onPickThread, onPickAttachment }: ThreadItemProps) {
  const detailId = useId();
  const subject = thread.subject || "(no subject)";
  const date = formatEmailDate(thread.date);
  return (
    <li className={`conn-thread${open ? " conn-thread-open" : ""}${thread.unread ? " conn-thread-unread" : ""}`}>
      <div className="conn-thread-row">
        <span className="conn-thread-check">
          <input
            type="checkbox"
            className="ui-check"
            checked={pick?.includeThread ?? false}
            onChange={(event) => onPickThread(event.target.checked)}
            aria-label={`Import the conversation “${subject}”`}
          />
        </span>
        <button
          type="button"
          className="conn-thread-toggle"
          aria-expanded={open}
          aria-controls={open ? detailId : undefined}
          onClick={onToggleOpen}
        >
          <span className="conn-thread-sender">
            {thread.unread ? (
              <>
                <span className="conn-unread-dot" aria-hidden="true" />
                <span className="sr-only">Unread, </span>
              </>
            ) : null}
            <span className="conn-thread-sender-name">{thread.sender}</span>
            {thread.message_count > 1 ? (
              <span className="conn-thread-count" title={plural(thread.message_count, "message")}>
                <span className="sr-only">, </span>
                {thread.message_count}
                <span className="sr-only"> messages,</span>
              </span>
            ) : null}
          </span>
          <span className="conn-thread-text">
            <span className="conn-thread-subject">{subject}</span>
            {thread.snippet ? (
              <span className="conn-thread-snippet">
                <span aria-hidden="true"> – </span>
                <span className="sr-only">, </span>
                {thread.snippet}
              </span>
            ) : null}
          </span>
          {date ? (
            <span className="conn-thread-date">
              <span className="sr-only">, </span>
              {date}
            </span>
          ) : null}
        </button>
      </div>
      {open ? (
        <div id={detailId} className="conn-thread-detail">
          {!load || load.status === "loading" ? (
            <Spinner size={16} label="Opening the conversation" />
          ) : load.status === "error" ? (
            <p className="error-text" role="alert">
              {load.message}{" "}
              <button type="button" className="conn-link-btn" onClick={onRetry}>
                Retry
              </button>
            </p>
          ) : (
            <ThreadDetailView detail={load.detail} pick={pick} onPickAttachment={onPickAttachment} />
          )}
        </div>
      ) : null}
    </li>
  );
}

type ThreadDetailViewProps = {
  detail: GmailThreadDetail;
  pick: ThreadPick | undefined;
  onPickAttachment: (ref: AttachmentRef, picked: boolean) => void;
};

function ThreadDetailView({ detail, pick, onPickAttachment }: ThreadDetailViewProps) {
  const link = safeHttpsUrl(detail.gmail_url);
  return (
    <>
      {detail.messages_truncated ? (
        <p className="conn-detail-note">Only the newest {detail.messages.length} messages are shown.</p>
      ) : null}
      {detail.messages.map((message) => (
        <MessageView key={message.message_id} message={message} pick={pick} onPickAttachment={onPickAttachment} />
      ))}
      {link ? (
        <a className="conn-link-btn" href={link} target="_blank" rel="noopener noreferrer">
          Open in Gmail <ExternalLinkIcon size={13} />
        </a>
      ) : null}
    </>
  );
}

type MessageViewProps = {
  message: GmailMessageView;
  pick: ThreadPick | undefined;
  onPickAttachment: (ref: AttachmentRef, picked: boolean) => void;
};

function MessageView({ message, pick, onPickAttachment }: MessageViewProps) {
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();
  const long = message.body_text.split("\n").length > COLLAPSED_LINES || message.body_text.length > COLLAPSE_CHARS;
  const date = formatEmailDate(message.date);
  const recipients = [
    message.to.length > 0 ? `To: ${message.to.join(", ")}` : null,
    message.cc.length > 0 ? `Cc: ${message.cc.join(", ")}` : null,
  ].filter(Boolean);
  return (
    <article className="conn-message" aria-label={`Email from ${message.sender}`}>
      <div className="conn-message-head">
        <span className="conn-message-sender">{message.sender}</span>
        {date ? <span className="conn-thread-date">{date}</span> : null}
      </div>
      {recipients.length > 0 ? <span className="conn-message-recipients">{recipients.join(" · ")}</span> : null}
      {message.body_text ? (
        <pre
          id={bodyId}
          className={long && !expanded ? "conn-message-body conn-message-body-collapsed" : "conn-message-body"}
        >
          {message.body_text}
        </pre>
      ) : (
        <p className="conn-detail-note">No text.</p>
      )}
      {long ? (
        <button
          type="button"
          className="conn-link-btn"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
      {message.body_truncated ? <p className="conn-detail-note">This email is longer than shown here.</p> : null}
      {message.attachments.length > 0 ? (
        <ul className="conn-attachments" aria-label="Attachments">
          {message.attachments.map((attachment) => (
            <AttachmentRow
              key={attachment.part_id}
              attachment={attachment}
              checked={pick?.attachments.has(`${message.message_id}/${attachment.part_id}`) ?? false}
              onChange={(picked) =>
                onPickAttachment({ message_id: message.message_id, part_id: attachment.part_id }, picked)
              }
            />
          ))}
        </ul>
      ) : null}
    </article>
  );
}

type AttachmentRowProps = { attachment: GmailAttachmentInfo; checked: boolean; onChange: (picked: boolean) => void };

function AttachmentRow({ attachment, checked, onChange }: AttachmentRowProps) {
  const name = (
    <>
      <PaperclipIcon size={14} />
      <span className="conn-attachment-name">{attachment.filename || "(unnamed)"}</span>
    </>
  );
  return (
    <li className={attachment.importable ? "conn-attachment" : "conn-attachment conn-attachment-off"}>
      {attachment.importable ? (
        <label>
          <input
            type="checkbox"
            className="ui-check"
            checked={checked}
            onChange={(event) => onChange(event.target.checked)}
          />
          {name}
        </label>
      ) : (
        name
      )}
      <span className="conn-attachment-size">{formatBytes(attachment.size_bytes)}</span>
      {!attachment.importable ? (
        <span className="conn-attachment-reason">{attachment.reason ?? "Can't be imported"}</span>
      ) : null}
    </li>
  );
}

type ImportResultsProps = { items: GmailImportItem[]; summaryRef: RefObject<HTMLParagraphElement | null> };

function ImportResults({ items, summaryRef }: ImportResultsProps) {
  const saved = items.filter((item) => item.status === "imported" || item.status === "already_existed").length;
  return (
    <div className="conn-browser">
      <p ref={summaryRef} className="conn-search-meta conn-focus-target" role="status" tabIndex={-1}>
        {saved === 0
          ? "Nothing was imported."
          : `${plural(saved, "item")} saved to your documents as “Imported from Gmail”. They can be searched once indexing finishes.`}
      </p>
      <ul className="conn-results">
        {items.map((item, i) => {
          const view = importStatusView(item.status);
          return (
            <li key={`${item.kind}-${item.thread_id}-${item.message_id ?? ""}-${item.part_id ?? ""}-${i}`} className="conn-result">
              {item.kind === "thread" ? <DocsIcon size={16} /> : <PaperclipIcon size={16} />}
              <span className="conn-result-label">{item.label || "(no subject)"}</span>
              <span className={`badge badge-${view.tone}`}>{view.label}</span>
              {item.reason ? <span className="conn-result-reason">{item.reason}</span> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Rows shaped like conversations while the first page loads. */
function ThreadSkeleton() {
  return (
    <ul className="conn-threads" aria-hidden="true">
      {[64, 48, 72, 56].map((width) => (
        <li key={width} className="conn-thread conn-thread-skeleton">
          <span className="ui-skeleton conn-skeleton-check" />
          <span className="ui-skeleton" style={{ width: "22%" }} />
          <span className="ui-skeleton" style={{ width: `${width}%` }} />
        </li>
      ))}
    </ul>
  );
}
