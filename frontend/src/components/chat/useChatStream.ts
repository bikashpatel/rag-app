import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, postSSE, type SSEMessage } from "../../api/client";
import type {
  ChatMessage,
  ChatStreamEvent,
  ChatStreamRequest,
  ConversationDetail,
  DocumentSelection,
  FileEventData,
  GeneratedFile,
  GeneratedFileFormat,
} from "../../api/types";
import { announceDocument, requestDocumentsReload } from "../documents/documentEvents";

/**
 * A file the answer created or tried to create. `key` is `c<call_id>` while streaming, and `d<document_id>` (`s<index>`
 * once the document was deleted) when loaded with the conversation.
 */
export type FileCard =
  | { key: string; kind: "writing"; title: string | null; format: GeneratedFileFormat | null; chars: number }
  | { key: string; kind: "created"; file: GeneratedFile; alreadyExisted: boolean }
  | { key: string; kind: "failed"; title: string | null; format: GeneratedFileFormat | null; error: string }
  | { key: string; kind: "stopped"; title: string | null; format: GeneratedFileFormat | null };

export type ThreadMessage = ChatMessage & {
  /** Stable React key that survives the swap from a temporary id to the server id. */
  key: string;
  streaming: boolean;
  stopped: boolean;
  warnings: string[];
  cards: FileCard[];
};

export type ThreadStatus = "ready" | "loading" | "error";

export type SendRequest = { message: string; modelId: number | null; documentIds: DocumentSelection };

type Callbacks = {
  /** The first message of a new chat created conversation `id` (navigate to it). */
  onConversationCreated: (id: number, title: string) => void;
  /** GET /conversations/{id} returned 404. */
  onConversationMissing: () => void;
  /**
   * A stream that reached the server ended (done, error or stopped). `null` when it ended before the conversation
   * was known; `title` is null when it ended before the meta event. `settled`: the server ended it (done or error),
   * so the answer and its usage are already saved; after Stop or a lost connection they are saved a moment later.
   */
  onTurnFinished: (turn: { conversationId: number; title: string | null } | null, settled: boolean) => void;
};

export type ChatThread = {
  /** Title from the loaded conversation or the stream's meta event; null for a new chat. */
  title: string | null;
  messages: ThreadMessage[];
  status: ThreadStatus;
  loadError: string | null;
  /** Changes whenever the thread is replaced (another conversation or a new chat), not when a stream adopts its new id. */
  threadKey: number;
  streaming: boolean;
  /** Error from a request rejected before streaming (rate limit, budget, model, ...). */
  sendError: string | null;
  dismissSendError: () => void;
  /**
   * Resolves false when the turn didn't start: rejected before streaming, or a new chat that ended before the
   * conversation was known. The caller restores the draft.
   */
  send: (request: SendRequest) => Promise<boolean>;
  stop: () => void;
  /** Re-load the current conversation (or clear the new chat). */
  reload: () => void;
};

type ActiveStream = {
  controller: AbortController;
  content: string;
  frame: number | null;
  conversationId: number | null;
  title: string | null;
  finished: boolean;
  /** Set when the thread was replaced while streaming: the stream must no longer touch the state. */
  detached: boolean;
  /** call_ids of files still being written (no `created` or `failed` event yet). */
  writing: Set<string>;
};

const INTERRUPTED = "The connection was lost before the answer finished.";
const STOPPED_EARLY = "Stopped before the answer started.";
const LOST_EARLY = "The connection was lost before the answer started.";
/** Saved as `error` by the backend when the client disconnected mid-answer, e.g. Stop (routers/chat.py _STOPPED). */
const STOPPED_ERROR = "Stopped before completion";

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function toThreadMessage(message: ChatMessage): ThreadMessage {
  // A saved stopped answer renders like a live one (muted "Stopped" note), not as a failure.
  const stopped = message.role === "assistant" && message.error === STOPPED_ERROR;
  return {
    ...message,
    error: stopped ? null : message.error,
    key: `m${message.id}`,
    streaming: false,
    stopped,
    warnings: [],
    cards: message.files.map((file, i) => ({
      key: file.document_id === null ? `s${i}` : `d${file.document_id}`,
      kind: "created",
      file,
      alreadyExisted: false,
    })),
  };
}

/** Inserts or updates the card of one create_document call; an event without a title or format keeps the known one. */
function applyFileEvent(cards: FileCard[], data: FileEventData): FileCard[] {
  const key = `c${data.call_id}`;
  const previous = cards.find((card) => card.key === key);
  let card: FileCard;
  if (data.status === "created") {
    card = { key, kind: "created", file: data.file, alreadyExisted: data.already_existed };
  } else {
    const known = previous?.kind === "created" ? undefined : previous;
    const title = data.title ?? known?.title ?? null;
    const format = data.format ?? known?.format ?? null;
    card =
      data.status === "writing"
        ? { key, kind: "writing", title, format, chars: data.chars }
        : { key, kind: "failed", title, format, error: data.error };
  }
  return previous ? cards.map((item) => (item === previous ? card : item)) : [...cards, card];
}

/**
 * Messages of the conversation in the URL (null = new chat) plus streaming answers from POST /chat/stream.
 * Deltas are buffered and flushed once per animation frame.
 */
export function useChatStream(conversationId: number | null, callbacks: Callbacks): ChatThread {
  const [title, setTitle] = useState<string | null>(null);
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [status, setStatus] = useState<ThreadStatus>("ready");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [threadKey, setThreadKey] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [streaming, setStreaming] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;
  /** Conversation whose messages are on screen: null = new chat, undefined = none (loading or failed). */
  const shownIdRef = useRef<number | null | undefined>(undefined);
  const streamRef = useRef<ActiveStream | null>(null);
  const mountedRef = useRef(false);
  const tempIdRef = useRef(0);

  const detachStream = useCallback(() => {
    const active = streamRef.current;
    if (!active) return;
    active.detached = true;
    active.controller.abort();
    streamRef.current = null;
    setStreaming(false);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      detachStream();
    };
  }, [detachStream]);

  useEffect(() => {
    if (conversationId === shownIdRef.current) return;
    detachStream();
    setTitle(null);
    setMessages([]);
    setSendError(null);
    setLoadError(null);
    setThreadKey((key) => key + 1);
    if (conversationId === null) {
      shownIdRef.current = null;
      setStatus("ready");
      return;
    }
    shownIdRef.current = undefined;
    setStatus("loading");
    const controller = new AbortController();
    api
      .get<ConversationDetail>(`/conversations/${conversationId}`, { signal: controller.signal })
      .then((detail) => {
        shownIdRef.current = detail.id;
        setTitle(detail.title);
        setMessages(detail.messages.map(toThreadMessage));
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) {
          callbacksRef.current.onConversationMissing();
          return;
        }
        setLoadError(err instanceof ApiError ? err.message : "Couldn't load this conversation.");
        setStatus("error");
      });
    return () => controller.abort();
  }, [conversationId, reloadKey, detachStream]);

  const reload = useCallback(() => {
    shownIdRef.current = undefined;
    setReloadKey((key) => key + 1);
  }, []);

  const send = useCallback(async ({ message, modelId, documentIds }: SendRequest): Promise<boolean> => {
    const conversationId = shownIdRef.current;
    if (streamRef.current || conversationId === undefined) return false;
    const now = new Date().toISOString();
    const userId = --tempIdRef.current;
    const assistantId = --tempIdRef.current;
    const userKey = `t${userId}`;
    const assistantKey = `t${assistantId}`;
    const blank = {
      sources: [],
      model_name: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      error: null,
      files: [],
      cards: [],
      created_at: now,
    };
    setMessages((list) => [
      ...list,
      { ...blank, id: userId, key: userKey, role: "user", content: message, streaming: false, stopped: false, warnings: [] },
      { ...blank, id: assistantId, key: assistantKey, role: "assistant", content: "", streaming: true, stopped: false, warnings: [] },
    ]);
    setSendError(null);
    setStreaming(true);

    const stream: ActiveStream = {
      controller: new AbortController(),
      content: "",
      frame: null,
      conversationId,
      title: null,
      finished: false,
      detached: false,
      writing: new Set(),
    };
    streamRef.current = stream;

    const update = (change: (message: ThreadMessage) => Partial<ThreadMessage>) => {
      if (stream.detached) return;
      setMessages((list) => list.map((item) => (item.key === assistantKey ? { ...item, ...change(item) } : item)));
    };
    const flush = () => {
      if (stream.frame !== null) cancelAnimationFrame(stream.frame);
      stream.frame = null;
      update(() => ({ content: stream.content }));
    };

    const onEvent = ({ event, data }: SSEMessage) => {
      const sse = { event, data } as ChatStreamEvent;
      switch (sse.event) {
        case "meta": {
          const { conversation_id, title: conversationTitle, user_message_id, model } = sse.data;
          const created = stream.conversationId === null;
          stream.conversationId = conversation_id;
          stream.title = conversationTitle;
          if (stream.detached) return;
          setTitle(conversationTitle);
          setMessages((list) =>
            list.map((item) =>
              item.key === userKey
                ? { ...item, id: user_message_id }
                : item.key === assistantKey
                  ? { ...item, model_name: model.display_name }
                  : item,
            ),
          );
          if (created) {
            shownIdRef.current = conversation_id;
            callbacksRef.current.onConversationCreated(conversation_id, conversationTitle);
          }
          break;
        }
        case "sources":
          update(() => ({ sources: sse.data.sources }));
          break;
        case "warning":
          update((item) => ({ warnings: [...item.warnings, sse.data.message] }));
          break;
        case "delta":
          stream.content += sse.data.content;
          if (stream.frame === null && !stream.detached) stream.frame = requestAnimationFrame(flush);
          break;
        case "file": {
          const data = sse.data;
          if (data.status === "writing") stream.writing.add(data.call_id);
          else stream.writing.delete(data.call_id);
          // The document exists whatever happens to this thread, so the panel hears about it even when detached.
          if (data.status === "created") announceDocument(data.document);
          update((item) => ({ cards: applyFileEvent(item.cards, data) }));
          break;
        }
        case "done":
          stream.finished = true;
          flush();
          update(() => ({
            id: sse.data.assistant_message_id,
            prompt_tokens: sse.data.usage.prompt_tokens,
            completion_tokens: sse.data.usage.completion_tokens,
            streaming: false,
          }));
          break;
        case "error":
          stream.finished = true;
          flush();
          update((item) => ({
            id: sse.data.assistant_message_id ?? item.id,
            error: sse.data.message,
            streaming: false,
          }));
          break;
      }
    };

    const body: ChatStreamRequest = {
      conversation_id: conversationId,
      message,
      model_id: modelId,
      document_ids: documentIds,
    };
    let rejected = false;
    let undone = false;
    // A new chat that ended before `meta` never learned its conversation id: the next send would silently start
    // another conversation under this turn. Drop the optimistic turn and hand the text back instead (any stub the
    // server already created shows up in the sidebar through onTurnFinished(null)).
    const undoUnstartedTurn = (reason: string): boolean => {
      if (stream.detached || stream.conversationId !== null) return false;
      setMessages((list) => list.filter((item) => item.key !== userKey && item.key !== assistantKey));
      setSendError(reason);
      return true;
    };
    try {
      await postSSE("/chat/stream", body, onEvent, stream.controller.signal);
      if (!stream.finished) {
        undone = undoUnstartedTurn(LOST_EARLY);
        if (!undone) {
          flush();
          update(() => ({ streaming: false, error: INTERRUPTED }));
        }
      }
    } catch (err) {
      if (isAbortError(err)) {
        if (!stream.finished) {
          undone = undoUnstartedTurn(STOPPED_EARLY);
          if (!undone) {
            flush();
            update(() => ({ streaming: false, stopped: true }));
          }
        }
      } else if (err instanceof ApiError) {
        // postSSE throws ApiError only before the stream starts: nothing was saved, so undo the optimistic messages.
        rejected = true;
        if (!stream.detached) {
          setMessages((list) => list.filter((item) => item.key !== userKey && item.key !== assistantKey));
          setSendError(err.message);
        }
      } else {
        undone = undoUnstartedTurn(LOST_EARLY);
        if (!undone) {
          flush();
          update(() => ({ streaming: false, error: INTERRUPTED }));
        }
      }
    } finally {
      if (stream.frame !== null) cancelAnimationFrame(stream.frame);
      // Stop or a lost connection while a file was being written: the server may still have saved it (it finishes a
      // file it was storing), so the card can't tell, and the panel looks again.
      if (stream.writing.size > 0) {
        stream.writing.clear();
        update((item) => ({
          cards: item.cards.map((card) =>
            card.kind === "writing" ? { key: card.key, kind: "stopped", title: card.title, format: card.format } : card,
          ),
        }));
        requestDocumentsReload();
      }
      if (streamRef.current === stream) {
        streamRef.current = null;
        setStreaming(false);
      }
    }
    if (!rejected && mountedRef.current) {
      callbacksRef.current.onTurnFinished(
        stream.conversationId === null ? null : { conversationId: stream.conversationId, title: stream.title },
        stream.finished,
      );
    }
    return !rejected && !undone;
  }, []);

  const stop = useCallback(() => streamRef.current?.controller.abort(), []);
  const dismissSendError = useCallback(() => setSendError(null), []);

  return { title, messages, status, loadError, threadKey, streaming, sendError, dismissSendError, send, stop, reload };
}
