import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { DocumentConfig, DocumentItem } from "../../api/types";

const POLL_INTERVAL_MS = 2500;

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
 * Loads GET /documents and GET /documents/config. The list is polled while any document is pending or processing,
 * or while `uploading` is true. A response is ignored when a newer request for the same resource has started.
 */
export function useDocuments(uploading: boolean): DocumentsState {
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

  const processing = documents?.some((doc) => doc.status === "pending" || doc.status === "processing") ?? false;
  const shouldPoll = processing || uploading;
  useEffect(() => {
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      if (!document.hidden && documentsInFlight.current === 0) void reloadDocuments();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [shouldPoll, reloadDocuments]);

  const upsertDocument = useCallback((doc: DocumentItem) => setDocuments((list) => upsert(list, doc)), []);

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
