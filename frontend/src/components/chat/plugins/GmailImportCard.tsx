import type { ReactNode } from "react";
import type { GmailImportCard as ImportCard, GmailImportItem } from "../../../api/types";
import { useDocumentSnapshot } from "../../documents/documentEvents";
import { StatusText } from "../GeneratedFileCard";
import { AlertIcon, DocumentsIcon, MailIcon, PaperclipIcon } from "../icons";
import { importStatusView, plural } from "./email";

function headline(card: ImportCard, stopped: boolean): string {
  if (stopped) return "Stopped; it may have finished. Check your documents.";
  const saved = card.items.filter((item) => item.status === "imported" || item.status === "already_existed").length;
  switch (card.status) {
    case "importing":
      return "Importing from Gmail…";
    case "done":
      return saved === 0 ? "Nothing was imported from Gmail" : `Imported ${plural(saved, "item")} from Gmail`;
    case "failed":
      return "Couldn't import from Gmail";
  }
}

/** Threads and attachments the assistant saved to the user's documents, with each document's indexing status. */
export default function GmailImportCard({ card, stopped }: { card: ImportCard; stopped: boolean }) {
  const busy = card.status === "importing" && !stopped;
  const failed = card.status === "failed";
  let lead: ReactNode = <MailIcon size={16} />;
  if (busy) lead = <span className="spinner chat-mail-spinner" aria-hidden="true" />;
  else if (failed) lead = <AlertIcon size={16} />;
  return (
    <div
      className={`chat-mail-card${failed ? " chat-mail-card-failed" : stopped ? " chat-mail-card-muted" : ""}`}
      role="group"
      aria-label="Import from Gmail"
    >
      <div className="chat-mail-head">
        <span className="chat-mail-lead">{lead}</span>
        <span className="chat-mail-title" aria-live="polite">
          {headline(card, stopped)}
        </span>
      </div>
      {card.items.length > 0 ? (
        <ul className="chat-mail-import-items">
          {card.items.map((item, i) => (
            <ImportItemRow key={`${item.kind}-${item.thread_id}-${item.message_id ?? ""}-${item.part_id ?? ""}-${i}`} item={item} />
          ))}
        </ul>
      ) : null}
      {failed && card.error ? <p className="chat-mail-error">{card.error}</p> : null}
      {card.status === "done" ? (
        <p className="chat-mail-note">Imported items are private documents marked “Imported from Gmail”.</p>
      ) : null}
    </div>
  );
}

function ImportItemRow({ item }: { item: GmailImportItem }) {
  const saved = item.status === "imported" || item.status === "already_existed";
  const doc = useDocumentSnapshot(saved ? item.document_id : null);
  const view = importStatusView(item.status);
  // The panel's list once it has the document (it polls while indexing), else the state the card was saved with.
  const status = !saved || item.document_id === null || doc === null ? null : (doc?.status ?? item.document?.status ?? null);
  return (
    <li className="chat-mail-import-item">
      {item.kind === "thread" ? <DocumentsIcon size={14} /> : <PaperclipIcon size={14} />}
      <span className="chat-mail-import-label" title={item.label}>
        {item.label || "(no subject)"}
      </span>
      {saved ? (
        <StatusText
          status={status}
          stale={doc?.is_stale ?? false}
          alreadyExisted={item.status === "already_existed"}
          error={doc?.error ?? null}
        />
      ) : (
        <span className={`badge badge-${view.tone}`}>{view.label}</span>
      )}
      {item.reason ? <span className="chat-mail-import-reason">{item.reason}</span> : null}
    </li>
  );
}
