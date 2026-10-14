// Minimal fetch wrapper. Auth is an httpOnly cookie, so requests just need same-origin credentials.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called by AuthContext: invoked whenever an API call returns 401 (session expired / revoked). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** For callers that bypass `request` (e.g. XHR uploads) and see a 401. */
export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

type ErrorBody = { detail?: unknown };

/** Turn a FastAPI error body (`{detail: string}` or validation list) into a readable message. */
export function errorMessageFromBody(status: number, body: unknown): string {
  const detail = (body as ErrorBody | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        return field ? `${field}: ${d.msg ?? "invalid"}` : (d.msg ?? "invalid");
      })
      .join("; ");
  }
  if (status === 413) return "The file is too large.";
  if (status >= 500) return "The server ran into a problem. Please try again.";
  return `Request failed (${status})`;
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  return new ApiError(res.status, errorMessageFromBody(res.status, body));
}

export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const isForm = body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: body === undefined || isForm ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      signal: options.signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the server. Check that the app is running.");
  }
  if (res.status === 401 && !path.startsWith("/auth/login")) notifyUnauthorized();
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  get: <T>(path: string, options?: { signal?: AbortSignal }) => request<T>("GET", path, undefined, options),
  post: <T>(path: string, body?: unknown, options?: { signal?: AbortSignal }) =>
    request<T>("POST", path, body ?? {}, options),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T = void>(path: string) => request<T>("DELETE", path),
};

export type SSEMessage = { event: string; data: unknown };

function parseSSEBlock(block: string): SSEMessage | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}

/**
 * POST JSON and consume a text/event-stream response, calling `onEvent` for every event.
 * Resolves when the stream ends. Rejects with ApiError for non-2xx responses (JSON error body),
 * or with an AbortError when `signal` is aborted (e.g. the Stop button).
 */
export async function postSSE(
  path: string,
  body: unknown,
  onEvent: (message: SSEMessage) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the server. Check that the app is running.");
  }
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) throw await toApiError(res);
  if (!res.body) throw new ApiError(0, "This browser does not support streaming responses.");

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + value).replace(/\r\n?/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const message = parseSSEBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (message) onEvent(message);
      boundary = buffer.indexOf("\n\n");
    }
  }
  const tail = parseSSEBlock(buffer.trim());
  if (tail) onEvent(tail);
}
