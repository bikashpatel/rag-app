import { ApiError, errorMessageFromBody, notifyUnauthorized } from "../../api/client";
import type { DocumentItem, DocumentScope } from "../../api/types";

type UploadOptions = {
  signal: AbortSignal;
  /** Called with the whole-number percentage of the request body sent, each time it changes. */
  onProgress: (percent: number) => void;
};

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function isDocumentItem(value: unknown): value is DocumentItem {
  return typeof value === "object" && value !== null && typeof (value as { id?: unknown }).id === "number";
}

function abortError(): DOMException {
  return new DOMException("Upload cancelled", "AbortError");
}

/**
 * POST /api/documents as multipart form data. Uses XMLHttpRequest because fetch cannot report upload progress.
 * Resolves with the created document; rejects with ApiError, or with an AbortError DOMException when `signal` aborts.
 */
export function uploadDocument(
  file: File,
  scope: DocumentScope,
  { signal, onProgress }: UploadOptions,
): Promise<DocumentItem> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    signal.addEventListener("abort", abort, { once: true });
    const cleanup = () => signal.removeEventListener("abort", abort);

    let lastPercent = -1;
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable || event.total === 0) return;
      const percent = Math.min(100, Math.floor((event.loaded / event.total) * 100));
      if (percent === lastPercent) return;
      lastPercent = percent;
      onProgress(percent);
    });

    xhr.addEventListener("load", () => {
      cleanup();
      if (xhr.status === 401) notifyUnauthorized();
      const body = parseJson(xhr.responseText);
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new ApiError(xhr.status, errorMessageFromBody(xhr.status, body)));
      } else if (isDocumentItem(body)) {
        resolve(body);
      } else {
        reject(new ApiError(xhr.status, "The server sent an unexpected response."));
      }
    });
    xhr.addEventListener("error", () => {
      cleanup();
      reject(new ApiError(0, "The upload was interrupted. Check your connection and try again."));
    });
    xhr.addEventListener("abort", () => {
      cleanup();
      reject(abortError());
    });

    const form = new FormData();
    form.append("file", file, file.name);
    form.append("scope", scope);
    xhr.open("POST", "/api/documents");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.send(form);
  });
}
