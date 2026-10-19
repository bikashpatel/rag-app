import FileTag from "../documents/FileTag";
import { AlertCircleIcon, CheckIcon, CloseIcon } from "./icons";
import type { AttachmentView } from "./useAttachments";

type AttachmentChipsProps = {
  views: AttachmentView[];
  onRemove: (id: number) => void;
};

function stateText(view: AttachmentView): string {
  switch (view.state.kind) {
    case "queued":
      return "Queued";
    case "uploading":
      return `Uploading ${Math.round(view.state.progress)}%`;
    case "indexing":
      return "Indexing…";
    case "ready":
      return "Ready";
    case "failed":
      return `Failed: ${view.state.reason}`;
  }
}

function removeLabel(view: AttachmentView): string {
  if (view.state.kind === "queued" || view.state.kind === "uploading") return `Cancel uploading ${view.name}`;
  if (view.documentId === null) return `Remove ${view.name}`;
  return `Remove ${view.name} from this message; it stays in your documents`;
}

/** Files attached to the next message, above the composer's input: type, name, progress or indexing state, remove. */
export default function AttachmentChips({ views, onRemove }: AttachmentChipsProps) {
  if (views.length === 0) return null;
  return (
    <ul className="chat-attachments" aria-label="Attached files">
      {views.map((view) => {
        const { state } = view;
        const failed = state.kind === "failed";
        return (
          <li key={view.id} className={failed ? "chat-attachment chat-attachment-failed" : "chat-attachment"}>
            <span className="chat-attachment-tag">
              <FileTag filename={view.name} />
            </span>
            <span className="chat-attachment-name" title={view.name}>
              {view.name}
            </span>
            <span className={`chat-attachment-state chat-attachment-state-${state.kind}`} title={failed ? stateText(view) : undefined}>
              {state.kind === "indexing" ? <span className="spinner" aria-hidden="true" /> : null}
              {state.kind === "ready" ? <CheckIcon size={14} /> : null}
              {failed ? <AlertCircleIcon size={14} /> : null}
              <span className="chat-attachment-state-text">{stateText(view)}</span>
            </span>
            <button
              type="button"
              className="btn btn-ghost btn-icon btn-xs chat-attachment-remove"
              onClick={() => onRemove(view.id)}
              aria-label={removeLabel(view)}
              title={removeLabel(view)}
            >
              <CloseIcon size={14} />
            </button>
            {state.kind === "uploading" ? (
              <span className="chat-attachment-progress" style={{ width: `${state.progress}%` }} aria-hidden="true" />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
