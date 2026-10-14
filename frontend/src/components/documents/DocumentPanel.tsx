import { useEffect, useId, useState, type JSX, type KeyboardEvent } from "react";
import type { DocumentConfig, DocumentScope, DocumentSelection } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import DocumentList from "./DocumentList";
import "./documents.css";
import { BYTES_PER_MB, supportedTypeLabels, uploadRules } from "./fileTypes";
import { AlertIcon, CloseIcon } from "./icons";
import QuotaFooter from "./QuotaFooter";
import ScopeToggle from "./ScopeToggle";
import { pruneSelection } from "./selection";
import SelectionBar from "./SelectionBar";
import UploadDropzone from "./UploadDropzone";
import UploadQueue from "./UploadQueue";
import { uploadManager, useUploads } from "./uploadManager";
import { useDocuments } from "./useDocuments";
import { useFileDrop } from "./useFileDrop";

export type { DocumentSelection } from "../../api/types";

export interface DocumentPanelProps {
  /** null = all accessible documents (new uploads are included automatically); number[] = only these ids ([] = none). */
  selection: DocumentSelection;
  onSelectionChange: (selection: DocumentSelection) => void;
  /** Slide-over mode: renders a close button, and Escape inside the panel calls it too. */
  onClose?: () => void;
}

function dropzoneHint(config: DocumentConfig | null, configError: string | null): string {
  if (config) {
    return `${supportedTypeLabels(config.supported_extensions).join(", ")} · up to ${config.max_upload_mb} MB each`;
  }
  return configError ? "Uploads are unavailable until the upload settings load." : "Loading upload settings…";
}

function quotaWarning(config: DocumentConfig | null): string | null {
  if (!config) return null;
  if (config.max_documents > 0 && config.document_count >= config.max_documents) {
    return "You've reached your document limit. Delete a document to upload another.";
  }
  if (config.max_storage_mb > 0 && config.storage_bytes >= config.max_storage_mb * BYTES_PER_MB) {
    return "Your storage is full. Delete a document to free up space.";
  }
  return null;
}

export default function DocumentPanel({ selection, onSelectionChange, onClose }: DocumentPanelProps): JSX.Element {
  const titleId = useId();
  const { user } = useAuth();
  const ownerId = user?.id ?? null;
  const uploads = useUploads(ownerId);
  const uploadsActive = uploads.some((item) => item.status !== "failed");
  const {
    documents,
    config,
    documentsError,
    configError,
    reloadDocuments,
    reloadConfig,
    upsertDocument,
    deleteDocument,
    reprocessDocument,
  } = useDocuments(uploadsActive);
  const [scope, setScope] = useState<DocumentScope>("private");
  const [announcement, setAnnouncement] = useState("");

  const canUpload = config !== null && ownerId !== null;
  const canShare = config?.can_upload_shared ?? false;

  function enqueue(files: File[], folders: string[] = []) {
    if (!config || ownerId === null) return;
    uploadManager.enqueue({ ownerId, scope: canShare ? scope : "private", rules: uploadRules(config), files, folders });
  }

  const { dragActive, dropHandlers } = useFileDrop(enqueue, canUpload);

  useEffect(
    () =>
      uploadManager.onSettled((created) => {
        if (created) {
          upsertDocument(created);
          setAnnouncement(`${created.filename} uploaded. It can be searched once processing finishes.`);
        }
        void reloadConfig();
      }),
    [upsertDocument, reloadConfig],
  );

  useEffect(() => {
    if (documents === null || selection === null) return;
    const pruned = pruneSelection(selection, documents);
    if (pruned !== selection) onSelectionChange(pruned);
  }, [documents, selection, onSelectionChange]);

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && onClose && !event.defaultPrevented) onClose();
  }

  return (
    <aside
      className={dragActive ? "docs-panel docs-panel-dragging" : "docs-panel"}
      aria-labelledby={titleId}
      onKeyDown={handleKeyDown}
      {...dropHandlers}
    >
      <header className="docs-header">
        <h2 id={titleId} className="docs-title">
          Documents
        </h2>
        {onClose ? (
          <button
            type="button"
            className="btn btn-ghost btn-icon"
            onClick={onClose}
            aria-label="Close documents panel"
            title="Close"
          >
            <CloseIcon size={18} />
          </button>
        ) : null}
      </header>

      <div className="docs-upload">
        {canShare ? <ScopeToggle value={scope} onChange={setScope} /> : null}
        <UploadDropzone
          accept={config?.supported_extensions.join(",") ?? ""}
          hint={dropzoneHint(config, configError)}
          warning={quotaWarning(config)}
          disabled={!canUpload}
          dragActive={dragActive}
          onFiles={enqueue}
        />
        {configError ? <ErrorNotice message={configError} onRetry={reloadConfig} /> : null}
      </div>

      <div className="docs-scroll">
        <UploadQueue items={uploads} onRemove={(id) => uploadManager.remove(id)} />
        {documents === null ? (
          documentsError ? (
            <LoadError message={documentsError} onRetry={reloadDocuments} />
          ) : (
            <ListSkeleton />
          )
        ) : (
          <>
            {documents.length > 0 ? (
              <SelectionBar documents={documents} selection={selection} onSelectionChange={onSelectionChange} />
            ) : null}
            {documentsError ? <ErrorNotice message={documentsError} onRetry={reloadDocuments} /> : null}
            <DocumentList
              documents={documents}
              uploadsActive={uploadsActive}
              selection={selection}
              onSelectionChange={onSelectionChange}
              canShare={canShare}
              supportedTypes={config ? supportedTypeLabels(config.supported_extensions) : []}
              onDelete={deleteDocument}
              onReprocess={reprocessDocument}
            />
          </>
        )}
      </div>

      {config ? <QuotaFooter config={config} /> : null}
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>
    </aside>
  );
}

function RetryButton({ onRetry }: { onRetry: () => Promise<void> }) {
  const [retrying, setRetrying] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-sm"
      disabled={retrying}
      onClick={() => {
        setRetrying(true);
        void onRetry().finally(() => setRetrying(false));
      }}
    >
      {retrying ? "Retrying…" : "Retry"}
    </button>
  );
}

function ErrorNotice({ message, onRetry }: { message: string; onRetry: () => Promise<void> }) {
  return (
    <div className="docs-notice" role="alert">
      <span className="docs-notice-text">{message}</span>
      <RetryButton onRetry={onRetry} />
    </div>
  );
}

function LoadError({ message, onRetry }: { message: string; onRetry: () => Promise<void> }) {
  return (
    <div className="docs-state" role="alert">
      <span className="docs-state-icon docs-state-icon-danger">
        <AlertIcon size={22} />
      </span>
      <p className="docs-state-title">Documents unavailable</p>
      <p className="docs-state-text">{message}</p>
      <RetryButton onRetry={onRetry} />
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="docs-skeleton" role="status">
      <span className="sr-only">Loading documents…</span>
      {[0, 1, 2].map((row) => (
        <div key={row} className="docs-skeleton-row" aria-hidden="true">
          <span className="docs-skeleton-block docs-skeleton-tag" />
          <span className="docs-skeleton-lines">
            <span className="docs-skeleton-block" />
            <span className="docs-skeleton-block docs-skeleton-short" />
          </span>
        </div>
      ))}
    </div>
  );
}
