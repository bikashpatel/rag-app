// Minimal fetch wrapper. Auth is an httpOnly cookie, so requests just need same-origin credentials.

import type {
  AccountInfo,
  AdminPlugin,
  AdminPluginUpdate,
  ConversationsDeleted,
  GeneratedImageDetails,
  GmailImportRequest,
  GmailImportResult,
  GmailThreadDetail,
  GmailThreadPage,
  GmailThreadSearch,
  Me,
  MyUsage,
  PendingSend,
  PendingSendConfirm,
  PendingSendRevision,
  PluginActivityReport,
  PluginConnectionStatus,
  PluginConnectionUpdate,
  PluginConnectRequest,
  PluginConnectResponse,
  PluginInfo,
  Preferences,
  PreferencesUpdate,
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

/** Their callers handle a 401 themselves (a wrong password; "signed out" at boot and in the session check). */
const SESSION_PATHS: ReadonlySet<string> = new Set(["/auth/login", "/auth/me"]);

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
  if (res.status === 401 && !SESSION_PATHS.has(path.split("?")[0])) notifyUnauthorized();
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

// ---------------- Session and account (SPEC section 6, "Auth" and "Account") ----------------

/** Requests that re-issue the session cookie (change password, log out others) while they are in flight. */
const sessionChanges = new Set<Promise<unknown>>();

function trackSessionChange<T>(promise: Promise<T>): Promise<T> {
  sessionChanges.add(promise);
  const forget = () => {
    sessionChanges.delete(promise);
  };
  promise.then(forget, forget);
  return promise;
}

/**
 * GET /auth/me for the session check (U25): the Me, or null on 401, without notifying the unauthorized handler;
 * rejects with ApiError on any other failure. It waits for a cookie re-issue in flight first, so the check doesn't
 * ask with the cookie that request is about to replace.
 */
export async function getSessionUser(signal?: AbortSignal): Promise<Me | null> {
  while (sessionChanges.size > 0) await Promise.allSettled([...sessionChanges]);
  try {
    return await api.get<Me>("/auth/me", { signal });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

export function getPreferences(signal?: AbortSignal): Promise<Preferences> {
  return api.get<Preferences>("/me/preferences", { signal });
}

export function patchPreferences(update: PreferencesUpdate): Promise<Preferences> {
  return api.patch<Preferences>("/me/preferences", update);
}

export function getAccount(signal?: AbortSignal): Promise<AccountInfo> {
  return api.get<AccountInfo>("/me/account", { signal });
}

export function getMyUsage(signal?: AbortSignal): Promise<MyUsage> {
  return api.get<MyUsage>("/me/usage", { signal });
}

/** Signs out every other session; this browser gets a new cookie. */
export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return trackSessionChange(
    api.post<void>("/auth/change-password", { current_password: currentPassword, new_password: newPassword }),
  );
}

/** POST /auth/logout-others: every other session ends (other browsers, devices and Bearer tokens); this one stays. */
export function logoutOtherSessions(): Promise<void> {
  return trackSessionChange(api.post<void>("/auth/logout-others"));
}

/** DELETE /conversations: every conversation of the caller (documents stay). */
export function deleteAllConversations(): Promise<ConversationsDeleted> {
  return api.del<ConversationsDeleted>("/conversations");
}

const EXPORT_FALLBACK_NAME = "rag-assistant-export.zip";

/** The file name of a `Content-Disposition: attachment; filename="…"` header, if it is a plain, safe one. */
function attachmentFilename(header: string | null): string | null {
  const match = header ? /filename="([^"\\/]+)"/i.exec(header) : null;
  return match ? match[1] : null;
}

function headerCount(res: Response, name: string): number {
  const value = Number.parseInt(res.headers.get(name) ?? "", 10);
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

/**
 * POST /me/export with fetch (an empty JSON body); ApiError (with the server's detail) on non-2xx. The file name comes
 * from Content-Disposition (fallback "rag-assistant-export.zip"), the counts from X-Export-Conversations / -Omitted.
 */
export async function downloadMyData(
  signal?: AbortSignal,
): Promise<{ blob: Blob; filename: string; conversations: number; omitted: number }> {
  let res: Response;
  try {
    res = await fetch("/api/me/export", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the server. Check that the app is running.");
  }
  if (res.status === 401) notifyUnauthorized();
  if (!res.ok) throw await toApiError(res);
  let blob: Blob;
  try {
    blob = await res.blob();
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "The download was interrupted. Please try again.");
  }
  return {
    blob,
    filename: attachmentFilename(res.headers.get("Content-Disposition")) ?? EXPORT_FALLBACK_NAME,
    conversations: headerCount(res, "X-Export-Conversations"),
    omitted: headerCount(res, "X-Export-Omitted"),
  };
}

/** How long a saved blob's URL lives: an immediate revoke can abort a large download in Safari and Firefox. */
const BLOB_URL_LIFETIME_MS = 60_000;

/** Saves a blob as a file: an object URL and a temporary <a download> click; the URL is revoked 60 s later. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), BLOB_URL_LIFETIME_MS);
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
    // A chunk may end between the \r and \n of a CRLF: keep a trailing \r until the next chunk shows which it is.
    buffer = (buffer + value).replace(/\r\n|\r(?!$)/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const message = parseSSEBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (message) onEvent(message);
      boundary = buffer.indexOf("\n\n");
    }
  }
  const tail = parseSSEBlock(buffer.replace(/\r/g, "\n").trim());
  if (tail) onEvent(tail);
}
