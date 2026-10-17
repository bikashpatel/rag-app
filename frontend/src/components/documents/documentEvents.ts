import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { DocumentItem } from "../../api/types";

// Shared by the documents panel and the chat's file cards (SPEC section 8), so DocumentPanel's props stay as they are.

const announcementListeners = new Set<(doc: DocumentItem) => void>();
const reloadListeners = new Set<() => void>();
const snapshotListeners = new Set<() => void>();
/** The panel's latest list by id; null until it loads or after it unmounts. */
let snapshot: Map<number, DocumentItem> | null = null;
/**
 * Every id the panel has published in a list. Only a seen id that is missing from a later list counts as deleted:
 * a list loaded before a document existed (created in another tab, or saved after a stream ended) merely doesn't know
 * it yet. Announced documents join once the panel publishes them, so a list that was already on its way can't flash
 * them as deleted (D12). Ids are never reused, so the set is never cleared (not even when the panel unmounts).
 */
const seen = new Set<number>();
/** Ids the panel was asked to catch up on (at most once each), and whether a request is already queued. */
const catchUpRequested = new Set<number>();
let catchUpQueued = false;

function subscribe<T>(listeners: Set<T>, listener: T): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function setSnapshot(next: Map<number, DocumentItem> | null): void {
  snapshot = next;
  snapshotListeners.forEach((listener) => listener());
}

/**
 * A document created outside the panel (a file the assistant made). The panel inserts it, reloads its list (so an
 * older in-flight list can't drop it) and refreshes its quota.
 */
export function announceDocument(doc: DocumentItem): void {
  // Into the snapshot right away: the card that announces it renders before the panel publishes its new list.
  if (snapshot) setSnapshot(new Map(snapshot).set(doc.id, doc));
  announcementListeners.forEach((listener) => listener(doc));
}

export function subscribeToAnnouncements(listener: (doc: DocumentItem) => void): () => void {
  return subscribe(announcementListeners, listener);
}

/**
 * Ask the panel to reload its list and quota (a stream ended while a file was still being written, or a file card
 * shows a document the panel's list doesn't have yet).
 */
export function requestDocumentsReload(): void {
  reloadListeners.forEach((listener) => listener());
}

export function subscribeToReloadRequests(listener: () => void): () => void {
  return subscribe(reloadListeners, listener);
}

/** The panel's latest list (null until loaded or after unmount). */
export function publishDocuments(list: DocumentItem[] | null): void {
  list?.forEach((doc) => seen.add(doc.id));
  setSnapshot(list && new Map(list.map((doc) => [doc.id, doc])));
}

function subscribeToSnapshot(listener: () => void): () => void {
  return subscribe(snapshotListeners, listener);
}

function snapshotLoaded(): boolean {
  return snapshot !== null;
}

function lookup(id: number | null): DocumentItem | null | undefined {
  if (snapshot === null || id === null) return undefined;
  return snapshot.get(id) ?? (seen.has(id) ? null : undefined);
}

/**
 * A card shows a document the loaded list has never had: ask the panel once per id to reload, so the card then
 * follows the panel's polling. Cards that render together share one reload.
 */
function requestCatchUp(id: number): void {
  if (catchUpRequested.has(id)) return;
  catchUpRequested.add(id);
  if (catchUpQueued) return;
  catchUpQueued = true;
  queueMicrotask(() => {
    catchUpQueued = false;
    requestDocumentsReload();
  });
}

/** The panel's latest list (announced documents included), or null until it has loaded. */
export function useDocumentList(): DocumentItem[] | null {
  const current = useSyncExternalStore(subscribeToSnapshot, () => snapshot);
  return useMemo(() => (current ? Array.from(current.values()) : null), [current]);
}

/**
 * The latest known state of one document. undefined: not known (no list loaded yet, or the list hasn't had it yet;
 * the panel is then asked once to reload). null: it was in the list and is gone (deleted).
 */
export function useDocumentSnapshot(id: number | null): DocumentItem | null | undefined {
  const doc = useSyncExternalStore(subscribeToSnapshot, () => lookup(id));
  const loaded = useSyncExternalStore(subscribeToSnapshot, snapshotLoaded);
  useEffect(() => {
    if (id !== null && loaded && doc === undefined) requestCatchUp(id);
  }, [id, loaded, doc]);
  return doc;
}
