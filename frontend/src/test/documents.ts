import type { MockedFunction } from "vitest";
import type { DocumentItem, DocumentScope } from "../api/types";
import type { uploadDocument } from "../components/documents/uploadDocument";

/** One upload the queue started, settled by the test. */
export type FakeUpload = {
  file: File;
  scope: DocumentScope;
  signal: AbortSignal;
  progress: (percent: number) => void;
  succeed: (created: DocumentItem) => void;
  fail: (err: unknown) => void;
};

/** Makes a vi.mock'ed uploadDocument hand each upload to the test; an abort rejects it like the real one. */
export function controlUploads(mock: MockedFunction<typeof uploadDocument>): FakeUpload[] {
  const uploads: FakeUpload[] = [];
  mock.mockReset();
  mock.mockImplementation(
    (file, scope, { signal, onProgress }) =>
      new Promise<DocumentItem>((resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Upload cancelled", "AbortError")), {
          once: true,
        });
        uploads.push({ file, scope, signal, progress: onProgress, succeed: resolve, fail: reject });
      }),
  );
  return uploads;
}

/** Lets settled promises' handlers run. */
export function settle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

export function makeFile(name: string, content = "Quarterly figures"): File {
  return new File([content], name);
}
