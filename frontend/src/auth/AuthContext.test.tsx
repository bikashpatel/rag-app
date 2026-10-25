import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, onTestFinished } from "vitest";
import { api } from "../api/client";
import type { Me, Preferences, PreferencesUpdate } from "../api/types";
import { onUserChanged } from "../lib/appEvents";
import { makeMe, makePreferences } from "../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall } from "../test/http";
import { PREFERENCES_KEY } from "../theme/appearance";
import { AuthProvider, handleExternalPreferences, useAuth } from "./AuthContext";

const unauthorized = () => errorResponse(401, "Not authenticated");

async function signedIn(routes: Parameters<typeof stubFetch>[0] = {}, me: Me = makeMe()) {
  const calls = stubFetch({ "GET /auth/me": me, ...routes });
  const hook = renderHook(() => useAuth(), { wrapper: AuthProvider });
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  return { ...hook, calls };
}

function patches(calls: FetchCall[]): unknown[] {
  return calls.filter((call) => call.method === "PATCH").map((call) => call.body);
}

describe("AuthProvider: the session", () => {
  it("signs in from the session check", async () => {
    const { result } = await signedIn();
    expect(result.current.user?.username).toBe("maya.chen");
    expect(result.current.bootError).toBeNull();
  });

  it("offers a retry when the session couldn't be checked", async () => {
    let up = false;
    stubFetch({ "GET /auth/me": () => (up ? makeMe() : errorResponse(503, "Starting up")) });
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
    await waitFor(() => expect(result.current.bootError).toBe("Starting up"));
    expect(result.current.user).toBeNull();
    up = true;
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.user).not.toBeNull());
    expect(result.current.bootError).toBeNull();
  });

  it("signs out after a 401 only when the session check agrees", async () => {
    let session = true;
    const { result, calls } = await signedIn({
      "GET /auth/me": () => (session ? makeMe() : unauthorized()),
      "GET /documents": unauthorized,
    });
    await act(async () => {
      await Promise.allSettled([api.get("/documents"), api.get("/documents")]);
    });
    await waitFor(() => expect(calls.filter((call) => call.path === "/auth/me")).toHaveLength(2));
    expect(result.current.user).not.toBeNull();

    session = false;
    await act(async () => {
      await api.get("/documents").catch(() => undefined);
    });
    await waitFor(() => expect(result.current.user).toBeNull());
  });

  it("stays signed in when the check after a 401 can't reach the server", async () => {
    let reachable = true;
    const { result, calls } = await signedIn({
      "GET /auth/me": () => (reachable ? makeMe() : errorResponse(502, "Bad gateway")),
      "GET /documents": unauthorized,
    });
    reachable = false;
    await act(async () => {
      await api.get("/documents").catch(() => undefined);
    });
    await waitFor(() => expect(calls.filter((call) => call.path === "/auth/me")).toHaveLength(2));
    expect(result.current.user).not.toBeNull();
  });

  it("asks twice before treating a refresh's 401 as signed out", async () => {
    let answers: (Response | Me)[] = [];
    const { result } = await signedIn({ "GET /auth/me": () => answers.shift() ?? makeMe() });
    expect(result.current.user).not.toBeNull();
    answers = [unauthorized(), makeMe({ usage: { ...makeMe().usage, chat_tokens: 130_000 } })];
    await act(() => result.current.refresh());
    expect(result.current.user?.usage.chat_tokens).toBe(130_000);

    answers = [unauthorized(), unauthorized()];
    await act(() => result.current.refresh());
    expect(result.current.user).toBeNull();
  });

  it("logs out, and stays signed in when the server couldn't clear the cookie", async () => {
    let fail = true;
    const { result } = await signedIn({
      "POST /auth/logout": () => (fail ? errorResponse(503, "Unavailable") : new Response(null, { status: 204 })),
    });
    await act(() => expect(result.current.logout()).rejects.toMatchObject({ status: 503 }));
    expect(result.current.user).not.toBeNull();
    fail = false;
    await act(() => result.current.logout());
    expect(result.current.user).toBeNull();
    expect(result.current.signedOut).toBe(true);
  });

  it("tells the per-user stores each time the signed-in user changes (U30)", async () => {
    const seen: (number | null)[] = [];
    onTestFinished(onUserChanged((id) => seen.push(id)));
    const { result } = await signedIn({
      "POST /auth/logout": () => new Response(null, { status: 204 }),
      "POST /auth/login": makeMe({ id: 9, username: "sam.ortiz" }),
    });
    await act(() => result.current.logout());
    await act(() => result.current.login("sam.ortiz", "correct horse"));
    expect(result.current.user?.id).toBe(9);
    expect(seen).toEqual([7, null, 9]);
  });

  it("requires the provider", () => {
    expect(() => renderHook(() => useAuth())).toThrow("useAuth must be used inside <AuthProvider>");
  });
});

describe("AuthProvider: preferences", () => {
  it("applies a change at once and saves it", async () => {
    const { result, calls } = await signedIn({
      "PATCH /me/preferences": ({ body }) => ({ ...makePreferences(), ...(body as PreferencesUpdate) }),
    });
    let saved: Preferences | undefined;
    await act(async () => {
      saved = await result.current.updatePreferences({ theme: "dark", accent: undefined });
    });
    expect(saved?.theme).toBe("dark");
    expect(result.current.preferences.theme).toBe("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(JSON.parse(localStorage.getItem(PREFERENCES_KEY) ?? "{}")).toMatchObject({ theme: "dark" });
    expect(patches(calls)).toEqual([{ theme: "dark" }]);
  });

  it("keeps one save in flight and merges the changes made meanwhile", async () => {
    const first = deferred<Preferences>();
    let request = 0;
    const { result, calls } = await signedIn({
      "PATCH /me/preferences": ({ body }) =>
        ++request === 1 ? first.promise : { ...makePreferences({ theme: "dark" }), ...(body as PreferencesUpdate) },
    });
    let pending: Promise<Preferences>[] = [];
    act(() => {
      pending = [
        result.current.updatePreferences({ theme: "dark" }),
        result.current.updatePreferences({ accent: "ocean" }),
        result.current.updatePreferences({ chat_font_size: "large" }),
      ];
    });
    expect(result.current.preferences).toMatchObject({ theme: "dark", accent: "ocean", chat_font_size: "large" });
    expect(patches(calls)).toEqual([{ theme: "dark" }]);

    await act(async () => {
      first.resolve(makePreferences({ theme: "dark" }));
      await Promise.all(pending);
    });
    expect(patches(calls)).toEqual([{ theme: "dark" }, { accent: "ocean", chat_font_size: "large" }]);
    expect(result.current.preferences).toMatchObject({ theme: "dark", accent: "ocean", chat_font_size: "large" });
  });

  it("reverts a refused save, keeping a change made again since", async () => {
    const first = deferred<Response>();
    let request = 0;
    const { result } = await signedIn({
      "PATCH /me/preferences": ({ body }) =>
        ++request === 1 ? first.promise : { ...makePreferences(), ...(body as PreferencesUpdate) },
    });
    let refused: Promise<unknown> = Promise.resolve();
    let later: Promise<Preferences> = Promise.resolve(makePreferences());
    act(() => {
      refused = result.current.updatePreferences({ theme: "dark", accent: "plum" }).catch((err: unknown) => err);
      later = result.current.updatePreferences({ accent: "sage" });
    });
    await act(async () => {
      first.resolve(errorResponse(422, "Invalid accent"));
      await later;
    });
    await expect(refused).resolves.toMatchObject({ status: 422, message: "Invalid accent" });
    expect(result.current.preferences).toMatchObject({ theme: "system", accent: "sage" });
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });

  it("forgets unsaved changes when the user signs out", async () => {
    const first = deferred<Preferences>();
    const { result } = await signedIn({
      "PATCH /me/preferences": () => first.promise,
      "POST /auth/logout": () => new Response(null, { status: 204 }),
    });
    let queued: Promise<unknown> = Promise.resolve();
    act(() => {
      void result.current.updatePreferences({ theme: "dark" });
      queued = result.current.updatePreferences({ accent: "ocean" }).catch((err: unknown) => err);
    });
    await act(() => result.current.logout());
    await expect(queued).resolves.toMatchObject({
      status: 401,
      message: "You were signed out before this setting was saved.",
    });
  });

  it("applies a change locally while signed out", async () => {
    const calls = stubFetch({ "GET /auth/me": unauthorized });
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => result.current.updatePreferences({ theme: "dark" }));
    expect(result.current.preferences.theme).toBe("dark");
    expect(patches(calls)).toEqual([]);
  });

  it("takes another tab's preferences, with this tab's pending change on top", async () => {
    const pending = deferred<Preferences>();
    const { result } = await signedIn({ "PATCH /me/preferences": () => pending.promise });
    act(() => {
      void result.current.updatePreferences({ accent: "plum" });
    });
    act(() => handleExternalPreferences(makePreferences({ theme: "dark", chat_font_size: "small" })));
    expect(result.current.preferences).toMatchObject({ theme: "dark", chat_font_size: "small", accent: "plum" });
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("applies another tab's preferences directly before React has mounted", () => {
    handleExternalPreferences(makePreferences({ theme: "dark", accent: "slate" }));
    expect(document.documentElement).toHaveAttribute("data-accent", "slate");
  });
});
