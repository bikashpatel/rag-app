import { useEffect, useId, useState, type JSX, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import type { DocumentConfig, DocumentScope, DocumentSelection } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import GmailImportModal from "../connections/GmailImportModal";
import { gmailReady, requestConnections, usePlugins } from "../connections/pluginStore";
import { formatBytes, formatMegabytes, formatNumber } from "../../lib/format";
import { AlertCircleIcon, AlertIcon, CloseIcon, MailIcon } from "../ui/icons";
import DocumentList from "./DocumentList";
import "./documents.css";
import { BYTES_PER_MB, supportedTypeLabels, uploadRules } from "./fileTypes";
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

/** Over a limit the plan lowered (B28): the documents are kept, adding more waits until the user is under it. */
function overLimit(config: DocumentConfig | null): string | null {
  if (!config) return null;
  const maxBytes = config.max_storage_mb * BYTES_PER_MB;
  const advice = "Delete some or upgrade to add more.";
  if (config.max_documents > 0 && config.document_count > config.max_documents) {
    const allows = formatNumber(config.max_documents);
    return `You have ${formatNumber(config.document_count)} documents; your plan allows ${allows}. ${advice}`;
  }
  if (config.max_storage_mb > 0 && config.storage_bytes > maxBytes) {
    const allows = formatMegabytes(config.max_storage_mb);
    return `You use ${formatBytes(config.storage_bytes)}; your plan allows ${allows}. ${advice}`;
  }
  return null;
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
  const hasAccess = user === null || user.is_admin || user.plan.access;
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
  } = useDocuments(uploadsActive, hasAccess);
  const [scope, setScope] = useState<DocumentScope>("private");
  const [announcement, setAnnouncement] = useState("");
  const [importing, setImporting] = useState(false);
  const { plugins } = usePlugins();
  const gmail = gmailReady(plugins);
  const gmailUsable = gmail !== null;
  // Import would work again after reconnecting: offer that instead of hiding the button without a word.
  const gmailPlugin = plugins?.find((plugin) => plugin.id === "gmail");
  const gmailReconnect =
    gmail === null &&
    gmailPlugin !== undefined &&
    gmailPlugin.enabled &&
    gmailPlugin.supports_import &&
    gmailPlugin.connection?.status === "reconnect_needed" &&
    gmailPlugin.capabilities.some((capability) => capability.id === "read" && capability.enabled);

  const canUpload = config !== null && ownerId !== null;
  const over = user?.is_admin ? null : overLimit(config);
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
          setAnnouncement(`${created.filename} uploaded. It can be searched once indexing finishes.`);
          // A poll sent while the upload's body was still arriving can answer after it, without the new document;
          // a newer request makes that answer stale, so it can't drop the document from the list.
          void reloadDocuments();
        }
        void reloadConfig();
      }),
    [upsertDocument, reloadDocuments, reloadConfig],
  );

  // The Gmail browser closes when Gmail stops being usable (often a search or import that got a 403/409), and doesn't
  // reopen by itself later. The chat page then opens Connections, which says why and offers Reconnect.
  useEffect(() => {
    if (gmailUsable || !importing) return;
    setImporting(false);
    requestConnections("gmail");
  }, [gmailUsable, importing]);

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
        {gmail ? (
          <button type="button" className="btn btn-ghost btn-sm docs-import-btn" onClick={() => setImporting(true)}>
            <MailIcon size={15} />
            Import from Gmail
          </button>
        ) : gmailReconnect ? (
          <button
            type="button"
            className="btn btn-ghost btn-sm docs-import-btn docs-import-attention"
            onClick={() => requestConnections()}
            title="Gmail needs to be reconnected before you can import from it"
          >
            <AlertIcon size={15} />
            Reconnect Gmail
          </button>
        ) : null}
        {onClose ? (
          <button
            type="button"
            className="btn btn-ghost btn-icon docs-close"
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
        {over ? (
          <div className="ui-notice ui-notice-warning docs-notice" role="status">
            <AlertIcon size={15} />
            <span className="ui-notice-text">{over}</span>
            <Link className="btn btn-ghost btn-xs" to="/plans">
              See plans
            </Link>
          </div>
        ) : null}
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
      {importing && gmail ? <GmailImportModal plugin={gmail} onClose={() => setImporting(false)} /> : null}
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
    <div className="ui-notice ui-notice-danger docs-notice" role="alert">
      <AlertCircleIcon size={15} />
      <span className="ui-notice-text">{message}</span>
      <RetryButton onRetry={onRetry} />
    </div>
  );
}

function LoadError({ message, onRetry }: { message: string; onRetry: () => Promise<void> }) {
  return (
    <div className="docs-state" role="alert">
      <span className="docs-state-icon docs-state-icon-danger" aria-hidden="true">
        <AlertCircleIcon size={24} />
      </span>
      <p className="docs-state-title">Documents unavailable</p>
      <p className="docs-state-text">{message}</p>
      <RetryButton onRetry={onRetry} />
    </div>
  );
}

/** Rows shaped like the real ones (box, tag, two lines) while the list loads. */
function ListSkeleton() {
  return (
    <div className="docs-skeleton" role="status" aria-busy="true">
      <span className="sr-only">Loading documents…</span>
      {[72, 58, 66].map((width, row) => (
        <div key={row} className="docs-skeleton-row" aria-hidden="true">
          <span className="ui-skeleton docs-skeleton-check" />
          <span className="ui-skeleton docs-skeleton-tag" />
          <span className="docs-skeleton-lines">
            <span className="ui-skeleton" style={{ width: `${width}%` }} />
            <span className="ui-skeleton ui-skeleton-sub" style={{ width: `${width - 26}%` }} />
          </span>
        </div>
      ))}
    </div>
  );
}
