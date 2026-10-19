import { useEffect, useState } from "react";
import { documentDownloadUrl, documentImageUrl, getImageDetails } from "../../api/client";
import type { GeneratedImage, GeneratedImageDetails } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import Modal from "../ui/Modal";
import { fileTypeTag } from "./fileTypes";
import { AlertCircleIcon, DownloadIcon } from "../ui/icons";

export type ImageViewerProps = { documentId: number; filename: string; image: GeneratedImage; onClose: () => void };

type Details = { status: "loading" } | { status: "ready"; details: GeneratedImageDetails } | { status: "failed" };

/**
 * A generated image at full size in a dialog (Escape closes it, focus stays inside and returns to the opener): the
 * image within the dialog's width and min(70vh, 900px) of height, its description as the caption, its size, format
 * and quality, the prompt (loaded when the viewer opens; only the owner gets it), Download and Close.
 */
export default function ImageViewer({ documentId, filename, image, onClose }: ImageViewerProps) {
  const [load, setLoad] = useState<"loading" | "loaded" | "failed">("loading");
  const [details, setDetails] = useState<Details>({ status: "loading" });
  const { width, height } = image;

  useEffect(() => {
    const controller = new AbortController();
    setDetails({ status: "loading" });
    getImageDetails(documentId, { signal: controller.signal })
      .then((loaded) => setDetails({ status: "ready", details: loaded }))
      .catch(() => {
        if (!controller.signal.aborted) setDetails({ status: "failed" });
      });
    return () => controller.abort();
  }, [documentId]);

  // The box has the image's final size before it loads (never enlarged), so the dialog doesn't jump.
  const boxStyle = {
    aspectRatio: `${width} / ${height}`,
    width: `min(100%, ${width}px, calc(min(70vh, 900px) * ${width / height}))`,
  };

  return (
    <Modal
      open
      title={filename}
      onClose={onClose}
      width={1100}
      className="docs-viewer-dialog"
      footer={
        <>
          <a className="btn btn-primary docs-viewer-download" href={documentDownloadUrl(documentId)} download>
            <DownloadIcon size={16} />
            Download
          </a>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      <figure className="docs-viewer">
        <div className="docs-viewer-stage">
          {load === "failed" ? (
            <p className="docs-viewer-error docs-viewer-broken" role="alert" style={boxStyle}>
              <AlertCircleIcon size={16} />
              Couldn't load the image.
            </p>
          ) : (
            <div className="docs-viewer-box" style={boxStyle}>
              <img
                className="docs-viewer-image"
                src={documentImageUrl(documentId, "full")}
                alt={image.alt_text}
                width={width}
                height={height}
                onLoad={() => setLoad("loaded")}
                onError={() => setLoad("failed")}
              />
              {load === "loading" ? (
                <span className="spinner docs-viewer-spinner" role="status" aria-label="Loading the image" />
              ) : null}
            </div>
          )}
        </div>
        <figcaption className="docs-viewer-caption">
          <span className="docs-viewer-alt">{image.alt_text}</span>
          <span className="docs-viewer-facts">
            {width} × {height} · {fileTypeTag(filename).label} · quality {image.quality}
          </span>
        </figcaption>
      </figure>
      <details className="docs-viewer-prompt">
        <summary>Prompt</summary>
        <div className="docs-viewer-prompt-body">
          {details.status === "loading" ? (
            <p className="docs-viewer-muted">
              <span className="spinner" aria-hidden="true" />
              Loading the prompt…
            </p>
          ) : details.status === "failed" ? (
            <p className="docs-viewer-error">Couldn't load the prompt.</p>
          ) : (
            <>
              <p className="docs-viewer-text">{details.details.prompt}</p>
              {details.details.revised_prompt ? (
                <>
                  <h3 className="docs-viewer-subhead">Revised prompt</h3>
                  <p className="docs-viewer-text">{details.details.revised_prompt}</p>
                </>
              ) : null}
              <p className="docs-viewer-muted">
                Made with {details.details.model_name} ·{" "}
                <time dateTime={details.details.created_at}>{formatDateTime(details.details.created_at)}</time>
              </p>
            </>
          )}
        </div>
      </details>
    </Modal>
  );
}
