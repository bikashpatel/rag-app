import type { ChatStreamEvent, TokenUsage } from "../api/types";
import type { FetchCall } from "./http";

/** One event exactly as the backend writes it (routers/chat.py `_event`). */
export function sse(event: ChatStreamEvent): string {
  return `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`;
}

export const KEEPALIVE = ": keepalive\n\n";

export function metaEvent(conversationId: number, title: string, userMessageId = 40): ChatStreamEvent {
  return {
    event: "meta",
    data: { conversation_id: conversationId, title, user_message_id: userMessageId, model: { id: 1, display_name: "Claude Opus 5.5" } },
  };
}

export function deltaEvent(content: string): ChatStreamEvent {
  return { event: "delta", data: { content } };
}

export function doneEvent(assistantMessageId: number, usage: Partial<TokenUsage> = {}): ChatStreamEvent {
  return {
    event: "done",
    data: {
      assistant_message_id: assistantMessageId,
      usage: { prompt_tokens: 1_200, completion_tokens: 180, total_tokens: 1_380, cost_usd: 0.0105, ...usage },
    },
  };
}

/**
 * A chat stream the test feeds event by event. `respond` is the POST /chat/stream route; aborting the request errors
 * the body with an AbortError, as a browser's fetch does.
 */
export function liveStream() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let closed = false;
  const body = new ReadableStream<Uint8Array>({
    start(streamController) {
      controller = streamController;
    },
  });
  return {
    respond(call: FetchCall): Response {
      call.signal?.addEventListener("abort", () => {
        if (closed) return;
        closed = true;
        controller.error(new DOMException("The operation was aborted.", "AbortError"));
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    },
    push(...events: (ChatStreamEvent | string)[]): void {
      for (const event of events) controller.enqueue(encoder.encode(typeof event === "string" ? event : sse(event)));
    },
    end(): void {
      if (closed) return;
      closed = true;
      controller.close();
    },
  };
}
