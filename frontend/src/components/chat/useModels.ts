import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { ChatModel } from "../../api/types";

/** The per-device model memory of earlier versions; the account default replaced it (functional brief U7). */
const LEGACY_STORAGE_KEY = "rag.modelId";

export type ModelsState = {
  /** null while loading (or after a failed load). */
  models: ChatModel[] | null;
  error: string | null;
  /** null = let the server use its default model. */
  selectedId: number | null;
  /** What new chats start with: the account default when it is listed, else the server's default (null: unknown). */
  defaultId: number | null;
  /** Picks a model for this chat (and the following ones in this tab until New chat); never the account default. */
  select: (id: number) => void;
  /** Back to the default (New chat, or the account default changed while the thread is empty). */
  resetToDefault: () => void;
  /** Refetch the list (e.g. after a send was rejected), keeping the current choice while it's still offered. */
  reload: () => void;
};

function forgetLegacyChoice(): void {
  try {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* storage unavailable: nothing to forget */
  }
}

/** The account default when it is one of the user's models, else the server's default, else the first. */
function defaultModel(models: ChatModel[], preferred: number | null): number | null {
  if (preferred !== null && models.some((model) => model.id === preferred)) return preferred;
  return (models.find((model) => model.is_default) ?? models[0])?.id ?? null;
}

/**
 * Chat models from GET /models and the one picked for the current chat. On load and on New chat the pick is
 * `preferredDefault` (Preferences.default_model_id) when listed, else the server default (functional brief 8.2).
 */
export function useModels(preferredDefault: number | null): ModelsState {
  const [models, setModels] = useState<ChatModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const preferredRef = useRef(preferredDefault);
  preferredRef.current = preferredDefault;
  const modelsRef = useRef<ChatModel[] | null>(null);

  useEffect(forgetLegacyChoice, []);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ChatModel[]>("/models", { signal: controller.signal })
      .then((list) => {
        modelsRef.current = list;
        setModels(list);
        setError(null);
        // A choice that is still offered stays (a reload after a rejected send); one that was disabled or withdrawn
        // falls back to the default.
        setSelectedId((current) =>
          current !== null && list.some((model) => model.id === current)
            ? current
            : defaultModel(list, preferredRef.current),
        );
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        // A failed refetch keeps the list that's already shown (the error only matters while there's no list).
        setError(err instanceof ApiError ? err.message : "Couldn't load the models.");
      });
    return () => controller.abort();
  }, [reloadKey]);

  const select = useCallback((id: number) => setSelectedId(id), []);

  const resetToDefault = useCallback(() => {
    const list = modelsRef.current;
    if (list) setSelectedId(defaultModel(list, preferredRef.current));
  }, []);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  const defaultId = models ? defaultModel(models, preferredDefault) : null;
  return { models, error, selectedId, defaultId, select, resetToDefault, reload };
}
