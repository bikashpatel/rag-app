import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { ChatModel } from "../../api/types";

const STORAGE_KEY = "rag.modelId";

export type ModelsState = {
  /** null while loading (or after a failed load). */
  models: ChatModel[] | null;
  error: string | null;
  /** null = let the server use its default model. */
  selectedId: number | null;
  select: (id: number) => void;
  /** Refetch the list (e.g. after a send was rejected), keeping the current choice while it's still offered. */
  reload: () => void;
};

function readStoredId(): number | null {
  try {
    const value = Number(localStorage.getItem(STORAGE_KEY));
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function storeId(id: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    /* storage unavailable (private mode, quota): the choice just isn't remembered */
  }
}

function pickModel(models: ChatModel[], stored: number | null): number | null {
  if (stored !== null && models.some((model) => model.id === stored)) return stored;
  return (models.find((model) => model.is_default) ?? models[0])?.id ?? null;
}

/** Chat models from GET /models; the choice is remembered in localStorage while it's still offered. */
export function useModels(): ModelsState {
  const [models, setModels] = useState<ChatModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ChatModel[]>("/models", { signal: controller.signal })
      .then((list) => {
        setModels(list);
        setError(null);
        // Keep the current choice while it's still offered (storage may be unavailable, so don't re-read it then);
        // a model that was disabled or withdrawn falls back to the default.
        setSelectedId((current) => pickModel(list, current ?? readStoredId()));
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        // A failed refetch keeps the list that's already shown (the error only matters while there's no list).
        setError(err instanceof ApiError ? err.message : "Couldn't load the models.");
      });
    return () => controller.abort();
  }, [reloadKey]);

  const select = useCallback((id: number) => {
    setSelectedId(id);
    storeId(id);
  }, []);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  return { models, error, selectedId, select, reload };
}
