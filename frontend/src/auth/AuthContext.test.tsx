import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { api } from "../api/client";
import type { Me, Preferences, PreferencesUpdate } from "../api/types";
import { onUserChanged } from "../lib/appEvents";
import { makeAdminMe, makeLapsedPlanAccess, makeMe, makePaidPlanAccess, makePreferences } from "../test/fixtures";
import { deferred, errorResponse, jsonResponse, stubFetch, type FetchCall } from "../test/http";
import { PREFERENCES_KEY } from "../theme/appearance";
import { makeAuthConfig } from "../test/auth";
import {
  AuthProvider,
  forgetAuthConfig,
  handleExternalPreferences,
  loadAuthConfig,
  useAuth,
  useAuthConfig,
} from "./AuthContext";

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
    let me: Me | Response = makeMe();
    const { result } = await signedIn({
      "GET /auth/me": () => me,
      "POST /auth/logout": () => new Response(null, { status: 204 }),
    });
    await act(() => result.current.logout());
    me = makeMe({ id: 9, username: "sam.ortiz" });
    await act(() => result.current.refresh());
    expect(result.current.user?.id).toBe(9);
    expect(seen).toEqual([7, null, 9]);
  });

  it("signs in with the identifier (and the robot check's token) and leaves this document's state alone", async () => {
    const sam = makeMe({ id: 9, username: "sam.ortiz", email: "sam@example.com" });
    const calls = stubFetch({ "GET /auth/me": unauthorized, "POST /auth/login": sam });
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
    await waitFor(() => expect(result.current.status).toBe("signed-out"));

    await act(async () => {
      await expect(result.current.login("sam@example.com", "correct horse")).resolves.toEqual(sam);
      await result.current.login("sam@example.com", "correct horse", "TOKEN");
    });
    expect(calls.filter((call) => call.path === "/auth/login").map((call) => call.body)).toEqual([
      { identifier: "sam@example.com", password: "correct horse" },
      { identifier: "sam@example.com", password: "correct horse", turnstile_token: "TOKEN" },
    ]);
    // The page leaves with a full page load: the next document's session check signs in.
    expect(result.current.user).toBeNull();
  });

  it("says where the session check stands", async () => {
    const me = deferred<Me | Response>();
    stubFetch({ "GET /auth/me": () => me.promise });
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider });
    expect(result.current.status).toBe("loading");
    await act(async () => me.resolve(errorResponse(503, "Starting up")));
    await waitFor(() => expect(result.current.status).toBe("unavailable"));
  });

  it("requires the provider", () => {
    expect(() => renderHook(() => useAuth())).toThrow("useAuth must be used inside <AuthProvider>");
  });
});

describe("AuthProvider: plans", () => {
  const DAY_MS = 86_400_000;
  const planExpired = () =>
    jsonResponse({ detail: "Your Standard plan ended on 7 Nov 2026.", code: "plan_expired", plan: "Standard" }, { status: 402 });
  const meCalls = (calls: FetchCall[]) => calls.filter((call) => call.path === "/auth/me");

  afterEach(() => {
    vi.useRealTimers();
  });

  it("refreshes Me once for 402s that say the plan ended", async () => {
    let answer: Promise<Me> | Me = makeMe({ plan: makePaidPlanAccess() });
    const { result, calls } = await signedIn({
      "GET /auth/me": () => answer,
      "GET /models": planExpired,
      "GET /documents/config": planExpired,
    });
    const lapsed = deferred<Me>();
    answer = lapsed.promise;
    await act(async () => {
      await Promise.allSettled([api.get("/models"), api.get("/documents/config")]);
      await api.get("/models").catch(() => undefined);
    });
    expect(meCalls(calls)).toHaveLength(2);
    await act(async () => lapsed.resolve(makeMe({ plan: makeLapsedPlanAccess() })));
    await waitFor(() => expect(result.current.user?.plan.state).toBe("expired"));

    answer = makeMe({ plan: makeLapsedPlanAccess() });
    await act(() => api.get("/models").catch(() => undefined));
    await waitFor(() => expect(meCalls(calls)).toHaveLength(3));
    expect(result.current.user).not.toBeNull();
  });

  it("leaves a feature outside the plan to the caller, and ignores 402s once signed out", async () => {
    const { result, calls } = await signedIn({
      "POST /plugins/gmail/connect": () =>
        jsonResponse({ detail: "Not in your plan.", code: "feature_not_in_plan", feature: "plugin:gmail" }, { status: 402 }),
      "POST /auth/logout": () => new Response(null, { status: 204 }),
      "GET /models": planExpired,
    });
    await act(() => api.post("/plugins/gmail/connect").catch(() => undefined));
    await act(() => result.current.logout());
    await act(() => api.get("/models").catch(() => undefined));
    expect(meCalls(calls)).toHaveLength(1);
  });

  /** Signed in with setTimeout and Date faked from the start, so the provider's timer is a fake one (no waitFor). */
  async function signedInAt(now: string, me: () => Me) {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], now: new Date(now) });
    const calls = stubFetch({ "GET /auth/me": me });
    const hook = renderHook(() => useAuth(), { wrapper: AuthProvider });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(hook.result.current.user).not.toBeNull();
    return { ...hook, calls };
  }

  it("asks again when the paid time ends in an open tab", async () => {
    let me = makeMe({ plan: makePaidPlanAccess() }); // paid through 7 Nov 2026, 18:30 UTC
    const { result, calls } = await signedInAt("2026-11-07T18:25:00Z", () => me);
    me = makeMe({ plan: makeLapsedPlanAccess("expired", { ended_at: "2026-11-07T18:30:00Z" }) });
    await act(() => vi.advanceTimersByTimeAsync(5 * 60_000));
    expect(meCalls(calls)).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(meCalls(calls)).toHaveLength(2);
    expect(result.current.user?.plan.access).toBe(false);

    await act(() => vi.advanceTimersByTimeAsync(30 * DAY_MS));
    expect(meCalls(calls)).toHaveLength(2);
  });

  it("checks a far end again after 24 days, and every minute while this clock runs ahead of the server's", async () => {
    const me = makeMe({ plan: makePaidPlanAccess({ paid_through: "2026-11-10T00:00:00Z" }) });
    const { calls } = await signedInAt("2026-10-01T00:00:00Z", () => me);
    await act(() => vi.advanceTimersByTimeAsync(24 * DAY_MS));
    expect(meCalls(calls)).toHaveLength(2);
    await act(() => vi.advanceTimersByTimeAsync(16 * DAY_MS + 1_000));
    expect(meCalls(calls)).toHaveLength(3);
    // The server still says active after the paid time: ask again a minute later.
    await act(() => vi.advanceTimersByTimeAsync(59_000));
    expect(meCalls(calls)).toHaveLength(3);
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(meCalls(calls)).toHaveLength(4);
  });

  it("sets no timer for an open-ended plan or the admin", async () => {
    for (const me of [makeMe(), makeAdminMe()]) {
      const { calls, unmount } = await signedInAt("2026-10-01T00:00:00Z", () => me);
      await act(() => vi.advanceTimersByTimeAsync(60 * DAY_MS));
      expect(meCalls(calls)).toHaveLength(1);
      unmount();
    }
  });
});

describe("AuthProvider: preferences", () => {
  it("carries the two email switches of Plan & billing, saving them like the rest", async () => {
    const { result, calls } = await signedIn(
      {
        "PATCH /me/preferences": ({ body }) => ({
          ...makePreferences({ email_receipts: false, email_plan_reminders: true }),
          ...(body as PreferencesUpdate),
        }),
      },
      makeMe({ preferences: makePreferences({ email_receipts: false, email_plan_reminders: true }) }),
    );
    expect(result.current.preferences).toMatchObject({ email_receipts: false, email_plan_reminders: true });

    await act(async () => {
      await result.current.updatePreferences({ email_plan_reminders: false });
    });
    expect(patches(calls)).toEqual([{ email_plan_reminders: false }]);
    expect(result.current.preferences).toMatchObject({ email_receipts: false, email_plan_reminders: false });

    // Another tab's cache carries the switches too.
    act(() => handleExternalPreferences(makePreferences({ theme: "dark", email_receipts: true })));
    expect(result.current.preferences).toMatchObject({ theme: "dark", email_receipts: true, email_plan_reminders: true });
  });

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

describe("useAuthConfig", () => {
  afterEach(() => forgetAuthConfig());

  it("loads GET /auth/config once per document and shares it", async () => {
    forgetAuthConfig();
    const calls = stubFetch({ "GET /auth/config": makeAuthConfig() });
    const first = renderHook(() => useAuthConfig());
    await waitFor(() => expect(first.result.current.config).toEqual(makeAuthConfig()));
    const second = renderHook(() => useAuthConfig());
    expect(second.result.current.config).toEqual(makeAuthConfig()); // at once, from the shared answer
    await expect(loadAuthConfig()).resolves.toEqual(makeAuthConfig());
    expect(calls.filter((call) => call.path === "/auth/config")).toHaveLength(1);
  });

  it("says when it couldn't load, and asks again on retry", async () => {
    forgetAuthConfig();
    let up = false;
    stubFetch({ "GET /auth/config": () => (up ? makeAuthConfig() : errorResponse(502, "Bad gateway")) });
    const { result } = renderHook(() => useAuthConfig());
    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(result.current.config).toBeNull();
    up = true;
    act(() => result.current.retry());
    expect(result.current.failed).toBe(false);
    await waitFor(() => expect(result.current.config).toEqual(makeAuthConfig()));
  });
});
