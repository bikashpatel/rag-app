import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, isPlanRequired, postSSE, type SSEMessage } from "../../api/client";
import type {
  ChatMessage,
  ChatStreamEvent,
  ChatStreamRequest,
  ConversationDetail,
  DocumentSelection,
  FileEventData,
  GeneratedFile,
  GeneratedFileFormat,
  GeneratedFileKind,
  ImageQuality,
  PluginCard,
} from "../../api/types";
import { announceDocument, requestDocumentsReload } from "../documents/documentEvents";

/**
 * A file or image the answer created or tried to create. `key` is `c<call_id>` while streaming, and `d<document_id>`
 * (`s<index>` once the document was deleted) when loaded with the conversation. `generating`: the image model is
 * making the image (`startedAt` for the elapsed time). `wasGenerating`: the stream ended while the image was being
 * generated, so the server still finishes and saves it.
 */
export type FileCard =
  | {
      key: string;
      kind: "writing";
      fileKind: GeneratedFileKind;
      title: string | null;
      format: GeneratedFileFormat | null;
      chars: number;
    }
  | { key: string; kind: "generating"; title: string; width: number; height: number; quality: ImageQuality; startedAt: number }
  | { key: string; kind: "created"; file: GeneratedFile; alreadyExisted: boolean }
  | {
      key: string;
      kind: "failed";
      fileKind: GeneratedFileKind;
      title: string | null;
      format: GeneratedFileFormat | null;
      error: string;
    }
  | {
      key: string;
      kind: "stopped";
      fileKind: GeneratedFileKind;
      title: string | null;
      format: GeneratedFileFormat | null;
      wasGenerating: boolean;
    };

const IMAGE_FORMATS: ReadonlySet<GeneratedFileFormat> = new Set(["png", "jpeg", "webp"]);

/** The card is about an image (create_image), not a file; a deleted image keeps its png, jpeg or webp format. */
export function isImageCard(card: FileCard): boolean {
  switch (card.kind) {
    case "generating":
      return true;
    case "created":
      return card.file.image !== null || IMAGE_FORMATS.has(card.file.format);
    default:
      return card.fileKind === "image";
  }
}

/**
 * A card a plugin added to the answer (`plugin` events, or `ChatMessage.plugin_cards` when loaded). `stopped`: the
 * stream ended while the card's action was still running, so its outcome is unknown here.
 */
export type ThreadPluginCard = PluginCard & { stopped: boolean };

const BUSY_PLUGIN_STATUSES: ReadonlySet<string> = new Set(["running", "writing", "creating", "importing"]);

/** The card's action is still running (searching, writing, saving or importing). */
export function pluginCardBusy(card: PluginCard): boolean {
  return BUSY_PLUGIN_STATUSES.has(card.status);
}

export type ThreadMessage = Omit<ChatMessage, "plugin_cards"> & {
  /** Stable React key that survives the swap from a temporary id to the server id. */
  key: string;
  streaming: boolean;
  stopped: boolean;
  warnings: string[];
  cards: FileCard[];
  pluginCards: ThreadPluginCard[];
};

export type ThreadStatus = "ready" | "loading" | "error";

export type SendRequest = { message: string; modelId: number | null; documentIds: DocumentSelection };

/** A request refused before streaming; `code` is the server's (`allowance_used`, `feature_not_in_plan`, …). */
export type SendError = { message: string; code: string | null };

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
  /**
   * Error from a request rejected before streaming (rate limit, allowance, model, ...). A 402 for a plan that ended has
   * none: the page shows the plan-ended panel instead.
   */
  sendError: SendError | null;
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
  /** Calls still in progress (no `created` or `failed` event yet): files being written, images being made. */
  writing: Map<string, "writing" | "generating">;
  /** Plugin cards whose action is still running, by card_id. */
  pluginBusy: Set<string>;
  /** Documents of plugin cards (imports) already announced to the panel. */
  announced: Set<number>;
};

const INTERRUPTED = "The connection was lost before the answer finished.";
const STOPPED_EARLY = "Stopped before the answer started.";
const LOST_EARLY = "The connection was lost before the answer started.";
/** Saved as `error` by the backend when the client disconnected mid-answer, e.g. Stop (routers/chat.py _STOPPED). */
const STOPPED_ERROR = "Stopped before completion";
/**
 * An image that was being generated when the stream ended is stored when the image model answers (within the server's
 * IMAGE_TIMEOUT_SECONDS, 180 s by default): the documents panel looks again a few times until then.
 */
const IMAGE_RELOAD_MS = [30_000, 90_000, 190_000];

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function toThreadMessage({ plugin_cards, ...message }: ChatMessage): ThreadMessage {
  // A saved stopped answer renders like a live one (muted "Stopped" note), not as a failure.
  const stopped = message.role === "assistant" && message.error === STOPPED_ERROR;
  return {
    ...message,
    // A card saved while its action was still running belongs to an answer that ended early.
    pluginCards: plugin_cards.map((card) => ({ ...card, stopped: pluginCardBusy(card) })),
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

/**
 * Inserts or updates the card of one create_document or create_image call; an event without a title or format keeps
 * the known one.
 */
function applyFileEvent(cards: FileCard[], data: FileEventData): FileCard[] {
  const key = `c${data.call_id}`;
  const previous = cards.find((card) => card.key === key);
  let card: FileCard;
  if (data.status === "created") {
    card = { key, kind: "created", file: data.file, alreadyExisted: data.already_existed };
  } else if (data.status === "generating") {
    const { title, width, height, quality } = data;
    const startedAt = previous?.kind === "generating" ? previous.startedAt : Date.now();
    card = { key, kind: "generating", title, width, height, quality, startedAt };
  } else {
    const title = data.title ?? (previous && previous.kind !== "created" ? previous.title : null);
    const format = data.format ?? (previous && "format" in previous ? previous.format : null);
    card =
      data.status === "writing"
        ? { key, kind: "writing", fileKind: data.kind, title, format, chars: data.chars }
        : { key, kind: "failed", fileKind: data.kind, title, format, error: data.error };
  }
  return previous ? cards.map((item) => (item === previous ? card : item)) : [...cards, card];
}

/** Replaces the card with the same card_id (a `plugin` event carries the card's whole state), else appends it. */
function upsertPluginCard(cards: ThreadPluginCard[], card: PluginCard): ThreadPluginCard[] {
  const next = { ...card, stopped: false };
  const index = cards.findIndex((item) => item.card_id === card.card_id);
  return index === -1 ? [...cards, next] : cards.map((item, i) => (i === index ? next : item));
}

/** Plugin cards whose action was still running when the stream ended. */
function stopPluginCards(cards: ThreadPluginCard[], busy: ReadonlySet<string>): ThreadPluginCard[] {
  return cards.map((card) => (busy.has(card.card_id) ? { ...card, stopped: true } : card));
}

/** The cards of calls still in progress when the stream ended early. */
function stopCards(cards: FileCard[]): FileCard[] {
  return cards.map((card) => {
    switch (card.kind) {
      case "writing": {
        const { key, fileKind, title, format } = card;
        return { key, kind: "stopped", fileKind, title, format, wasGenerating: false };
      }
      case "generating":
        return { key: card.key, kind: "stopped", fileKind: "image", title: card.title, format: null, wasGenerating: true };
      default:
        return card;
    }
  });
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
  const [sendError, setSendError] = useState<SendError | null>(null);

  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;
  /** Conversation whose messages are on screen: null = new chat, undefined = none (loading or failed). */
  const shownIdRef = useRef<number | null | undefined>(undefined);
  const streamRef = useRef<ActiveStream | null>(null);
  const mountedRef = useRef(false);
  const tempIdRef = useRef(0);
  const imageReloadTimers = useRef<number[]>([]);

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
      imageReloadTimers.current.forEach((timer) => window.clearTimeout(timer));
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
      pluginCards: [],
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
      writing: new Map(),
      pluginBusy: new Set(),
      announced: new Set(),
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
          // Sent again (the whole list) when a plugin tool adds emails, and once more before the end when emails the
          // answer doesn't cite are dropped.
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
          if (data.status === "writing" || data.status === "generating") stream.writing.set(data.call_id, data.status);
          else stream.writing.delete(data.call_id);
          // The document exists whatever happens to this thread, so the panel hears about it even when detached.
          if (data.status === "created") announceDocument(data.document);
          update((item) => ({ cards: applyFileEvent(item.cards, data) }));
          break;
        }
        case "plugin": {
          const card = sse.data;
          if (pluginCardBusy(card)) stream.pluginBusy.add(card.card_id);
          else stream.pluginBusy.delete(card.card_id);
          // Imported documents exist whatever happens to this thread: the panel hears about each one once.
          if (card.type === "gmail_import") {
            for (const { document } of card.items) {
              if (!document || stream.announced.has(document.id)) continue;
              stream.announced.add(document.id);
              announceDocument(document);
            }
          }
          update((item) => ({ pluginCards: upsertPluginCard(item.pluginCards, card) }));
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
      setSendError({ message: reason, code: null });
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
          setSendError(isPlanRequired(err) ? null : { message: err.message, code: err.code });
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
      // file it was storing), so the card can't tell, and the panel looks again. An image already being generated is
      // always finished and saved, up to a few minutes later.
      if (stream.writing.size > 0) {
        const generating = [...stream.writing.values()].includes("generating");
        stream.writing.clear();
        update((item) => ({ cards: stopCards(item.cards) }));
        requestDocumentsReload();
        if (generating) {
          imageReloadTimers.current.forEach((timer) => window.clearTimeout(timer));
          imageReloadTimers.current = IMAGE_RELOAD_MS.map((ms) => window.setTimeout(requestDocumentsReload, ms));
        }
      }
      // A plugin action still running (an import, a draft being saved) may have finished on the server.
      if (stream.pluginBusy.size > 0) {
        const busy = new Set(stream.pluginBusy);
        stream.pluginBusy.clear();
        update((item) => ({ pluginCards: stopPluginCards(item.pluginCards, busy) }));
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
