import type { ReactNode } from "react";
import { documentDownloadUrl } from "../../api/client";
import type { DocumentItem, DocumentStatus } from "../../api/types";
import { formatBytes, formatNumber } from "../../lib/format";
import { useDocumentSnapshot } from "../documents/documentEvents";
import FileTag from "../documents/FileTag";
import { fileTypeTag } from "../documents/fileTypes";
import { AlertCircleIcon, AlertIcon, CheckIcon, DownloadIcon } from "../ui/icons";
import type { FileCard } from "./useChatStream";

type CardView = {
  /** The group's accessible name. */
  label: string;
  tone: "default" | "muted" | "failed" | "busy";
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

const KIND_NAMES: Record<string, string> = {
  docx: "Word",
  xlsx: "Excel",
  pptx: "PowerPoint",
  md: "Markdown",
  markdown: "Markdown",
  txt: "Text",
};

/** "Word", "Excel", "PDF", "PNG" … for a card's meta line. */
export function kindName(filename: string): string {
  const extension = filename.slice(filename.lastIndexOf(".") + 1).toLowerCase();
  return KIND_NAMES[extension] ?? fileTypeTag(filename).label;
}

type StatusTextProps = {
  status: DocumentStatus | null;
  /** Indexed with an embedding model that is no longer active (DocumentItem.is_stale, from the panel's list). */
  stale: boolean;
  alreadyExisted: boolean;
  error: string | null;
};

/**
 * Where a created file or image is now (`status` null: the document was deleted), in the one status vocabulary of
 * the app: Queued, Indexing…, Ready, Needs re-index, Failed (visual spec 6.5). Also used by image and Gmail import cards.
 */
export function StatusText({ status, stale, alreadyExisted, error }: StatusTextProps) {
  switch (status) {
    case null:
      return <span className="chat-file-status chat-file-status-muted">Deleted from your documents</span>;
    case "pending":
      return <span className="chat-file-status chat-file-status-muted">Queued</span>;
    case "processing":
      return (
        <span className="chat-file-status chat-file-status-info">
          <span className="spinner" aria-hidden="true" />
          Indexing…
        </span>
      );
    case "ready":
      return stale ? (
        <span
          className="chat-file-status chat-file-status-warning"
          title="Indexed with an embedding model that is no longer active, so search skips it until it is re-indexed"
        >
          <AlertIcon size={14} />
          Needs re-index
        </span>
      ) : (
        <span className="chat-file-status chat-file-status-ready">
          <CheckIcon size={14} />
          {alreadyExisted ? "Already in your documents" : "Ready"}
        </span>
      );
    case "failed":
      return (
        <span className="chat-file-status chat-file-status-failed" title={error ?? undefined}>
          <AlertCircleIcon size={14} />
          Saved, but indexing failed
        </span>
      );
  }
}

/** A card of a create_document call (images have GeneratedImageCard). */
type FileOnlyCard = Exclude<FileCard, { kind: "generating" }>;

function cardView(card: FileOnlyCard, doc: DocumentItem | null | undefined): CardView {
  switch (card.kind) {
    case "writing":
      return {
        label: "File being created",
        tone: "busy",
        lead: card.format ? <FileTag filename={`file.${card.format}`} /> : <span className="spinner chat-file-spinner" aria-hidden="true" />,
        headline: card.title ? `Creating ${card.title}…` : "Creating a file…",
        wrap: false,
        meta: (
          <span className="chat-file-meta">
            <span>
              <span className="chat-file-status chat-file-status-info">
                <span className="spinner" aria-hidden="true" />
                Writing the file…
              </span>
            </span>
          </span>
        ),
        error: null,
        progress: (
          <>
            {card.chars > 0 ? <span className="chat-file-chars">{formatNumber(card.chars)} characters</span> : null}
            <span className="chat-file-bar" aria-hidden="true">
              <span />
            </span>
          </>
        ),
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
            <span>{kindName(file.filename)}</span>
            <span>{formatBytes(file.size_bytes)}</span>
            <span>
              <StatusText
                status={status}
                stale={doc?.is_stale ?? false}
                alreadyExisted={card.alreadyExisted}
                error={doc?.error ?? null}
              />
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
        lead: (
          <span className="chat-file-icon">
            <AlertCircleIcon size={20} />
          </span>
        ),
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

/** Download as an sm button; icon only on phones (chat.css). */
export function DownloadButton({ id, filename }: { id: number; filename: string }) {
  return (
    <a className="btn btn-sm chat-file-download" href={documentDownloadUrl(id)} download aria-label={`Download ${filename}`}>
      <DownloadIcon size={16} />
      <span className="chat-dl-label">Download</span>
    </a>
  );
}

/**
 * A file the assistant created (or tried to) in an answer: type, name, size, indexing status and Download. The text
 * block is one live region whose DOM node survives writing → created, so the change is announced.
 */
export default function GeneratedFileCard({ card }: { card: FileOnlyCard }) {
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
      {view.download ? <DownloadButton {...view.download} /> : null}
    </div>
  );
}
