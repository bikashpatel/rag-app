import { formatBytes } from "../../lib/format";
import FileTag from "./FileTag";
import { CloseIcon } from "./icons";
import type { UploadItem } from "./uploadManager";

type UploadQueueProps = {
  items: UploadItem[];
  /** Cancels a queued/running upload or dismisses a failed one. */
  onRemove: (id: number) => void;
};

export default function UploadQueue({ items, onRemove }: UploadQueueProps) {
  if (items.length === 0) return null;
  return (
    <ul className="docs-uploads" aria-label="Uploads">
      {items.map((item) => (
        <UploadQueueItem key={item.id} item={item} onRemove={onRemove} />
      ))}
    </ul>
  );
}

function progressLabel(item: UploadItem): string {
  if (item.status === "queued") return "Waiting…";
  // The whole body has been sent; the server is still storing and checking the file.
  return item.progress >= 100 ? "Finishing…" : `Uploading ${item.progress}%`;
}

function UploadQueueItem({ item, onRemove }: { item: UploadItem; onRemove: (id: number) => void }) {
  const failed = item.status === "failed";
  return (
    <li className={failed ? "docs-upload-item docs-upload-item-failed" : "docs-upload-item"}>
      <FileTag filename={item.name} />
      <div className="docs-upload-main">
        <div className="docs-upload-name" title={item.name}>
          {item.name}
        </div>
        {failed ? (
          <p className="docs-upload-error" role="alert">
            {item.error}
          </p>
        ) : (
          <>
            <div
              className="docs-progress"
              role="progressbar"
              aria-label={`Upload progress of ${item.name}`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={item.progress}
            >
              <div className="docs-progress-fill" style={{ width: `${item.progress}%` }} />
            </div>
            <div className="docs-upload-status">
              <span>
                {progressLabel(item)}
                {item.scope === "shared" ? " · Shared" : null}
              </span>
              <span>{formatBytes(item.size)}</span>
            </div>
          </>
        )}
      </div>
      <button
        type="button"
        className="docs-icon-btn"
        onClick={() => onRemove(item.id)}
        aria-label={failed ? `Dismiss ${item.name}` : `Cancel upload of ${item.name}`}
        title={failed ? "Dismiss" : "Cancel upload"}
      >
        <CloseIcon size={14} />
      </button>
    </li>
  );
}
