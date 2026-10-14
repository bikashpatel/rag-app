import { api, ApiError } from "../../api/client";

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** DELETE that treats 404 as success: the resource being gone is the outcome the admin asked for. */
export async function deleteResource(path: string): Promise<void> {
  try {
    await api.del(path);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) throw err;
  }
}

/** POST /admin/reindex; returns how many documents were queued. */
export async function reindexDocuments(onlyStale: boolean): Promise<number> {
  const { queued } = await api.post<{ queued: number }>("/admin/reindex", { only_stale: onlyStale });
  return queued;
}

export function queuedMessage(queued: number): string {
  if (queued === 0) return "Nothing needed re-indexing.";
  return `Queued ${queued} document${queued === 1 ? "" : "s"} for re-indexing.`;
}
