import { useEffect, useMemo, useSyncExternalStore } from "react";
import { ApiError } from "../../api/client";
import type { DocumentItem, DocumentScope } from "../../api/types";
import { validateUpload, type UploadRules } from "./fileTypes";
import { uploadDocument } from "./uploadDocument";

const MAX_PARALLEL_UPLOADS = 2;
const FOLDER_ERROR = "Folders can't be uploaded. Open the folder and select the files instead.";

export type UploadStatus = "queued" | "uploading" | "failed";

export type UploadItem = {
  id: number;
  ownerId: number;
  name: string;
  size: number;
  scope: DocumentScope;
  status: UploadStatus;
  /** Percentage of the request body sent (0-100). */
  progress: number;
  error: string | null;
};

export type EnqueueRequest = {
  ownerId: number;
  scope: DocumentScope;
  rules: UploadRules;
  files: File[];
  /** Names of dropped folders; they are listed as failed items. */
  folders: string[];
};

type PendingUpload = { id: number; file: File; scope: DocumentScope };

/** Called once per finished upload with the created document, or null when it failed or was cancelled. */
type SettledListener = (created: DocumentItem | null) => void;

/**
 * Upload queue shared by every DocumentPanel. It lives outside React so uploads keep running when the panel
 * unmounts (e.g. a closed slide-over) and reappear with their progress when it is opened again.
 */
class UploadManager {
  private items: UploadItem[] = [];
  private pending: PendingUpload[] = [];
  private readonly running = new Map<number, AbortController>();
  private readonly listeners = new Set<() => void>();
  private readonly settledListeners = new Set<SettledListener>();
  private nextId = 1;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getSnapshot = (): UploadItem[] => this.items;

  onSettled(listener: SettledListener): () => void {
    this.settledListeners.add(listener);
    return () => {
      this.settledListeners.delete(listener);
    };
  }

  enqueue({ ownerId, scope, rules, files, folders }: EnqueueRequest): void {
    const createItem = (name: string, size: number, error: string | null): UploadItem => ({
      id: this.nextId++,
      ownerId,
      name,
      size,
      scope,
      status: error ? "failed" : "queued",
      progress: 0,
      error,
    });
    const added = folders.map((folder) => createItem(folder, 0, FOLDER_ERROR));
    for (const file of files) {
      const item = createItem(file.name, file.size, validateUpload(file, rules));
      if (item.status === "queued") this.pending.push({ id: item.id, file, scope });
      added.push(item);
    }
    if (added.length === 0) return;
    this.setItems([...this.items, ...added]);
    this.pump();
  }

  /** Cancel a queued or running upload, or dismiss a failed one. */
  remove(id: number): void {
    const controller = this.running.get(id);
    if (controller) {
      controller.abort(); // the upload's rejection handler removes the item
      return;
    }
    this.pending = this.pending.filter((upload) => upload.id !== id);
    this.setItems(this.items.filter((item) => item.id !== id));
  }

  /** Forget, and cancel, the uploads of any other account (another user signed in in this tab). */
  retainOwner(ownerId: number): void {
    const foreign = new Set(this.items.filter((item) => item.ownerId !== ownerId).map((item) => item.id));
    if (foreign.size === 0) return;
    this.pending = this.pending.filter((upload) => !foreign.has(upload.id));
    this.setItems(this.items.filter((item) => !foreign.has(item.id)));
    for (const id of foreign) this.running.get(id)?.abort();
  }

  private pump(): void {
    while (this.running.size < MAX_PARALLEL_UPLOADS) {
      const next = this.pending.shift();
      if (!next) return;
      this.start(next);
    }
  }

  private start({ id, file, scope }: PendingUpload): void {
    const controller = new AbortController();
    this.running.set(id, controller);
    this.patch(id, { status: "uploading" });
    uploadDocument(file, scope, {
      signal: controller.signal,
      onProgress: (progress) => this.patch(id, { progress }),
    }).then(
      (created) => {
        this.finish(id, null);
        this.emitSettled(created);
      },
      (err: unknown) => {
        if (err instanceof ApiError && err.status === 401) this.reset();
        else this.finish(id, controller.signal.aborted ? null : uploadErrorMessage(err));
        this.emitSettled(null);
      },
    );
  }

  /** Cancel and forget everything: the session ended, so every remaining upload would be refused. */
  private reset(): void {
    const controllers = [...this.running.values()];
    this.running.clear();
    this.pending = [];
    this.setItems([]);
    for (const controller of controllers) controller.abort();
  }

  /** Drop a finished upload from the list, or keep it as failed when there is an error to show. */
  private finish(id: number, error: string | null): void {
    this.running.delete(id);
    if (error) this.patch(id, { status: "failed", error });
    else this.setItems(this.items.filter((item) => item.id !== id));
    this.pump();
  }

  private patch(id: number, changes: Partial<Pick<UploadItem, "status" | "progress" | "error">>): void {
    this.setItems(this.items.map((item) => (item.id === id ? { ...item, ...changes } : item)));
  }

  private setItems(items: UploadItem[]): void {
    this.items = items;
    for (const listener of this.listeners) listener();
  }

  private emitSettled(created: DocumentItem | null): void {
    for (const listener of this.settledListeners) listener(created);
  }
}

function uploadErrorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Upload failed. Please try again.";
}

export const uploadManager = new UploadManager();

/** The signed-in user's uploads: queued, running and failed ones. */
export function useUploads(ownerId: number | null): UploadItem[] {
  const items = useSyncExternalStore(uploadManager.subscribe, uploadManager.getSnapshot);
  useEffect(() => {
    if (ownerId !== null) uploadManager.retainOwner(ownerId);
  }, [ownerId]);
  return useMemo(() => items.filter((item) => item.ownerId === ownerId), [items, ownerId]);
}
