import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { ConversationSummary } from "../../api/types";

export type ConversationsState = {
  /** null until the first successful load. */
  conversations: ConversationSummary[] | null;
  error: string | null;
  reload: () => Promise<void>;
  rename: (id: number, title: string) => Promise<void>;
  remove: (id: number) => Promise<void>;
  /** Move a conversation to the top (inserting it if new) after it received a message. null keeps the title. */
  touch: (id: number, title: string | null) => void;
};

/** The sidebar list from GET /conversations, kept up to date locally after chats, renames and deletes. */
export function useConversations(): ConversationsState {
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestRequest = useRef(0);
  const loaded = useRef(false);
  // A stream that ends after its conversation was deleted must not bring it back.
  const deletedIds = useRef(new Set<number>());

  const load = useCallback(async (signal?: AbortSignal) => {
    const request = ++latestRequest.current;
    try {
      const list = await api.get<ConversationSummary[]>("/conversations", { signal });
      if (request !== latestRequest.current) return;
      loaded.current = true;
      setConversations(list);
      setError(null);
    } catch (err) {
      if (signal?.aborted || request !== latestRequest.current) return;
      setError(err instanceof ApiError ? err.message : "Couldn't load your conversations.");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const reload = useCallback(() => load(), [load]);

  const rename = useCallback(async (id: number, title: string) => {
    const updated = await api.patch<ConversationSummary>(`/conversations/${id}`, { title });
    setConversations((list) => list && list.map((item) => (item.id === id ? updated : item)));
  }, []);

  const remove = useCallback(async (id: number) => {
    try {
      await api.del(`/conversations/${id}`);
    } catch (err) {
      // Already gone (deleted in another tab) is the outcome the user asked for.
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
    deletedIds.current.add(id);
    setConversations((list) => list && list.filter((item) => item.id !== id));
  }, []);

  const touch = useCallback(
    (id: number, title: string | null) => {
      if (deletedIds.current.has(id)) return;
      if (!loaded.current) {
        void load();
        return;
      }
      const now = new Date().toISOString();
      setConversations((list) => {
        const current = list ?? [];
        const existing = current.find((item) => item.id === id);
        const known = title ?? existing?.title;
        if (known === undefined) return current;
        const updated = { id, title: known, created_at: existing?.created_at ?? now, updated_at: now };
        return [updated, ...current.filter((item) => item.id !== id)];
      });
    },
    [load],
  );

  return useMemo(
    () => ({ conversations, error, reload, rename, remove, touch }),
    [conversations, error, reload, rename, remove, touch],
  );
}
