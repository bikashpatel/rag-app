import { useRef, useState, type ReactNode } from "react";
import { ApiError, documentDownloadUrl, documentImageUrl } from "../../api/client";
import type { DocumentItem } from "../../api/types";
import { formatBytes, formatDateTime, formatNumber, formatRelativeTime } from "../../lib/format";
import { DownloadIcon, TrashIcon } from "../ui/icons";
import FileTag from "./FileTag";
import ImageViewer from "./ImageViewer";

type RowAction = "delete" | "reprocess";

type DocumentRowProps = {
  doc: DocumentItem;
  checked: boolean;
  onToggle: () => void;
  onDelete: (id: number) => Promise<void>;
  onReprocess: (id: number) => Promise<void>;
};

/** One part of the meta line; `origin` parts ("Created in chat", "Imported from Gmail") are set in the accent ink. */
type MetaPart = { text: string; origin?: boolean };

function countLabel(count: number, noun: string): string {
  return `${formatNumber(count)} ${noun}${count === 1 ? "" : "s"}`;
}

function metaParts(doc: DocumentItem): MetaPart[] {
  const parts: MetaPart[] = [{ text: formatBytes(doc.size_bytes) }];
  if (doc.image) parts.unshift({ text: `${doc.image.width} × ${doc.image.height}` });
  if (doc.generated) parts.unshift({ text: "Created in chat", origin: true });
  if (doc.imported_from) parts.unshift({ text: `Imported from ${doc.imported_from}`, origin: true });
  if (doc.status === "ready") parts.push({ text: countLabel(doc.num_chunks, "chunk") });
  if (doc.num_pages) parts.push({ text: countLabel(doc.num_pages, "page") });
  if (doc.status === "ready" && doc.vision_pages > 0) {
    parts.push({ text: `${countLabel(doc.vision_pages, "page")} read by vision` });
  }
  const added = formatRelativeTime(doc.created_at);
  if (added) parts.push({ text: added });
  return parts;
}

function checkboxHint(doc: DocumentItem): string {
  if (doc.status === "ready") return "Use in chat";
  if (doc.status === "failed") return "Indexing failed, so this document can't be searched";
  return "Available once indexing finishes";
}

/** One status vocabulary everywhere (visual spec 6.5): Queued, Indexing…, Failed, Needs re-index; no badge when ready. */
function statusBadge(doc: DocumentItem): ReactNode {
  switch (doc.status) {
    case "pending":
      return <span className="badge badge-neutral">Queued</span>;
    case "processing":
      return (
        <span className="badge badge-info">
          <span className="spinner" aria-hidden="true" />
          Indexing…
        </span>
      );
    case "failed":
      return <span className="badge badge-danger">Failed</span>;
    case "ready":
      return doc.is_stale ? (
        <span
          className="badge badge-warning"
          title="Indexed with an embedding model that is no longer active, so search skips it until it is re-indexed"
        >
          Needs re-index
        </span>
      ) : null;
  }
}

export default function DocumentRow({ doc, checked, onToggle, onDelete, onReprocess }: DocumentRowProps) {
  const [confirming, setConfirming] = useState(false);
  const [running, setRunning] = useState<RowAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState(false);
  const [thumbFailed, setThumbFailed] = useState(false);
  const deleteButtonRef = useRef<HTMLButtonElement>(null);
  const ready = doc.status === "ready";
  const canReprocess = doc.can_delete && (doc.status === "failed" || (ready && doc.is_stale));
  const badge = statusBadge(doc);
  const meta = metaParts(doc);
  const added = `${doc.generated ? "Created" : doc.imported_from ? "Imported" : "Uploaded"} ${formatDateTime(doc.created_at)}`;

  function closeConfirm() {
    setConfirming(false);
    deleteButtonRef.current?.focus();
  }

  async function run(action: RowAction) {
    setRunning(action);
    setError(null);
    try {
      await (action === "delete" ? onDelete(doc.id) : onReprocess(doc.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      if (action === "delete") closeConfirm();
    } finally {
      setRunning(null);
    }
  }

  const rowClass = ["docs-row", confirming ? "docs-row-confirming" : null, doc.can_delete ? null : "docs-row-one-action"]
    .filter(Boolean)
    .join(" ");

  return (
    <li className={rowClass}>
      <label className="docs-check" title={checkboxHint(doc)}>
        <input
          type="checkbox"
          className="ui-check"
          checked={checked}
          disabled={!ready}
          onChange={onToggle}
          aria-label={`Use ${doc.filename} in chat`}
        />
      </label>
      {doc.image ? (
        <button
          type="button"
          className="docs-thumb"
          onClick={() => setViewing(true)}
          aria-label={`View ${doc.filename}`}
          title="View"
        >
          {thumbFailed ? (
            <FileTag filename={doc.filename} />
          ) : (
            <img
              src={documentImageUrl(doc.id, "thumb")}
              alt=""
              width={34}
              height={40}
              loading="lazy"
              decoding="async"
              onError={() => setThumbFailed(true)}
            />
          )}
        </button>
      ) : (
        <FileTag filename={doc.filename} />
      )}
      <div className="docs-row-main">
        <div className="docs-row-name" title={doc.filename}>
          {doc.filename}
        </div>
        <div className="docs-row-meta" title={added}>
          {/* Parts wrap whole, so the time is never cut off; the "·" separators are CSS (see documents.css). */}
          <span className="docs-row-meta-parts" aria-hidden="true">
            {meta.map((part, i) => (
              <span key={i} className={part.origin ? "docs-row-meta-part docs-row-meta-origin" : "docs-row-meta-part"}>
                {part.text}
              </span>
            ))}
          </span>
          <span className="sr-only">{meta.map((part) => part.text).join(", ")}</span>
        </div>
        {badge || canReprocess ? (
          <div className="docs-status">
            {badge}
            {canReprocess ? (
              <button
                type="button"
                className="docs-link-btn"
                onClick={() => void run("reprocess")}
                disabled={running !== null}
              >
                {running === "reprocess" ? "Starting…" : doc.status === "failed" ? "Retry" : "Re-index"}
              </button>
            ) : null}
          </div>
        ) : null}
        {doc.status === "failed" ? (
          <p className="docs-row-error" title={doc.error ?? undefined}>
            {doc.error || "Indexing failed."}
          </p>
        ) : null}
        {error ? (
          <p className="docs-row-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <div className="docs-row-actions">
        <a
          className="docs-icon-btn"
          href={documentDownloadUrl(doc.id)}
          download
          aria-label={`Download ${doc.filename}`}
          title="Download"
        >
          <DownloadIcon size={16} />
        </a>
        {doc.can_delete ? (
          <button
            ref={deleteButtonRef}
            type="button"
            className="docs-icon-btn docs-icon-btn-danger"
            onClick={() => setConfirming((open) => !open)}
            aria-label={`Delete ${doc.filename}`}
            aria-expanded={confirming}
            title="Delete"
          >
            <TrashIcon size={16} />
          </button>
        ) : null}
      </div>
      {confirming ? (
        <div
          className="docs-confirm"
          role="group"
          aria-label={`Confirm deleting ${doc.filename}`}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            closeConfirm();
          }}
        >
          <p className="docs-confirm-title">Delete this document?</p>
          <p className="docs-confirm-text">It's removed from your documents and from search. This can't be undone.</p>
          <div className="docs-confirm-actions">
            <button type="button" className="btn btn-xs" onClick={closeConfirm} disabled={running === "delete"} autoFocus>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-xs btn-danger"
              onClick={() => void run("delete")}
              disabled={running === "delete"}
            >
              {running === "delete" ? "Deleting…" : "Delete document"}
            </button>
          </div>
        </div>
      ) : null}
      {viewing && doc.image ? (
        <ImageViewer documentId={doc.id} filename={doc.filename} image={doc.image} onClose={() => setViewing(false)} />
      ) : null}
    </li>
  );
}
