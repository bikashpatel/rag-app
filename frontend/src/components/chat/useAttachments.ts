import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DocumentConfig, DocumentItem } from "../../api/types";
import { requestDocumentsReload, useDocumentList } from "../documents/documentEvents";
import { uploadRules } from "../documents/fileTypes";
import { uploadManager, useUploads } from "../documents/uploadManager";

// Files attached from the composer's "+" menu (functional brief 9.3, U10): they go to My documents through the shared
// upload queue, and the message waits until each one is indexed. Chips follow the queue while uploading, then the
// documents panel's published list (it polls while documents are indexed).

/** A chip whose document the panel's list doesn't know yet asks it to reload, every this often. */
const CATCH_UP_MS = 10_000;

type Attachment = {
  /** The upload queue item's id. */
  itemId: number;
  name: string;
  /** The created document (from the queue's onSettled), once the upload finished. */
  created: DocumentItem | null;
  /** When the upload ended (Date.now()); null while queued or uploading. */
  settledAt: number | null;
};

export type AttachmentState =
  | { kind: "queued" }
  | { kind: "uploading"; progress: number }
  | { kind: "indexing" }
  | { kind: "ready" }
  | { kind: "failed"; reason: string };

export type AttachmentView = {
  id: number;
  name: string;
  state: AttachmentState;
  documentId: number | null;
};

/** The composer's attachments; `take` and `restore` move them with a send that may be rejected. */
export type Attachments = {
  views: AttachmentView[];
  /** Why Send waits ("Waiting for report.pdf to finish indexing…"), or null. */
  waiting: string | null;
  add: (files: File[], folders: string[], config: DocumentConfig) => void;
  /** Removes a chip: cancels an upload in progress, dismisses a failed one; a created document stays. */
  remove: (id: number) => void;
  /** The ready documents' ids and the chips as they are, clearing them (a send was accepted). */
  take: () => { documentIds: number[]; snapshot: unknown };
  /** Puts chips taken by `take` back (the send was rejected). */
  restore: (snapshot: unknown) => void;
};

function isBusy(state: AttachmentState): boolean {
  return state.kind === "queued" || state.kind === "uploading" || state.kind === "indexing";
}

export function useAttachments(ownerId: number | null): Attachments {
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const uploads = useUploads(ownerId);
  const documents = useDocumentList();
  /** Document ids the panel's list has had: one missing from a later list was deleted. */
  const seen = useRef(new Set<number>());

  const byId = useMemo(() => new Map((documents ?? []).map((doc) => [doc.id, doc])), [documents]);
  useEffect(() => {
    byId.forEach((_, id) => seen.current.add(id));
  }, [byId]);

  // The queue reports each upload it started once it ends: the created document, or null (failed or cancelled).
  useEffect(
    () =>
      uploadManager.onSettled((created, itemId) => {
        const queued = uploadManager.getSnapshot().find((item) => item.id === itemId);
        setAttachments((list) => {
          if (!list.some((item) => item.itemId === itemId)) return list;
          // Cancelled: the queue forgot it. Failed: it stays in the queue with its error, and the chip shows it.
          if (!created && !queued) return list.filter((item) => item.itemId !== itemId);
          return list.map((item) => (item.itemId === itemId ? { ...item, created, settledAt: Date.now() } : item));
        });
      }),
    [],
  );

  const views = useMemo(() => {
    const result: AttachmentView[] = [];
    for (const attachment of attachments) {
      const upload = uploads.find((item) => item.id === attachment.itemId);
      let state: AttachmentState | null = null;
      let documentId: number | null = null;
      if (attachment.settledAt === null) {
        // Not started or still running; refused files (type, size, folders) are failed queue items from the start. A
        // refused item dismissed in the documents panel's queue has nothing left to show.
        if (upload?.status === "queued") state = { kind: "queued" };
        else if (upload?.status === "uploading") state = { kind: "uploading", progress: upload.progress };
        else if (upload?.status === "failed") state = { kind: "failed", reason: upload.error ?? "Upload failed." };
      } else if (attachment.created === null) {
        if (upload?.status === "failed") state = { kind: "failed", reason: upload.error ?? "Upload failed." };
      } else {
        documentId = attachment.created.id;
        const doc = byId.get(documentId);
        const status = doc?.status ?? (seen.current.has(documentId) ? null : attachment.created.status);
        if (status === null) state = { kind: "failed", reason: "It was deleted from your documents." };
        else if (status === "ready") state = { kind: "ready" };
        else if (status === "failed") state = { kind: "failed", reason: doc?.error ?? "It couldn't be indexed." };
        else state = { kind: "indexing" };
      }
      if (state) result.push({ id: attachment.itemId, name: attachment.name, state, documentId });
    }
    return result;
  }, [attachments, uploads, byId]);

  // A created document the panel's list hasn't had for 10 s: ask it to reload, and again every 10 s while it stays so.
  const unknownSince = useMemo(() => {
    const times = attachments
      .filter((item) => item.created !== null && item.settledAt !== null && !byId.has(item.created.id))
      .filter((item) => !seen.current.has((item.created as DocumentItem).id))
      .map((item) => item.settledAt as number);
    return times.length > 0 ? Math.min(...times) : null;
  }, [attachments, byId]);
  useEffect(() => {
    if (unknownSince === null) return;
    const first = Math.max(0, unknownSince + CATCH_UP_MS - Date.now());
    let interval: number | undefined;
    const timeout = window.setTimeout(() => {
      requestDocumentsReload();
      interval = window.setInterval(requestDocumentsReload, CATCH_UP_MS);
    }, first);
    return () => {
      window.clearTimeout(timeout);
      window.clearInterval(interval);
    };
  }, [unknownSince]);

  const add = useCallback(
    (files: File[], folders: string[], config: DocumentConfig) => {
      if (ownerId === null || (files.length === 0 && folders.length === 0)) return;
      const ids = uploadManager.enqueue({ ownerId, scope: "private", rules: uploadRules(config), files, folders });
      const names = [...folders, ...files.map((file) => file.name)];
      const added = ids.map((itemId, i) => ({ itemId, name: names[i] ?? "File", created: null, settledAt: null }));
      setAttachments((list) => [...list, ...added]);
    },
    [ownerId],
  );

  const remove = useCallback((id: number) => {
    // Cancels a queued or running upload, or dismisses a failed one from the shared queue; a document stays.
    if (uploadManager.getSnapshot().some((item) => item.id === id)) uploadManager.remove(id);
    setAttachments((list) => list.filter((item) => item.itemId !== id));
  }, []);

  const viewsRef = useRef(views);
  viewsRef.current = views;
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  const take = useCallback(() => {
    const documentIds = viewsRef.current
      .filter((view) => view.state.kind === "ready" && view.documentId !== null)
      .map((view) => view.documentId as number);
    const snapshot = attachmentsRef.current;
    setAttachments([]);
    return { documentIds, snapshot };
  }, []);

  const restore = useCallback((snapshot: unknown) => {
    const taken = snapshot as Attachment[];
    if (taken.length === 0) return;
    setAttachments((list) => [...taken.filter((item) => !list.some((other) => other.itemId === item.itemId)), ...list]);
  }, []);

  const busy = views.find((view) => isBusy(view.state));
  const waiting = busy
    ? busy.state.kind === "indexing"
      ? `Waiting for ${busy.name} to finish indexing…`
      : `Waiting for ${busy.name} to finish uploading…`
    : null;

  return { views, waiting, add, remove, take, restore };
}
