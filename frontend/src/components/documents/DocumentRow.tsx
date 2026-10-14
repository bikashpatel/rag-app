import { useRef, useState, type ReactNode } from "react";
import { ApiError } from "../../api/client";
import type { DocumentItem } from "../../api/types";
import { formatBytes, formatDateTime, formatNumber, formatRelativeTime } from "../../lib/format";
import FileTag from "./FileTag";
import { TrashIcon } from "./icons";

type RowAction = "delete" | "reprocess";

type DocumentRowProps = {
  doc: DocumentItem;
  checked: boolean;
  onToggle: () => void;
  onDelete: (id: number) => Promise<void>;
  onReprocess: (id: number) => Promise<void>;
};

function countLabel(count: number, noun: string): string {
  return `${formatNumber(count)} ${noun}${count === 1 ? "" : "s"}`;
}

function metaParts(doc: DocumentItem): string[] {
  const parts = [formatBytes(doc.size_bytes)];
  if (doc.status === "ready") parts.push(countLabel(doc.num_chunks, "chunk"));
  if (doc.num_pages) parts.push(countLabel(doc.num_pages, "page"));
  if (doc.status === "ready" && doc.vision_pages > 0) parts.push(`${countLabel(doc.vision_pages, "page")} read by vision`);
  const uploaded = formatRelativeTime(doc.created_at);
  if (uploaded) parts.push(uploaded);
  return parts;
}

function checkboxHint(doc: DocumentItem): string {
  if (doc.status === "ready") return "Use in chat";
  if (doc.status === "failed") return "Processing failed, so this document can't be searched";
  return "Available once processing finishes";
}

function statusBadge(doc: DocumentItem): ReactNode {
  switch (doc.status) {
    case "pending":
      return <span className="badge badge-neutral">Queued</span>;
    case "processing":
      return (
        <span className="badge badge-info">
          <span className="spinner docs-spinner" aria-hidden="true" />
          Processing
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
  const deleteButtonRef = useRef<HTMLButtonElement>(null);
  const ready = doc.status === "ready";
  const canReprocess = doc.can_delete && (doc.status === "failed" || (ready && doc.is_stale));
  const badge = statusBadge(doc);
  const meta = metaParts(doc);

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

  return (
    <li className="docs-row">
      <label className="docs-check" title={checkboxHint(doc)}>
        <input
          type="checkbox"
          checked={checked}
          disabled={!ready}
          onChange={onToggle}
          aria-label={`Use ${doc.filename} in chat`}
        />
      </label>
      <FileTag filename={doc.filename} />
      <div className="docs-row-main">
        <div className="docs-row-name" title={doc.filename}>
          {doc.filename}
        </div>
        <div className="docs-row-meta" title={`Uploaded ${formatDateTime(doc.created_at)}`}>
          {/* Parts wrap whole, so the upload time is never cut off; the "·" separators are CSS (see documents.css). */}
          <span className="docs-row-meta-parts" aria-hidden="true">
            {meta.map((part, i) => (
              <span key={i} className="docs-row-meta-part">
                {part}
              </span>
            ))}
          </span>
          <span className="sr-only">{meta.join(", ")}</span>
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
            {doc.error || "Processing failed."}
          </p>
        ) : null}
        {error ? (
          <p className="docs-row-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
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
          <TrashIcon size={15} />
        </button>
      ) : null}
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
          <span className="docs-confirm-text">Delete this document? This can't be undone.</span>
          <div className="docs-confirm-actions">
            <button type="button" className="btn btn-sm" onClick={closeConfirm} disabled={running === "delete"} autoFocus>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger"
              onClick={() => void run("delete")}
              disabled={running === "delete"}
            >
              {running === "delete" ? "Deleting…" : "Delete"}
            </button>
          </div>
        </div>
      ) : null}
    </li>
  );
}
