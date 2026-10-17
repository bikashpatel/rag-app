// Minimal fetch wrapper. Auth is an httpOnly cookie, so requests just need same-origin credentials.

import type {
  AdminPlugin,
  AdminPluginUpdate,
  GeneratedImageDetails,
  GmailImportRequest,
  GmailImportResult,
  GmailThreadDetail,
  GmailThreadPage,
  GmailThreadSearch,
  PendingSend,
  PendingSendConfirm,
  PendingSendRevision,
  PluginActivityReport,
  PluginConnectionStatus,
  PluginConnectionUpdate,
  PluginConnectRequest,
  PluginConnectResponse,
  PluginInfo,
  RequestCheckTest,
  RequestCheckTestResult,
} from "./types";

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

/** Same-origin link to a document's file; the server sends it as an attachment, so a plain link downloads it. */
export function documentDownloadUrl(id: number): string {
  return `/api/documents/${id}/download`;
}

/** `full`: the stored image; `preview` (long edge 1024) and `thumb` (256): WebP renditions. */
export type ImageRendition = "full" | "preview" | "thumb";

/** Same-origin URL of a generated image for display (served inline). */
export function documentImageUrl(id: number, size: ImageRendition = "full"): string {
  return `/api/documents/${id}/image?size=${size}`;
}

/** The prompt, revised prompt and image model of a generated image (only its owner gets them). */
export function getImageDetails(id: number, options?: { signal?: AbortSignal }): Promise<GeneratedImageDetails> {
  return api.get<GeneratedImageDetails>(`/documents/${id}/image/details`, options);
}

// ---------------- Plugins (SPEC section 6, "Plugins"; docs/briefs/plugins-gmail.md section 15) ----------------

const pluginProblemListeners = new Set<() => void>();

/**
 * Called after any 403 (plugin or capability turned off) or 409 (not connected, reconnect needed, another account)
 * from a plugin endpoint, so the plugin store can reload. Returns the unsubscribe function.
 */
export function onPluginProblem(listener: () => void): () => void {
  pluginProblemListeners.add(listener);
  return () => pluginProblemListeners.delete(listener);
}

async function pluginRequest<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  try {
    return await request<T>(method, path, body, { signal });
  } catch (err) {
    if (err instanceof ApiError && (err.status === 403 || err.status === 409)) {
      pluginProblemListeners.forEach((listener) => listener());
    }
    throw err;
  }
}

const pluginPath = (pluginId: string) => `/plugins/${encodeURIComponent(pluginId)}`;

export function listPlugins(signal?: AbortSignal): Promise<PluginInfo[]> {
  return api.get<PluginInfo[]>("/plugins", { signal });
}

/** Starts the OAuth flow and navigates this window to the provider's consent page (resolves only on failure). */
export async function startPluginConnect(pluginId: string, capabilities?: string[]): Promise<void> {
  const body: PluginConnectRequest = capabilities ? { capabilities } : {};
  const { authorize_url } = await pluginRequest<PluginConnectResponse>("POST", `${pluginPath(pluginId)}/connect`, body);
  window.location.assign(authorize_url);
}

export function updatePluginConnection(pluginId: string, update: PluginConnectionUpdate): Promise<PluginConnectionStatus> {
  return pluginRequest<PluginConnectionStatus>("PATCH", `${pluginPath(pluginId)}/connection`, update);
}

export function disconnectPlugin(pluginId: string): Promise<void> {
  return pluginRequest<void>("DELETE", `${pluginPath(pluginId)}/connection`);
}

export function searchGmailThreads(
  query: { q?: string; pageToken?: string | null; maxResults?: number },
  signal?: AbortSignal,
): Promise<GmailThreadPage> {
  // A POST body, never a URL query: search terms must not reach the access logs.
  const body: GmailThreadSearch = {
    q: query.q || null,
    page_token: query.pageToken || null,
    ...(query.maxResults ? { max_results: query.maxResults } : {}),
  };
  return pluginRequest<GmailThreadPage>("POST", "/plugins/gmail/threads/search", body, signal);
}

export function getGmailThread(threadId: string, signal?: AbortSignal): Promise<GmailThreadDetail> {
  return pluginRequest<GmailThreadDetail>("GET", `/plugins/gmail/threads/${encodeURIComponent(threadId)}`, undefined, signal);
}

export function importFromGmail(body: GmailImportRequest): Promise<GmailImportResult> {
  return pluginRequest<GmailImportResult>("POST", "/plugins/gmail/import", body);
}

export function getPendingSend(id: number): Promise<PendingSend> {
  return pluginRequest<PendingSend>("GET", `/plugins/gmail/sends/${id}`);
}

/** Sends exactly the email the card showed (identified by its content hash), once. */
export function confirmPendingSend(id: number, contentHash: string): Promise<PendingSend> {
  const body: PendingSendConfirm = { content_hash: contentHash };
  return pluginRequest<PendingSend>("POST", `/plugins/gmail/sends/${id}/send`, body);
}

export function cancelPendingSend(id: number): Promise<PendingSend> {
  return pluginRequest<PendingSend>("POST", `/plugins/gmail/sends/${id}/cancel`, {});
}

/** Edit: a new proposal replaces this one (returned). */
export function revisePendingSend(id: number, revision: PendingSendRevision): Promise<PendingSend> {
  return pluginRequest<PendingSend>("POST", `/plugins/gmail/sends/${id}/revise`, revision);
}

export function listAdminPlugins(signal?: AbortSignal): Promise<AdminPlugin[]> {
  return api.get<AdminPlugin[]>("/admin/plugins", { signal });
}

export function updateAdminPlugin(pluginId: string, update: AdminPluginUpdate): Promise<AdminPlugin> {
  return api.patch<AdminPlugin>(`/admin${pluginPath(pluginId)}`, update);
}

export function adminPluginActivityPath(pluginId: string, month: string): string {
  return `/admin${pluginPath(pluginId)}/activity?month=${encodeURIComponent(month)}`;
}

export function getAdminPluginActivity(pluginId: string, month: string, signal?: AbortSignal): Promise<PluginActivityReport> {
  return api.get<PluginActivityReport>(adminPluginActivityPath(pluginId, month), { signal });
}

export function disconnectAllPluginUsers(pluginId: string): Promise<{ disconnected: number }> {
  return api.post<{ disconnected: number }>(`/admin${pluginPath(pluginId)}/disconnect-all`);
}

/** Runs the request check (the model call that gates drafts, sends and imports) once with this chat model. */
export function testRequestCheck(modelId: number): Promise<RequestCheckTestResult> {
  const body: RequestCheckTest = { model_id: modelId };
  return api.post<RequestCheckTestResult>("/admin/plugins/request-check/test", body);
}

export type SSEMessage = { event: string; data: unknown };

/** One event block; comment lines (": keepalive") are skipped, so a block of only comments yields null. */
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
