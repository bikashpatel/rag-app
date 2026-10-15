import type { ReactNode } from "react";
import { documentDownloadUrl } from "../../api/client";
import type { DocumentItem, DocumentStatus } from "../../api/types";
import { formatBytes, formatNumber } from "../../lib/format";
import { useDocumentSnapshot } from "../documents/documentEvents";
import FileTag from "../documents/FileTag";
import { fileTypeTag } from "../documents/fileTypes";
import { AlertIcon, CheckIcon, DownloadIcon } from "./icons";
import type { FileCard } from "./useChatStream";

type CardView = {
  /** The group's accessible name. */
  label: string;
  tone: "default" | "muted" | "failed";
  lead: ReactNode;
  headline: string;
  /** Long headlines wrap instead of being cut off (sentences rather than names). */
  wrap: boolean;
  /** Type, size and indexing status of a created file. */
  meta: ReactNode;
  error: string | null;
  /** Shown outside the live region, so updates every 2 s aren't read out. */
  progress: ReactNode;
  download: { id: number; filename: string } | null;
};

/** `status` null: the document was deleted. */
function statusText(status: DocumentStatus | null, alreadyExisted: boolean, error: string | null): ReactNode {
  switch (status) {
    case null:
      return <span className="chat-file-status">Deleted from your documents</span>;
    case "pending":
      return <span className="chat-file-status">Queued for indexing</span>;
    case "processing":
      return (
        <span className="chat-file-status">
          <span className="spinner chat-file-status-spinner" aria-hidden="true" />
          Indexing…
        </span>
      );
    case "ready":
      return (
        <span className="chat-file-status chat-file-status-ready">
          <CheckIcon size={13} />
          {alreadyExisted ? "Already in your documents" : "In your documents"}
        </span>
      );
    case "failed":
      return (
        <span className="chat-file-status chat-file-status-warning" title={error ?? undefined}>
          <AlertIcon size={13} />
          Saved, but couldn't be indexed
        </span>
      );
  }
}

function cardView(card: FileCard, doc: DocumentItem | null | undefined): CardView {
  switch (card.kind) {
    case "writing":
      return {
        label: "File being created",
        tone: "default",
        lead: <span className="spinner chat-file-spinner" aria-hidden="true" />,
        headline: card.title ? `Creating ${card.title}…` : "Creating a file…",
        wrap: false,
        meta: null,
        error: null,
        progress:
          card.format || card.chars > 0 ? (
            <>
              {card.format ? <FileTag filename={`file.${card.format}`} /> : null}
              {card.chars > 0 ? <span>{formatNumber(card.chars)} characters</span> : null}
            </>
          ) : null,
        download: null,
      };
    case "created": {
      const { file } = card;
      // The panel's list when it has the document (it polls while the document is indexed), else what the server
      // sent. Only a document the list had and then lost (doc null) counts as deleted here: a list that merely doesn't
      // know it yet (undefined) asks the panel to catch up and keeps the server's status and Download meanwhile.
      const status = file.document_id === null || doc === null ? null : (doc?.status ?? file.status);
      const id = status === null ? null : file.document_id;
      return {
        label: file.filename,
        tone: id === null ? "muted" : "default",
        lead: <FileTag filename={file.filename} />,
        headline: file.filename,
        wrap: false,
        meta: (
          <span className="chat-file-meta">
            <span className="chat-file-meta-parts">
              <span className="chat-file-meta-part">
                {fileTypeTag(file.filename).label} · {formatBytes(file.size_bytes)}
              </span>
              <span className="chat-file-meta-part">{statusText(status, card.alreadyExisted, doc?.error ?? null)}</span>
            </span>
          </span>
        ),
        error: null,
        progress: null,
        download: id === null ? null : { id, filename: file.filename },
      };
    }
    case "failed":
      return {
        label: card.title ?? "File",
        tone: "failed",
        lead: <AlertIcon size={18} />,
        headline: `Couldn't create ${card.title ?? "the file"}`,
        wrap: true,
        meta: null,
        error: card.error,
        progress: null,
        download: null,
      };
    case "stopped":
      return {
        label: card.title ?? "File",
        tone: "muted",
        lead: <FileTag filename={card.format ? `file.${card.format}` : "file"} />,
        headline: `${card.title ?? "File"}: stopped. If it was already being saved, it's in your documents.`,
        wrap: true,
        meta: null,
        error: null,
        progress: null,
        download: null,
      };
  }
}

/**
 * A file the assistant created (or tried to) in an answer: type, name, size, indexing status and Download. The text
 * block is one live region whose DOM node survives writing → created, so the change is announced.
 */
export default function GeneratedFileCard({ card }: { card: FileCard }) {
  const doc = useDocumentSnapshot(card.kind === "created" ? card.file.document_id : null);
  const view = cardView(card, doc);
  return (
    <div className={`chat-file chat-file-${view.tone}`} role="group" aria-label={view.label}>
      <span className="chat-file-lead">{view.lead}</span>
      <div className="chat-file-body">
        <div className="chat-file-text" aria-live="polite">
          <span
            className={view.wrap ? "chat-file-headline chat-file-headline-wrap" : "chat-file-headline"}
            title={view.wrap ? undefined : view.headline}
          >
            {view.headline}
          </span>
          {view.meta}
          {view.error ? <span className="chat-file-error">{view.error}</span> : null}
        </div>
        {view.progress ? <div className="chat-file-progress">{view.progress}</div> : null}
      </div>
      {view.download ? (
        <a
          className="btn btn-sm chat-file-download"
          href={documentDownloadUrl(view.download.id)}
          download
          aria-label={`Download ${view.download.filename}`}
        >
          <DownloadIcon size={14} />
          Download
        </a>
      ) : null}
    </div>
  );
}
