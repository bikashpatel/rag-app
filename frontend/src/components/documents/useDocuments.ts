import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { DocumentConfig, DocumentItem } from "../../api/types";
import { publishDocuments, subscribeToAnnouncements, subscribeToReloadRequests } from "./documentEvents";

const POLL_INTERVAL_MS = 2500;
/**
 * A reload request mostly follows a stream that ended mid-file; the server may commit that file a moment later. (A
 * file card catching up on a document this list doesn't know also asks; the extra reloads are harmless there.)
 */
const LATE_RELOAD_MS = [1500, 5000];

export type DocumentsState = {
  /** null until the first successful load. */
  documents: DocumentItem[] | null;
  config: DocumentConfig | null;
  documentsError: string | null;
  configError: string | null;
  reloadDocuments: () => Promise<void>;
  reloadConfig: () => Promise<void>;
  /** Insert or replace a document returned by the server. */
  upsertDocument: (doc: DocumentItem) => void;
  deleteDocument: (id: number) => Promise<void>;
  reprocessDocument: (id: number) => Promise<void>;
};

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function upsert(list: DocumentItem[] | null, doc: DocumentItem): DocumentItem[] | null {
  if (list === null) return null;
  const index = list.findIndex((item) => item.id === doc.id);
  if (index === -1) return [doc, ...list];
  const next = list.slice();
  next[index] = doc;
  return next;
}

/**
 * Loads GET /documents and GET /documents/config, and reloads both when the tab becomes visible again. The list is
 * polled while any document is pending or processing, or while `uploading` is true. A response is ignored when a
 * newer request for the same resource has started. The list is published to `documentEvents`, and documents
 * announced there (files the assistant created) are inserted at once. Without `active` (the plan ended on the page,
 * plans-billing.md 8.7) it stops polling and reloading by itself.
 */
export function useDocuments(uploading: boolean, active = true): DocumentsState {
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null);
  const [config, setConfig] = useState<DocumentConfig | null>(null);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const signalRef = useRef<AbortSignal | undefined>(undefined);
  const latestDocumentsRequest = useRef(0);
  const latestConfigRequest = useRef(0);
  const documentsInFlight = useRef(0);

  const reloadDocuments = useCallback(async () => {
    const request = ++latestDocumentsRequest.current;
    documentsInFlight.current += 1;
    try {
      const list = await api.get<DocumentItem[]>("/documents", { signal: signalRef.current });
      if (request !== latestDocumentsRequest.current) return;
      setDocuments(list);
      setDocumentsError(null);
    } catch (err) {
      if (!isAbortError(err) && request === latestDocumentsRequest.current) {
        setDocumentsError(errorMessage(err, "Couldn't load your documents."));
      }
    } finally {
      documentsInFlight.current -= 1;
    }
  }, []);

  const reloadConfig = useCallback(async () => {
    const request = ++latestConfigRequest.current;
    try {
      const next = await api.get<DocumentConfig>("/documents/config", { signal: signalRef.current });
      if (request !== latestConfigRequest.current) return;
      setConfig(next);
      setConfigError(null);
    } catch (err) {
      if (!isAbortError(err) && request === latestConfigRequest.current) {
        setConfigError(errorMessage(err, "Couldn't load the upload settings."));
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    signalRef.current = controller.signal;
    void reloadDocuments();
    void reloadConfig();
    return () => controller.abort();
  }, [reloadDocuments, reloadConfig]);

  useEffect(() => {
    if (!active) return;
    const onVisibilityChange = () => {
      if (document.hidden) return;
      void reloadDocuments();
      void reloadConfig();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [active, reloadDocuments, reloadConfig]);

  const processing = documents?.some((doc) => doc.status === "pending" || doc.status === "processing") ?? false;
  const shouldPoll = active && (processing || uploading);
  useEffect(() => {
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      if (!document.hidden && documentsInFlight.current === 0) void reloadDocuments();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [shouldPoll, reloadDocuments]);

  const upsertDocument = useCallback((doc: DocumentItem) => setDocuments((list) => upsert(list, doc)), []);

  useEffect(() => publishDocuments(documents), [documents]);
  useEffect(() => () => publishDocuments(null), []);

  useEffect(
    () =>
      subscribeToAnnouncements((doc) => {
        upsertDocument(doc);
        // Starts a newer request, so a list fetched before the document existed can no longer replace this one.
        void reloadDocuments();
        void reloadConfig();
      }),
    [upsertDocument, reloadDocuments, reloadConfig],
  );

  useEffect(() => {
    const timers: number[] = [];
    const unsubscribe = subscribeToReloadRequests(() => {
      timers.splice(0).forEach((timer) => window.clearTimeout(timer));
      const reload = () => {
        void reloadDocuments();
        void reloadConfig();
      };
      reload();
      timers.push(...LATE_RELOAD_MS.map((ms) => window.setTimeout(reload, ms)));
    });
    return () => {
      unsubscribe();
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [reloadDocuments, reloadConfig]);

  const deleteDocument = useCallback(
    async (id: number) => {
      try {
        await api.del(`/documents/${id}`);
      } catch (err) {
        // Already gone (deleted elsewhere) is the outcome the user asked for.
        if (!(err instanceof ApiError && err.status === 404)) throw err;
      }
      setDocuments((list) => list && list.filter((doc) => doc.id !== id));
      void reloadDocuments();
      void reloadConfig();
    },
    [reloadDocuments, reloadConfig],
  );

  const reprocessDocument = useCallback(
    async (id: number) => {
      try {
        upsertDocument(await api.post<DocumentItem>(`/documents/${id}/reprocess`));
      } finally {
        void reloadDocuments();
      }
    },
    [reloadDocuments, upsertDocument],
  );

  return {
    documents,
    config,
    documentsError,
    configError,
    reloadDocuments,
    reloadConfig,
    upsertDocument,
    deleteDocument,
    reprocessDocument,
  };
}
