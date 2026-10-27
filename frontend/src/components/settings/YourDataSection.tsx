import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api, ApiError, documentDownloadUrl } from "../../api/client";
import type { ConversationSummary, DocumentItem } from "../../api/types";
import { formatBytes, formatDate } from "../../lib/format";
import "../billing/billing.css";
import FileTag from "../documents/FileTag";
import { AlertCircleIcon, DownloadIcon, TrashIcon } from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";
import { useLoad } from "./useLoad";

/** Rows shown before "Show all". */
const FIRST_ROWS = 20;

function sourceOf(doc: DocumentItem): string {
  if (doc.image) return "Image";
  if (doc.generated) return "Generated file";
  return doc.imported_from ? `From ${doc.imported_from}` : "Uploaded";
}

type Row = { id: number; name: string; detail: string; icon?: ReactNode; download?: string };

type ListProps = {
  label: string;
  noun: string;
  empty: string;
  rows: Row[] | null;
  error: string | null;
  onRetry: () => void;
  /** Deletes on the server; the row goes once it resolves. */
  onDelete: (id: number) => Promise<void>;
};

/** One list of "Your data": rows with Download (documents) and Delete with an inline confirmation (visual 8.5). */
function DataList({ label, noun, empty, rows, error, onRetry, onDelete }: ListProps) {
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [all, setAll] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState<ReadonlySet<number>>(new Set());
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (confirming !== null) cancelRef.current?.focus();
  }, [confirming]);

  async function remove(id: number) {
    if (deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete(id);
      setDeleted((current) => new Set([...current, id]));
      setConfirming(null);
      headingRef.current?.focus();
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : `Couldn't delete the ${noun}. Please try again.`);
    } finally {
      setDeleting(false);
    }
  }

  const visible = rows?.filter((row) => !deleted.has(row.id)) ?? null;
  let content: ReactNode;
  if (visible === null) {
    content = error ? (
      <div className="ui-notice ui-notice-danger" role="alert">
        <AlertCircleIcon size={15} />
        <span className="ui-notice-text">{error}</span>
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Try again
        </button>
      </div>
    ) : (
      <p className="settings-desc" role="status">
        Loading…
      </p>
    );
  } else if (visible.length === 0) {
    content = <p className="settings-desc">{empty}</p>;
  } else {
    const shown = all ? visible : visible.slice(0, FIRST_ROWS);
    content = (
      <>
        <ul className="billing-list" aria-labelledby={headingId}>
          {shown.map((row) => (
            <li key={row.id} className="billing-list-row">
              {row.icon ? (
                <span className="billing-list-icon" aria-hidden="true">
                  {row.icon}
                </span>
              ) : null}
              <div className="billing-list-text">
                <b title={row.name}>{row.name}</b>
                <span>{row.detail}</span>
              </div>
              {confirming === row.id ? (
                <div className="billing-list-confirm" role="group" aria-label={`Delete ${row.name}`}>
                  <p>Delete “{row.name}”? It can't be undone.</p>
                  <button
                    type="button"
                    className="btn btn-danger btn-xs"
                    aria-disabled={deleting || undefined}
                    onClick={() => void remove(row.id)}
                  >
                    {deleting ? <ButtonSpinner size={11} /> : null}
                    Delete
                  </button>
                  <button
                    ref={cancelRef}
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => {
                      setConfirming(null);
                      setDeleteError(null);
                    }}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="billing-list-actions">
                  {row.download ? (
                    <a className="btn btn-ghost btn-xs" href={row.download} aria-label={`Download ${row.name}`}>
                      <DownloadIcon size={14} />
                      Download
                    </a>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-ghost btn-danger btn-xs"
                    aria-label={`Delete ${row.name}…`}
                    onClick={() => {
                      setDeleteError(null);
                      setConfirming(row.id);
                    }}
                  >
                    <TrashIcon size={14} />
                    Delete…
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
        {visible.length > FIRST_ROWS ? (
          <button
            type="button"
            className="btn btn-ghost btn-sm billing-list-more"
            onClick={() => setAll((value) => !value)}
          >
            {all ? "Show fewer" : `Show all ${visible.length}`}
          </button>
        ) : null}
        {deleteError ? (
          <p className="error-text" role="alert">
            <AlertCircleIcon size={13} />
            {deleteError}
          </p>
        ) : null}
      </>
    );
  }

  return (
    <div role="group" aria-labelledby={headingId}>
      <h3 className="settings-sub" id={headingId} ref={headingRef} tabIndex={-1}>
        {label}
      </h3>
      {content}
    </div>
  );
}

/** DELETE that also takes "already gone" as done. */
async function deleteGone(path: string): Promise<void> {
  try {
    await api.del(path);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) throw err;
  }
}

/**
 * Settings → Plugins & data for a user without access (functional 8.8): their own documents (download, delete) and
 * chats (delete), so getting their data out never depends on paying.
 */
export default function YourDataSection() {
  const documents = useLoad(
    (signal) => api.get<DocumentItem[]>("/documents", { signal }),
    "Couldn't load your documents.",
  );
  const chats = useLoad(
    (signal) => api.get<ConversationSummary[]>("/conversations", { signal }),
    "Couldn't load your chats.",
  );
  const documentRows =
    documents.data
      ?.filter((doc) => doc.can_delete)
      .map<Row>((doc) => ({
        id: doc.id,
        name: doc.filename,
        detail: `${formatBytes(doc.size_bytes)} · ${formatDate(doc.created_at)} · ${sourceOf(doc)}`,
        icon: <FileTag filename={doc.filename} size="sm" />,
        download: documentDownloadUrl(doc.id),
      })) ?? null;
  const chatRows =
    chats.data?.map<Row>((chat) => ({
      id: chat.id,
      name: chat.title,
      detail: formatDate(chat.updated_at),
    })) ?? null;

  return (
    <section className="billing-yourdata" aria-label="Your data">
      <p className="settings-desc">
        Everything comes back when you choose a plan. To keep a copy of your chats, use Export your data below.
      </p>
      <DataList
        label="Your documents"
        noun="document"
        empty="No documents."
        rows={documentRows}
        error={documents.error}
        onRetry={documents.reload}
        onDelete={(id) => deleteGone(`/documents/${id}`)}
      />
      <DataList
        label="Your chats"
        noun="chat"
        empty="No chats."
        rows={chatRows}
        error={chats.error}
        onRetry={chats.reload}
        onDelete={(id) => deleteGone(`/conversations/${id}`)}
      />
    </section>
  );
}
