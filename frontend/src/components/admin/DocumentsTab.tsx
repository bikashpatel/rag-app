import { useEffect, useMemo, useRef, useState } from "react";
import type { DocumentItem, DocumentStatus } from "../../api/types";
import { formatBytes, formatDateTime, formatNumber, formatRelativeTime } from "../../lib/format";
import { deleteResource, queuedMessage, reindexDocuments } from "./adminApi";
import FileTag from "../documents/FileTag";
import ConfirmAction from "./ConfirmAction";
import FlashMessage from "./FlashMessage";
import { useFlash, usePolling, useResource } from "./hooks";
import { EmptyState, ResourceGate } from "./StateViews";

const POLL_MS = 4000;

type StatusFilter = "all" | DocumentStatus | "stale";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "ready", label: "Ready" },
  { value: "pending", label: "Queued" },
  { value: "processing", label: "Indexing" },
  { value: "failed", label: "Failed" },
  { value: "stale", label: "Needs re-index" },
];

const STATUS_BADGES: Record<DocumentStatus, { label: string; className: string }> = {
  pending: { label: "Queued", className: "badge badge-neutral" },
  processing: { label: "Indexing…", className: "badge badge-info" },
  ready: { label: "Ready", className: "badge badge-success" },
  failed: { label: "Failed", className: "badge badge-danger" },
};

function matchesStatus(doc: DocumentItem, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  return filter === "stale" ? doc.is_stale : doc.status === filter;
}

export default function DocumentsTab({ onChanged }: { onChanged: () => void }) {
  const docs = useResource<DocumentItem[]>("/admin/documents", "Couldn't load documents.");
  const flash = useFlash();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  const inProgress = docs.data?.some((doc) => doc.status === "pending" || doc.status === "processing") ?? false;
  usePolling(inProgress, POLL_MS, () => {
    if (!docs.loading) void docs.reload();
  });

  const wasInProgress = useRef(false);
  useEffect(() => {
    if (wasInProgress.current && !inProgress) onChanged();
    wasInProgress.current = inProgress;
  }, [inProgress, onChanged]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (docs.data ?? []).filter(
      (doc) =>
        matchesStatus(doc, status) &&
        (!needle || doc.filename.toLowerCase().includes(needle) || doc.owner_username.toLowerCase().includes(needle)),
    );
  }, [docs.data, query, status]);

  async function deleteDocument(doc: DocumentItem) {
    await deleteResource(`/admin/documents/${doc.id}`);
    docs.setData((list) => list && list.filter((d) => d.id !== doc.id));
    flash.success(`Deleted ${doc.filename}.`);
    onChanged();
  }

  async function reindexAll() {
    flash.success(queuedMessage(await reindexDocuments(false)));
    onChanged();
    await docs.reload();
  }

  return (
    <section className="card admin-card" aria-labelledby="admin-docs-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-docs-title" className="admin-card-title">
            Documents
          </h2>
          <p className="admin-card-subtitle">Every user's private documents and the shared knowledge base.</p>
        </div>
        <span className="spacer" />
        <ConfirmAction
          label="Re-index all documents"
          tone="primary"
          triggerClassName="btn btn-sm"
          disabled={!docs.data?.length}
          message="Re-embed every document with the active embedding model? This uses embedding tokens, and vision tokens for PDF pages not yet read with the active vision model. It can take a while."
          confirmLabel="Re-index all"
          onConfirm={reindexAll}
        />
      </div>
      <FlashMessage flash={flash} />
      <ResourceGate resource={docs} loadingLabel="Loading documents">
        {(list) =>
          list.length === 0 ? (
            <EmptyState title="No documents yet.">
              <p className="muted">Users upload documents from the panel on the chat page.</p>
            </EmptyState>
          ) : (
            <>
              <div className="admin-toolbar">
                <input
                  type="search"
                  className="input admin-search"
                  placeholder="Filter by file or owner"
                  aria-label="Filter by file name or owner"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <select
                  className="select admin-status-filter"
                  aria-label="Filter by status"
                  value={status}
                  onChange={(e) => setStatus(e.target.value as StatusFilter)}
                >
                  {STATUS_FILTERS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <span className="admin-toolbar-count" aria-live="polite">
                  {visible.length === list.length
                    ? `${formatNumber(list.length)} document${list.length === 1 ? "" : "s"}`
                    : `${formatNumber(visible.length)} of ${formatNumber(list.length)}`}
                </span>
              </div>
              {visible.length === 0 ? (
                <EmptyState title="No documents match these filters.">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      setQuery("");
                      setStatus("all");
                    }}
                  >
                    Clear filters
                  </button>
                </EmptyState>
              ) : (
                <div className="table-wrap">
                  <table className="table admin-table">
                    <thead>
                      <tr>
                        <th scope="col">File</th>
                        <th scope="col">Owner</th>
                        <th scope="col">Scope</th>
                        <th scope="col">Status</th>
                        <th scope="col" className="num">
                          Size
                        </th>
                        <th scope="col" className="num">
                          Chunks
                        </th>
                        <th scope="col">Created</th>
                        <th scope="col" className="admin-actions-cell">
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((doc) => (
                        <DocumentRow key={doc.id} doc={doc} onDelete={() => deleteDocument(doc)} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )
        }
      </ResourceGate>
    </section>
  );
}

function DocumentRow({ doc, onDelete }: { doc: DocumentItem; onDelete: () => Promise<void> }) {
  // One status per document (visual spec 6.5): a stale ready document reads "Needs re-index", not "Ready" as well.
  const badge = doc.is_stale ? { label: "Needs re-index", className: "badge badge-warning" } : STATUS_BADGES[doc.status];
  const visionPages = `${formatNumber(doc.vision_pages)} page${doc.vision_pages === 1 ? "" : "s"} read with the vision model`;
  return (
    <tr>
      <td>
        <span className="admin-file-cell">
          <FileTag filename={doc.filename} size="sm" />
          <span className="admin-filename" title={doc.filename}>
            {doc.filename}
          </span>
        </span>
      </td>
      <td className="admin-nowrap">{doc.owner_username}</td>
      <td>
        <span className={doc.scope === "shared" ? "badge badge-accent" : "badge badge-neutral"}>
          {doc.scope === "shared" ? "Shared" : "Private"}
        </span>
      </td>
      <td>
        <span className="admin-badges">
          <span className={badge.className} title={doc.error ?? undefined}>
            {doc.status === "processing" ? <span className="spinner" aria-hidden="true" /> : null}
            {badge.label}
          </span>
          {doc.error ? <span className="sr-only">: {doc.error}</span> : null}
          {doc.vision_pages > 0 ? (
            <span className="badge badge-neutral" title={visionPages}>
              <span aria-hidden="true">Vision · {formatNumber(doc.vision_pages)}</span>
              <span className="sr-only">{visionPages}</span>
            </span>
          ) : null}
        </span>
      </td>
      <td className="num admin-nowrap">{formatBytes(doc.size_bytes)}</td>
      <td className="num">{formatNumber(doc.num_chunks)}</td>
      <td className="admin-nowrap">
        <time dateTime={doc.created_at} title={formatDateTime(doc.created_at)}>
          {formatRelativeTime(doc.created_at)}
        </time>
      </td>
      <td className="admin-actions-cell">
        <span className="admin-actions">
          <ConfirmAction
            label="Delete"
            ariaLabel={`Delete ${doc.filename}`}
            message={
              <>
                Delete <strong>{doc.filename}</strong>
                {doc.scope === "shared" ? " from the shared knowledge base" : ` (owned by ${doc.owner_username})`}?
              </>
            }
            confirmLabel="Delete"
            onConfirm={onDelete}
          />
        </span>
      </td>
    </tr>
  );
}
