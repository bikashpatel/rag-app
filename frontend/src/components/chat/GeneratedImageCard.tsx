import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { documentImageUrl } from "../../api/client";
import type { DocumentItem, GeneratedImage } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { useDocumentSnapshot } from "../documents/documentEvents";
import FileTag from "../documents/FileTag";
import ImageViewer from "../documents/ImageViewer";
import { AlertCircleIcon, ImageIcon } from "../ui/icons";
import { DownloadButton, StatusText } from "./GeneratedFileCard";
import type { FileCard } from "./useChatStream";

/** Longest edge of an image in the answer (px); narrow screens use the message's width instead. */
const MAX_EDGE = 480;

type Shown = { id: number; filename: string; image: GeneratedImage };

type CardView = {
  /** The group's accessible name. */
  label: string;
  tone: "default" | "muted" | "failed" | "busy";
  lead: ReactNode;
  headline: string;
  /** Long headlines wrap instead of being cut off (sentences rather than names). */
  wrap: boolean;
  meta: ReactNode;
  error: string | null;
  /** Shown outside the live region, so the seconds counting up aren't read out. */
  progress: ReactNode;
  /** The image's size while it is generated (a placeholder) or once it is shown. */
  frame: { width: number; height: number } | null;
  /** The created image, still in the user's documents. */
  shown: Shown | null;
  download: { id: number; filename: string } | null;
};

/** The card and its frame keep the image's aspect ratio (within limits), so nothing moves when the preview loads. */
function frameStyles(width: number, height: number): { card: CSSProperties; frame: CSSProperties } {
  const ratio = Math.min(Math.max(width / height, 0.4), 2.5);
  return { card: { maxWidth: Math.round(MAX_EDGE * Math.min(1, ratio)) }, frame: { aspectRatio: String(ratio) } };
}

function cardView(card: FileCard, doc: DocumentItem | null | undefined): CardView {
  const base = { wrap: false, meta: null, error: null, progress: null, frame: null, shown: null, download: null };
  switch (card.kind) {
    case "writing":
      return {
        ...base,
        label: "Image being generated",
        tone: "busy",
        lead: <span className="spinner chat-file-spinner" aria-hidden="true" />,
        headline: card.title ? `Creating ${card.title}…` : "Creating an image…",
      };
    case "generating":
      return {
        ...base,
        label: "Image being generated",
        tone: "busy",
        lead: <FileTag filename="image.png" />,
        headline: `Generating ${card.title}…`,
        progress: <Elapsed since={card.startedAt} />,
        frame: { width: card.width, height: card.height },
      };
    case "created": {
      const { file } = card;
      // As on file cards: the panel's list when it has the document, else what the server sent; only a document the
      // list had and then lost (doc null) counts as deleted.
      const status = file.document_id === null || doc === null ? null : (doc?.status ?? file.status);
      const id = status === null ? null : file.document_id;
      const image = file.image ?? doc?.image ?? null;
      const shown = id !== null && image ? { id, filename: file.filename, image } : null;
      return {
        ...base,
        label: file.filename,
        tone: id === null ? "muted" : "default",
        lead: <FileTag filename={file.filename} />,
        headline: file.filename,
        meta: (
          <span className="chat-file-meta">
            {image ? (
              <span>
                {image.width} × {image.height}
              </span>
            ) : null}
            <span>{formatBytes(file.size_bytes)}</span>
            <span>
              <StatusText status={status} alreadyExisted={false} error={doc?.error ?? null} />
            </span>
          </span>
        ),
        frame: shown ? { width: shown.image.width, height: shown.image.height } : null,
        shown,
        download: id === null ? null : { id, filename: file.filename },
      };
    }
    case "failed":
      return {
        ...base,
        label: card.title ?? "Image",
        tone: "failed",
        lead: (
          <span className="chat-file-icon">
            <AlertCircleIcon size={20} />
          </span>
        ),
        headline: `Couldn't generate ${card.title ?? "the image"}`,
        wrap: true,
        error: card.error,
      };
    case "stopped":
      return {
        ...base,
        label: card.title ?? "Image",
        tone: "muted",
        lead: (
          <span className="chat-file-icon">
            <ImageIcon size={20} />
          </span>
        ),
        headline: card.wasGenerating
          ? `${card.title ?? "Image"}: stopped. An image that was already being generated is still finished and saved to your documents.`
          : `${card.title ?? "Image"}: stopped.`,
        wrap: true,
      };
  }
}

/**
 * An image the assistant generated (or tried to) in an answer: a placeholder of the final shape while it is made, then
 * the preview, which opens the full-size viewer, with name, type, size, indexing status and Download. The text block
 * is one live region whose DOM node survives writing → generating → created, so each change is announced.
 */
export default function GeneratedImageCard({ card }: { card: FileCard }) {
  const doc = useDocumentSnapshot(card.kind === "created" ? card.file.document_id : null);
  const [viewing, setViewing] = useState(false);
  const view = cardView(card, doc);
  const styles = view.frame ? frameStyles(view.frame.width, view.frame.height) : null;
  const shown = view.shown;

  return (
    <div
      className={`chat-image chat-image-${view.tone}${styles ? " chat-image-framed" : ""}`}
      role="group"
      aria-label={view.label}
      style={styles?.card}
    >
      {styles ? (
        shown ? (
          <Preview shown={shown} style={styles.frame} onOpen={() => setViewing(true)} />
        ) : (
          <div className="chat-image-frame chat-image-placeholder" style={styles.frame}>
            {card.kind === "generating" ? (
              <span className="chat-image-wait">
                <span className="spinner" aria-hidden="true" />
                <span>
                  Creating the image… this takes up to a minute
                  {view.progress ? <span className="chat-image-elapsed"> · {view.progress}</span> : null}
                </span>
              </span>
            ) : (
              <ImageIcon size={28} />
            )}
          </div>
        )
      ) : null}
      <div className="chat-image-bar">
        <span className="chat-image-lead">{view.lead}</span>
        <div className="chat-image-body">
          <div className="chat-image-text" aria-live="polite">
            <span
              className={view.wrap ? "chat-file-headline chat-file-headline-wrap" : "chat-file-headline"}
              title={view.wrap ? undefined : view.headline}
            >
              {view.headline}
            </span>
            {view.meta}
            {view.error ? <span className="chat-file-error">{view.error}</span> : null}
          </div>
          {view.progress && !styles ? <div className="chat-file-progress">{view.progress}</div> : null}
        </div>
        {view.download ? <DownloadButton {...view.download} /> : null}
      </div>
      {viewing && shown ? (
        <ImageViewer
          documentId={shown.id}
          filename={shown.filename}
          image={shown.image}
          onClose={() => setViewing(false)}
        />
      ) : null}
    </div>
  );
}

/** The preview rendition as a button that opens the viewer; a failed load offers to try again. */
function Preview({ shown, style, onOpen }: { shown: Shown; style: CSSProperties; onOpen: () => void }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const { id, filename, image } = shown;

  if (failed) {
    return (
      <div className="chat-image-frame chat-image-broken" style={style}>
        <AlertCircleIcon size={18} />
        <span>Couldn't load the image</span>
        <button
          type="button"
          className="chat-image-retry"
          onClick={() => {
            setFailed(false);
            setAttempt((n) => n + 1);
          }}
        >
          Retry
        </button>
      </div>
    );
  }
  return (
    <button
      type="button"
      className="chat-image-frame chat-image-open"
      style={style}
      onClick={onOpen}
      aria-label={`Open ${filename} full size`}
    >
      <img
        key={attempt}
        src={documentImageUrl(id, "preview")}
        alt={image.alt_text}
        width={image.width}
        height={image.height}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    </button>
  );
}

/** The seconds since the image model started ("12 s"), counting up every second. */
function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  return <span className="chat-image-seconds">{seconds} s</span>;
}
