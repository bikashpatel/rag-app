import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "../../api/client";
import { errorMessage, isAbortError } from "./adminApi";

export type Resource<T> = {
  /** Last successful response; kept while a reload is in flight or after a failed reload. */
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: Dispatch<SetStateAction<T | null>>;
};

/** GETs `path` on mount and whenever it changes. Responses to superseded requests are dropped. */
export function useResource<T>(path: string, errorFallback: string): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const latestRequest = useRef(0);
  const signalRef = useRef<AbortSignal | undefined>(undefined);

  const reload = useCallback(async () => {
    const request = ++latestRequest.current;
    setLoading(true);
    try {
      const result = await api.get<T>(path, { signal: signalRef.current });
      if (request !== latestRequest.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (isAbortError(err) || request !== latestRequest.current) return;
      setError(errorMessage(err, errorFallback));
    } finally {
      if (request === latestRequest.current) setLoading(false);
    }
  }, [path, errorFallback]);

  useEffect(() => {
    const controller = new AbortController();
    signalRef.current = controller.signal;
    void reload();
    return () => controller.abort();
  }, [reload]);

  return { data, error, loading, reload, setData };
}

/** Calls `callback` every `intervalMs` while `active` and the tab is visible. */
export function usePolling(active: boolean, intervalMs: number, callback: () => void): void {
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  });
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) callbackRef.current();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs]);
}

export type FlashTone = "success" | "error";
export type FlashEntry = { id: number; tone: FlashTone; text: string };
export type Flash = {
  message: FlashEntry | null;
  success: (text: string) => void;
  error: (text: string) => void;
  dismiss: () => void;
};

const FLASH_MS: Record<FlashTone, number> = { success: 4000, error: 9000 };

/** A short-lived inline confirmation (or error) for the outcome of an action. */
export function useFlash(): Flash {
  const [message, setMessage] = useState<FlashEntry | null>(null);
  const nextId = useRef(0);

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), FLASH_MS[message.tone]);
    return () => window.clearTimeout(timer);
  }, [message]);

  return useMemo(() => {
    const show = (tone: FlashTone) => (text: string) => setMessage({ id: ++nextId.current, tone, text });
    return { message, success: show("success"), error: show("error"), dismiss: () => setMessage(null) };
  }, [message]);
}
