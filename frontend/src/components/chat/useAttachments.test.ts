import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { DocumentItem } from "../../api/types";
import { controlUploads, makeFile, settle, type FakeUpload } from "../../test/documents";
import { makeDocument, makeDocumentConfig } from "../../test/fixtures";
import { publishDocuments, subscribeToReloadRequests } from "../documents/documentEvents";
import { uploadDocument } from "../documents/uploadDocument";
import { uploadManager } from "../documents/uploadManager";
import { useAttachments, type AttachmentView } from "./useAttachments";

vi.mock("../documents/uploadDocument", () => ({ uploadDocument: vi.fn() }));

const config = makeDocumentConfig();
let uploads: FakeUpload[];

function setup() {
  const hook = renderHook(() => useAttachments(7));
  const attach = (...names: string[]) => act(() => hook.result.current.add(names.map((name) => makeFile(name)), [], config));
  const states = () => hook.result.current.views.map((view: AttachmentView) => [view.name, view.state]);
  return { hook, attach, states };
}

async function finish(upload: FakeUpload, created: DocumentItem): Promise<void> {
  await act(async () => {
    upload.succeed(created);
    await settle();
  });
}

function publish(list: DocumentItem[] | null): void {
  act(() => publishDocuments(list));
}

beforeEach(() => {
  uploads = controlUploads(vi.mocked(uploadDocument));
});

afterEach(() => {
  vi.useRealTimers();
  uploadManager.clear();
  publishDocuments(null);
});

describe("useAttachments", () => {
  it("follows a file from uploading through indexing to ready", async () => {
    const { hook, attach, states } = setup();
    attach("brief.pdf", "notes.md", "plan.docx");
    expect(states()).toEqual([
      ["brief.pdf", { kind: "uploading", progress: 0 }],
      ["notes.md", { kind: "uploading", progress: 0 }],
      ["plan.docx", { kind: "queued" }],
    ]);
    expect(hook.result.current.waiting).toBe("Waiting for brief.pdf to finish uploading…");

    act(() => uploads[0].progress(60));
    expect(states()[0]).toEqual(["brief.pdf", { kind: "uploading", progress: 60 }]);

    const created = makeDocument({ id: 401, filename: "brief.pdf", status: "pending" });
    await finish(uploads[0], created);
    expect(states()[0]).toEqual(["brief.pdf", { kind: "indexing" }]);
    expect(hook.result.current.views[0].documentId).toBe(401);
    expect(hook.result.current.waiting).toBe("Waiting for brief.pdf to finish indexing…");

    publish([{ ...created, status: "ready" }]);
    expect(states()[0]).toEqual(["brief.pdf", { kind: "ready" }]);
    expect(hook.result.current.waiting).toBe("Waiting for notes.md to finish uploading…");
  });

  it("is not waiting once every file is ready", async () => {
    const { hook, attach } = setup();
    attach("brief.pdf");
    const created = makeDocument({ id: 402, filename: "brief.pdf", status: "ready" });
    await finish(uploads[0], created);
    expect(hook.result.current.waiting).toBeNull();
  });

  it("shows why indexing failed", async () => {
    const { attach, states } = setup();
    attach("scan.pdf");
    const created = makeDocument({ id: 403, filename: "scan.pdf", status: "processing" });
    await finish(uploads[0], created);
    publish([{ ...created, status: "failed", error: "No text could be extracted." }]);
    expect(states()).toEqual([["scan.pdf", { kind: "failed", reason: "No text could be extracted." }]]);
  });

  it("marks a document deleted from the panel's list", async () => {
    const { attach, states } = setup();
    attach("brief.pdf");
    const created = makeDocument({ id: 404, filename: "brief.pdf" });
    await finish(uploads[0], created);
    publish([created]);
    publish([]);
    expect(states()).toEqual([["brief.pdf", { kind: "failed", reason: "It was deleted from your documents." }]]);
  });

  it("shows a failed upload's error, and refused files at once", async () => {
    const { hook, attach, states } = setup();
    attach("setup.exe", "brief.pdf");
    await act(async () => {
      uploads[0].fail(new ApiError(400, "You've reached your document limit (100)."));
      await settle();
    });
    expect(states()).toEqual([
      ["setup.exe", { kind: "failed", reason: ".exe files aren't supported." }],
      ["brief.pdf", { kind: "failed", reason: "You've reached your document limit (100)." }],
    ]);
    expect(hook.result.current.waiting).toBeNull();
  });

  it("cancels an upload when its chip is removed", async () => {
    const { hook, attach } = setup();
    attach("brief.pdf");
    const [chip] = hook.result.current.views;
    await act(async () => {
      hook.result.current.remove(chip.id);
      await settle();
    });
    expect(uploads[0].signal.aborted).toBe(true);
    expect(hook.result.current.views).toEqual([]);
    expect(uploadManager.getSnapshot()).toEqual([]);
  });

  it("drops a chip whose upload was cancelled in the documents panel", async () => {
    const { hook, attach } = setup();
    attach("brief.pdf");
    await act(async () => {
      uploadManager.remove(hook.result.current.views[0].id);
      await settle();
    });
    expect(hook.result.current.views).toEqual([]);
  });

  it("hands over the ready documents on send and puts the chips back when the send is rejected", async () => {
    const { hook, attach, states } = setup();
    attach("brief.pdf", "notes.md");
    await finish(uploads[0], makeDocument({ id: 405, filename: "brief.pdf", status: "ready" }));
    await finish(uploads[1], makeDocument({ id: 406, filename: "notes.md", status: "processing" }));

    let taken: ReturnType<typeof hook.result.current.take> | undefined;
    act(() => {
      taken = hook.result.current.take();
    });
    expect(taken?.documentIds).toEqual([405]);
    expect(hook.result.current.views).toEqual([]);

    act(() => hook.result.current.restore(taken?.snapshot));
    expect(states()).toEqual([
      ["brief.pdf", { kind: "ready" }],
      ["notes.md", { kind: "indexing" }],
    ]);
  });

  it("asks the documents panel to catch up on a document its list doesn't have, every 10 s", async () => {
    vi.useFakeTimers();
    const reloads = vi.fn();
    const unsubscribe = subscribeToReloadRequests(reloads);
    const { attach } = setup();
    attach("brief.pdf");
    const created = makeDocument({ id: 407, filename: "brief.pdf", status: "pending" });
    await act(async () => {
      uploads[0].succeed(created);
      await vi.advanceTimersByTimeAsync(0);
    });

    await act(() => vi.advanceTimersByTimeAsync(9_900));
    expect(reloads).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(reloads).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(reloads).toHaveBeenCalledTimes(2);

    publish([created]);
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(reloads).toHaveBeenCalledTimes(2);
    unsubscribe();
  });
});
