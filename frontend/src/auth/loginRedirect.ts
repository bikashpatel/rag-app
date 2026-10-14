import type { Location } from "react-router-dom";

/** Router state carried to /login so signing in returns to the page that was asked for. */
export type LoginRedirectState = { from: { pathname: string; search: string; hash: string } };

export function loginRedirectState({ pathname, search, hash }: Location): LoginRedirectState {
  return { from: { pathname, search, hash } };
}

/** Where to go after signing in: the page from the redirect state when it's an in-app path, else the chat. */
export function postLoginPath(state: unknown): string {
  const from = (state as Partial<LoginRedirectState> | null)?.from;
  const pathname = from?.pathname;
  if (typeof pathname !== "string" || !pathname.startsWith("/") || pathname.startsWith("//") || pathname === "/login") {
    return "/";
  }
  const search = typeof from?.search === "string" ? from.search : "";
  const hash = typeof from?.hash === "string" ? from.hash : "";
  return pathname + search + hash;
}
