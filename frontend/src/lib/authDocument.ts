// The signed-out pages and their documents (docs/briefs/signup-auth.md 7.4). nginx gives only the documents loaded at
// TURNSTILE_PATHS a CSP that allows Cloudflare's Turnstile; every other document keeps the strict one. So the widget
// runs only in a document loaded at one of those paths, a page that needs it elsewhere reloads itself once, and
// leaving an auth page for the app is a full page load (the app's document never allows third-party script).

export const TURNSTILE_PATHS = ["/login", "/signup", "/forgot-password"] as const;

const RELOAD_KEY = "rag.turnstileReload";
const STASH_PREFIX = "rag.auth.";
const MAX_NEXT_LENGTH = 512;
const IN_APP_BROWSER = /FBAN|FBAV|Instagram|LinkedInApp|\bLine\/|; wv\)/;
const UNSAFE_PATH_CHARACTERS = /[\u0000-\u001f\u007f\\]/;

const isTurnstilePath = (path: string) => (TURNSTILE_PATHS as readonly string[]).includes(path);

/** Read once, when the app starts: the CSP belongs to the document, not to the route shown now. */
const documentAllowsTurnstile = isTurnstilePath(window.location.pathname);

let reloading = false;

function readSession(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string): boolean {
  try {
    sessionStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function removeSession(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* storage unavailable */
  }
}

// A reload that landed in a document allowing the widget has done its job: a later one in this tab may happen again.
if (documentAllowsTurnstile) removeSession(RELOAD_KEY);

/** True when this document was loaded at one of TURNSTILE_PATHS (its CSP allows Cloudflare's script). */
export function turnstileAllowed(): boolean {
  return documentAllowsTurnstile;
}

/**
 * Reloads the page once so it arrives with the CSP of its path. True when it reloads; false when it already did for
 * this path (or can't remember doing so, or the path isn't one of TURNSTILE_PATHS): the page then shows the widget's
 * "couldn't load" state instead of looping.
 */
export function reloadForTurnstile(): boolean {
  if (reloading) return true;
  const path = window.location.pathname;
  if (!isTurnstilePath(path) || readSession(RELOAD_KEY) === path || !writeSession(RELOAD_KEY, path)) return false;
  reloading = true;
  window.location.reload();
  return true;
}

/** `value` if it is a path on this site (not `//…` or under `/api/`, no backslash or control character), else "/". */
export function safeNextPath(value: unknown): string {
  if (typeof value !== "string" || value.length > MAX_NEXT_LENGTH) return "/";
  const onSite = value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/api/");
  return onSite && !UNSAFE_PATH_CHARACTERS.test(value) ? value : "/";
}

/** A full page load into the app, so the next document gets the app's strict CSP. */
export function leaveForApp(path: string): void {
  window.location.assign(safeNextPath(path));
}

/** The token of an emailed link (`#t=…`); the fragment leaves the address bar either way. */
export function readFragmentToken(): string | null {
  const { hash, pathname, search } = window.location;
  if (!hash) return null;
  const token = new URLSearchParams(hash.slice(1)).get("t");
  window.history.replaceState(window.history.state, "", pathname + search);
  return token || null;
}

/** Instagram, Facebook, LinkedIn, LINE and Android WebViews: Google refuses to sign in inside them. */
export function inAppBrowser(userAgent: string = navigator.userAgent): boolean {
  return IN_APP_BROWSER.test(userAgent);
}

type Stashed = { value: string; expires: number };

/** Keeps `value` in this tab's sessionStorage for `ttlMs` (an address carried to another page, a waiting token). */
export function stash(key: "email" | "confirm_token", value: string, ttlMs: number): void {
  const entry: Stashed = { value, expires: Date.now() + ttlMs };
  writeSession(STASH_PREFIX + key, JSON.stringify(entry));
}

/** The stashed value, removed as it is read; null when absent, expired or unreadable. */
export function take(key: "email" | "confirm_token"): string | null {
  const raw = readSession(STASH_PREFIX + key);
  if (raw === null) return null;
  removeSession(STASH_PREFIX + key);
  try {
    const entry = JSON.parse(raw) as Partial<Stashed> | null;
    const fresh = typeof entry?.expires === "number" && entry.expires > Date.now();
    return fresh && typeof entry.value === "string" ? entry.value : null;
  } catch {
    return null;
  }
}
