import type { Location } from "react-router-dom";
import { safeNextPath } from "../lib/authDocument";

/** Router state carried to /login so signing in returns to the page that was asked for. */
export type LoginRedirectState = { from: { pathname: string; search: string; hash: string } };

/** The signed-out pages: never a place to return to after signing in. */
const SIGNED_OUT_PATHS: ReadonlySet<string> = new Set(["/login", "/signup", "/forgot-password"]);

export function loginRedirectState({ pathname, search, hash }: Location): LoginRedirectState {
  return { from: { pathname, search, hash } };
}

/**
 * Where to go after signing in: the page from the redirect state when it's an in-app path, else `?next=` of the sign-in
 * URL (a link from another page, such as /login?next=/confirm-email), else the chat.
 */
export function postLoginPath(state: unknown, search = ""): string {
  const from = (state as Partial<LoginRedirectState> | null)?.from;
  const pathname = from?.pathname;
  if (typeof pathname === "string" && pathname.startsWith("/") && !pathname.startsWith("//")) {
    if (SIGNED_OUT_PATHS.has(pathname)) return "/";
    const query = typeof from?.search === "string" ? from.search : "";
    const hash = typeof from?.hash === "string" ? from.hash : "";
    return pathname + query + hash;
  }
  const next = safeNextPath(new URLSearchParams(search).get("next"));
  return SIGNED_OUT_PATHS.has(new URL(next, window.location.origin).pathname) ? "/" : next;
}
