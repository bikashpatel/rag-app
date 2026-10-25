import { vi } from "vitest";

/** One request the app sent: the path without the "/api" prefix (query string kept) and the parsed JSON body. */
export type FetchCall = { method: string; path: string; body: unknown; signal: AbortSignal | null };

type Reply = Response | object | string | number | boolean | null | undefined;
/** A JSON body (status 200), or a function of the request returning one (or a Response, possibly later). */
export type Route = Exclude<Reply, Response> | ((call: FetchCall) => Reply | Promise<Reply>);

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });
}

export function errorResponse(status: number, detail: unknown): Response {
  return jsonResponse({ detail }, { status });
}

/** A Response whose body streams `chunks` (text), one read each. */
export function streamResponse(chunks: string[], init: ResponseInit = {}): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" }, ...init });
}

function parseBody(body: BodyInit | null | undefined): unknown {
  if (typeof body !== "string") return body ?? undefined;
  try {
    return JSON.parse(body);
  } catch {
    return body;
  }
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

/**
 * Replaces `fetch` with a router keyed "METHOD /path" (without "/api"; a key with the query string wins over one
 * without). Unrouted requests answer 500 with a detail naming them. Returns the live list of requests sent.
 */
export function stubFetch(routes: Record<string, Route>): FetchCall[] {
  const calls: FetchCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(input instanceof Request ? input.url : String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const pathname = url.pathname.replace(/^\/api/, "");
    const signal = init.signal ?? null;
    const call: FetchCall = { method, path: pathname + url.search, body: parseBody(init.body), signal };
    calls.push(call);
    if (signal?.aborted) throw abortError();
    const route = routes[`${method} ${pathname}${url.search}`] ?? routes[`${method} ${pathname}`];
    if (route === undefined) return errorResponse(500, `Unrouted request: ${method} ${call.path}`);
    const reply = typeof route === "function" ? route(call) : route;
    const settled = signal
      ? await Promise.race([
          Promise.resolve(reply),
          new Promise<never>((_, reject) => signal.addEventListener("abort", () => reject(abortError()), { once: true })),
        ])
      : await reply;
    return settled instanceof Response ? settled : jsonResponse(settled);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

/** A promise with its resolve function, to hold a response until the test releases it. */
export function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (reason: unknown) => void } {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
