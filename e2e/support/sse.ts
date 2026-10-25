import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import type { Page, Route } from "@playwright/test";
import type {
  AnswerSource,
  ChatStreamEvent,
  ChatStreamRequest,
  DocumentItem,
  GeneratedFileFormat,
  TokenUsage,
} from "../../frontend/src/api/types";
import { fakeId } from "./fakes";

// Canned answers for POST /api/chat/stream, in the event contract of SPEC.md section 6 ("Chat"): meta first, sources
// once after it, then deltas and file events, and done or error last. No request ever reaches the backend (or a model).

export const CHAT_STREAM = "**/api/chat/stream";

export const events = {
  meta(data: { conversationId: number; title: string; model?: { id: number; display_name: string } }): ChatStreamEvent {
    return {
      event: "meta",
      data: {
        conversation_id: data.conversationId,
        title: data.title,
        user_message_id: fakeId(),
        model: data.model ?? { id: fakeId(), display_name: "Canned Model" },
      },
    };
  },
  sources(sources: AnswerSource[]): ChatStreamEvent {
    return { event: "sources", data: { sources } };
  },
  delta(content: string): ChatStreamEvent {
    return { event: "delta", data: { content } };
  },
  warning(message: string): ChatStreamEvent {
    return { event: "warning", data: { message } };
  },
  fileWriting(callId: string, title: string | null, format: GeneratedFileFormat | null, chars = 0): ChatStreamEvent {
    return { event: "file", data: { call_id: callId, kind: "file", status: "writing", title, format, chars } };
  },
  fileCreated(callId: string, document: DocumentItem, format: GeneratedFileFormat): ChatStreamEvent {
    return {
      event: "file",
      data: {
        call_id: callId,
        kind: "file",
        status: "created",
        file: {
          document_id: document.id,
          filename: document.filename,
          format,
          size_bytes: document.size_bytes,
          status: document.status,
          image: null,
        },
        document,
        already_existed: false,
      },
    };
  },
  done(usage: Partial<TokenUsage> = {}): ChatStreamEvent {
    const prompt = usage.prompt_tokens ?? 900;
    const completion = usage.completion_tokens ?? 334;
    return {
      event: "done",
      data: {
        assistant_message_id: fakeId(),
        usage: { prompt_tokens: prompt, completion_tokens: completion, total_tokens: prompt + completion, cost_usd: usage.cost_usd ?? 0.0123 },
      },
    };
  },
  error(message: string): ChatStreamEvent {
    return { event: "error", data: { message, assistant_message_id: fakeId() } };
  },
};

/** `event: <name>\ndata: <json>\n\n` per event, as the backend writes them. */
export function encode(list: ChatStreamEvent[]): string {
  return list.map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
}

/** A passage of a (fake) document, as `sources` carries it. */
export function source(index: number, filename: string, snippet: string, page: number | null = null): AnswerSource {
  return { index, document_id: fakeId(), filename, page, chunk_index: index - 1, score: 0.87 - index * 0.05, snippet };
}

export type CannedChat = { requests: ChatStreamRequest[] };

/**
 * Answers every POST /api/chat/stream of this page with the whole canned stream at once (route.fulfill). `answer` gets
 * the request body, so a reply can depend on it. Use SseStub for answers that must arrive piece by piece.
 */
export async function cannedChat(
  page: Page,
  answer: ChatStreamEvent[] | ((request: ChatStreamRequest) => ChatStreamEvent[]),
): Promise<CannedChat> {
  const canned: CannedChat = { requests: [] };
  await page.route(CHAT_STREAM, async (route: Route) => {
    const body = route.request().postDataJSON() as ChatStreamRequest;
    canned.requests.push(body);
    await route.fulfill({
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
      body: encode(typeof answer === "function" ? answer(body) : answer),
    });
  });
  return canned;
}

/** One chat request that reached the stub: the test writes the events one at a time. */
export class StubStream {
  readonly closedByClient: Promise<void>;
  private ended = false;

  constructor(
    readonly body: ChatStreamRequest,
    private readonly res: ServerResponse,
  ) {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no" });
    res.flushHeaders();
    this.closedByClient = new Promise((resolve) => res.on("close", () => (this.ended ? undefined : resolve())));
  }

  send(...list: ChatStreamEvent[]): void {
    this.res.write(encode(list));
  }

  /** The keepalive comment the server sends after 15 s of silence; clients must ignore it. */
  keepalive(): void {
    this.res.write(": keepalive\n\n");
  }

  end(...list: ChatStreamEvent[]): void {
    if (list.length > 0) this.send(...list);
    this.ended = true;
    this.res.end();
  }
}

/**
 * A local SSE server for answers that stream piece by piece (progressive rendering, Stop). page.route() intercepts
 * POST /api/chat/stream and continues it to this server instead of the backend; the page still sees its own URL.
 */
export class SseStub {
  private readonly waiting: ((stream: StubStream) => void)[] = [];
  private readonly arrived: StubStream[] = [];
  private readonly open = new Set<ServerResponse>();

  private constructor(private readonly server: Server) {}

  static async start(): Promise<SseStub> {
    let stub: SseStub | undefined;
    const server = createServer((req, res) => stub?.accept(req, res));
    stub = new SseStub(server);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    return stub;
  }

  get url(): string {
    return `http://127.0.0.1:${(this.server.address() as AddressInfo).port}/chat/stream`;
  }

  /** Route this page's chat requests here. */
  async attach(page: Page): Promise<void> {
    await page.route(CHAT_STREAM, (route) => route.continue({ url: this.url }));
  }

  /** The next chat request (in arrival order). */
  next(timeoutMs = 10_000): Promise<StubStream> {
    const ready = this.arrived.shift();
    if (ready) return Promise.resolve(ready);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("No chat request reached the SSE stub")), timeoutMs);
      this.waiting.push((stream) => {
        clearTimeout(timer);
        resolve(stream);
      });
    });
  }

  async close(): Promise<void> {
    for (const res of this.open) res.destroy();
    await new Promise<void>((resolve) => this.server.close(() => resolve()));
  }

  private accept(req: IncomingMessage, res: ServerResponse): void {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      this.open.add(res);
      res.on("close", () => this.open.delete(res));
      const stream = new StubStream(JSON.parse(Buffer.concat(chunks).toString("utf8")) as ChatStreamRequest, res);
      const waiter = this.waiting.shift();
      if (waiter) waiter(stream);
      else this.arrived.push(stream);
    });
  }
}
