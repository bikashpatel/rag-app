import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, setUnauthorizedHandler } from "../../api/client";
import { makeDocument } from "../../test/fixtures";
import { uploadDocument } from "./uploadDocument";

class FakeXhr extends EventTarget {
  static sent: FakeXhr[] = [];
  readonly upload = new EventTarget();
  readonly headers: Record<string, string> = {};
  method = "";
  url = "";
  body: FormData | null = null;
  status = 0;
  responseText = "";

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value;
  }

  send(body: FormData): void {
    this.body = body;
    FakeXhr.sent.push(this);
  }

  abort(): void {
    this.dispatchEvent(new Event("abort"));
  }

  progress(loaded: number, total: number, lengthComputable = true): void {
    this.upload.dispatchEvent(new ProgressEvent("progress", { loaded, total, lengthComputable }));
  }

  respond(status: number, body: unknown): void {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.dispatchEvent(new Event("load"));
  }
}

const file = new File(["%PDF-1.7"], "report.pdf", { type: "application/pdf" });

function start(signal = new AbortController().signal) {
  const onProgress = vi.fn<(percent: number) => void>();
  const result = uploadDocument(file, "shared", { signal, onProgress });
  return { result, onProgress, xhr: FakeXhr.sent.at(-1) as FakeXhr };
}

beforeEach(() => {
  FakeXhr.sent = [];
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
});

afterEach(() => setUnauthorizedHandler(null));

describe("uploadDocument", () => {
  it("posts the file and scope as multipart form data and resolves with the created document", async () => {
    const created = makeDocument({ id: 300, filename: "report.pdf", status: "pending", scope: "shared" });
    const { result, xhr } = start();
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/documents");
    expect(xhr.headers).toEqual({ Accept: "application/json" });
    expect((xhr.body?.get("file") as File).name).toBe("report.pdf");
    expect(xhr.body?.get("scope")).toBe("shared");
    xhr.respond(201, created);
    await expect(result).resolves.toEqual(created);
  });

  it("reports each whole percentage once, ignoring uncomputable progress", async () => {
    const { result, xhr, onProgress } = start();
    xhr.progress(10, 0);
    xhr.progress(5, 100, false);
    xhr.progress(333, 1000);
    xhr.progress(339, 1000);
    xhr.progress(1000, 1000);
    xhr.respond(201, makeDocument());
    await result;
    expect(onProgress.mock.calls).toEqual([[33], [100]]);
  });

  it("rejects with the server's detail on an error status", async () => {
    const { result, xhr } = start();
    xhr.respond(400, { detail: "You've reached your document limit (100)." });
    await expect(result).rejects.toEqual(new ApiError(400, "You've reached your document limit (100)."));
  });

  it("explains a 413 without a JSON body", async () => {
    const { result, xhr } = start();
    xhr.respond(413, "<html>Request Entity Too Large</html>");
    await expect(result).rejects.toMatchObject({ status: 413, message: "The file is too large." });
  });

  it("notifies the session check on a 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    const { result, xhr } = start();
    xhr.respond(401, { detail: "Not authenticated" });
    await expect(result).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("rejects a success without a document", async () => {
    const { result, xhr } = start();
    xhr.respond(200, { ok: true });
    await expect(result).rejects.toMatchObject({ message: "The server sent an unexpected response." });
  });

  it("rejects a network failure with a readable message", async () => {
    const { result, xhr } = start();
    xhr.dispatchEvent(new Event("error"));
    await expect(result).rejects.toEqual(
      new ApiError(0, "The upload was interrupted. Check your connection and try again."),
    );
  });

  it("aborts the request when the signal aborts", async () => {
    const controller = new AbortController();
    const { result } = start(controller.signal);
    controller.abort();
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
  });

  it("never sends when the signal already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    const { result } = start(controller.signal);
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(FakeXhr.sent).toHaveLength(0);
  });
});
