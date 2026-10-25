import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { announceUserChanged } from "../../lib/appEvents";
import { controlUploads, makeFile, settle, type FakeUpload } from "../../test/documents";
import { makeDocument } from "../../test/fixtures";
import type { UploadRules } from "./fileTypes";
import { uploadDocument } from "./uploadDocument";
import { uploadManager, useUploads, type UploadItem } from "./uploadManager";

vi.mock("./uploadDocument", () => ({ uploadDocument: vi.fn() }));

const rules: UploadRules = { extensions: [".pdf", ".md"], maxUploadMb: 1 };
const OWNER = 7;
let uploads: FakeUpload[];

function enqueue(names: string[], options: { ownerId?: number; folders?: string[] } = {}): number[] {
  return uploadManager.enqueue({
    ownerId: options.ownerId ?? OWNER,
    scope: "private",
    rules,
    files: names.map((name) => makeFile(name)),
    folders: options.folders ?? [],
  });
}

function items(): Pick<UploadItem, "name" | "status" | "progress" | "error">[] {
  return uploadManager.getSnapshot().map(({ name, status, progress, error }) => ({ name, status, progress, error }));
}

beforeEach(() => {
  uploads = controlUploads(vi.mocked(uploadDocument));
});

afterEach(() => uploadManager.clear());

describe("uploadManager", () => {
  it("runs at most two uploads at a time and starts the next one when one ends", async () => {
    const settled = vi.fn();
    const unsubscribe = uploadManager.onSettled(settled);
    const [first, , third] = enqueue(["a.pdf", "b.pdf", "c.md"]);
    expect(items().map((item) => item.status)).toEqual(["uploading", "uploading", "queued"]);
    expect(uploads.map((upload) => upload.file.name)).toEqual(["a.pdf", "b.pdf"]);

    const created = makeDocument({ id: 301, filename: "a.pdf", status: "pending" });
    uploads[0].succeed(created);
    await settle();
    unsubscribe();

    expect(settled).toHaveBeenCalledWith(created, first);
    expect(items().map((item) => item.name)).toEqual(["b.pdf", "c.md"]);
    expect(uploads.map((upload) => upload.file.name)).toEqual(["a.pdf", "b.pdf", "c.md"]);
    expect(uploadManager.getSnapshot().find((item) => item.id === third)?.status).toBe("uploading");
  });

  it("tracks the progress of a running upload", () => {
    enqueue(["a.pdf"]);
    uploads[0].progress(42);
    expect(items()).toEqual([{ name: "a.pdf", status: "uploading", progress: 42, error: null }]);
  });

  it("lists refused folders and files as failed without starting them, folders first", () => {
    const ids = enqueue(["setup.exe", "notes.md"], { folders: ["Invoices"] });
    expect(ids).toHaveLength(3);
    expect(items()).toEqual([
      {
        name: "Invoices",
        status: "failed",
        progress: 0,
        error: "Folders can't be uploaded. Open the folder and select the files instead.",
      },
      { name: "setup.exe", status: "failed", progress: 0, error: ".exe files aren't supported." },
      { name: "notes.md", status: "uploading", progress: 0, error: null },
    ]);
    expect(uploads.map((upload) => upload.file.name)).toEqual(["notes.md"]);
  });

  it("returns no ids and changes nothing for an empty request", () => {
    const listener = vi.fn();
    const unsubscribe = uploadManager.subscribe(listener);
    expect(enqueue([])).toEqual([]);
    unsubscribe();
    expect(listener).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError(400, "You've reached your document limit (100)."), "You've reached your document limit (100)."],
    [new ApiError(401, "Not authenticated"), "Your session changed during the upload. Upload the file again."],
    [new TypeError("boom"), "Upload failed. Please try again."],
  ])("keeps a failed upload with its message (%s)", async (err, message) => {
    const settled = vi.fn();
    const unsubscribe = uploadManager.onSettled(settled);
    const [id] = enqueue(["a.pdf"]);
    uploads[0].fail(err);
    await settle();
    unsubscribe();
    expect(items()).toEqual([{ name: "a.pdf", status: "failed", progress: 0, error: message }]);
    expect(settled).toHaveBeenCalledWith(null, id);
  });

  it("cancels a running upload and forgets it", async () => {
    const settled = vi.fn();
    const unsubscribe = uploadManager.onSettled(settled);
    const [id] = enqueue(["a.pdf"]);
    uploadManager.remove(id);
    expect(uploads[0].signal.aborted).toBe(true);
    await settle();
    unsubscribe();
    expect(items()).toEqual([]);
    expect(settled).toHaveBeenCalledWith(null, id);
  });

  it("drops a queued upload before it starts, and dismisses a failed one", async () => {
    const [, , queued] = enqueue(["a.pdf", "b.pdf", "c.pdf"]);
    uploadManager.remove(queued);
    uploads[0].fail(new ApiError(500, "The server ran into a problem. Please try again."));
    uploads[1].succeed(makeDocument({ id: 302, filename: "b.pdf" }));
    await settle();
    expect(uploads).toHaveLength(2);
    expect(items().map((item) => item.name)).toEqual(["a.pdf"]);

    uploadManager.remove(uploadManager.getSnapshot()[0].id);
    expect(items()).toEqual([]);
  });

  it("forgets and cancels another account's uploads", async () => {
    enqueue(["mine.pdf"]);
    enqueue(["theirs.pdf", "theirs-too.pdf"], { ownerId: 8 });
    uploadManager.retainOwner(OWNER);
    expect(items().map((item) => item.name)).toEqual(["mine.pdf"]);
    expect(uploads.map((upload) => upload.signal.aborted)).toEqual([false, true]);
    await settle();
    expect(uploads.map((upload) => upload.file.name)).toEqual(["mine.pdf", "theirs.pdf"]);
  });

  it("follows the signed-in user: another user keeps only theirs, signing out clears everything", () => {
    enqueue(["mine.pdf"]);
    enqueue(["theirs.pdf"], { ownerId: 8 });
    announceUserChanged(8);
    expect(items().map((item) => item.name)).toEqual(["theirs.pdf"]);
    announceUserChanged(null);
    expect(items()).toEqual([]);
    expect(uploads.every((upload) => upload.signal.aborted)).toBe(true);
  });

  it("gives each user only their own uploads through useUploads", () => {
    enqueue(["mine.pdf"]);
    const { result } = renderHook(() => useUploads(OWNER));
    expect(result.current.map((item) => item.name)).toEqual(["mine.pdf"]);
    act(() => {
      enqueue(["theirs.pdf"], { ownerId: 8 });
      enqueue(["also-mine.md"]);
    });
    expect(result.current.map((item) => item.name)).toEqual(["mine.pdf", "also-mine.md"]);
  });
});
