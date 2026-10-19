import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";

export type Load<T> = {
  data: T | null;
  error: string | null;
  /** True while a request runs (the first load and every reload). */
  loading: boolean;
  /** Loads again; the current data stays on screen meanwhile. */
  reload: () => void;
};

/**
 * Loads a panel's data when it mounts (the tab was selected) and aborts it when it unmounts (another tab was selected),
 * so a tab shown again loads afresh (docs/briefs/ui-shell-settings.md 10.1). `fallback` says what failed ("Couldn't
 * load your usage."); the server's reason follows it.
 */
export function useLoad<T>(loader: (signal: AbortSignal) => Promise<T>, fallback: string): Load<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    loaderRef.current(controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setLoading(false);
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError ? `${fallbackRef.current} ${err.message}` : fallbackRef.current);
        setLoading(false);
      },
    );
    return () => controller.abort();
  }, [attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { data, error, loading, reload };
}
