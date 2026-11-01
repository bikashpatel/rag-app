import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type AuthDocument = typeof import("./authDocument");

/** The module as a document loaded at `path` sees it (it reads the path once, when the app starts). */
async function loadAt(path: string): Promise<AuthDocument> {
  window.history.replaceState(null, "", path);
  vi.resetModules();
  return import("./authDocument");
}

/** Replaces window.location with one whose navigations are recorded (jsdom can't navigate). */
function fakeLocation(path: string) {
  const url = new URL(path, "http://localhost");
  const location = {
    href: url.href,
    pathname: url.pathname,
    search: url.search,
    hash: url.hash,
    origin: url.origin,
    assign: vi.fn(),
    reload: vi.fn(),
  };
  vi.stubGlobal("location", location);
  return location;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.useRealTimers();
});

// Real User-Agent strings (2026).
const UA = {
  instagram:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/22G86 " +
    "Instagram 401.0.0.27.79 (iPhone15,3; iOS 18_6; en_IN; en; scale=3.00; 1290x2796; 789123456)",
  facebookIos:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/22F76 " +
    "[FBAN/FBIOS;FBAV/518.0.0.52.97;FBBV/731234567;FBDV/iPhone14,7;FBMD/iPhone;FBSN/iOS;FBSV/18.5;FBSS/3;FBCR/;FBID/phone;FBLC/en_US;FBOP/80]",
  facebookAndroid:
    "Mozilla/5.0 (Linux; Android 15; SM-S921B Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) " +
    "Version/4.0 Chrome/140.0.7339.51 Mobile Safari/537.36 [FB_IAB/FB4A;FBAV/519.0.0.47.109;]",
  linkedIn:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/22G86 " +
    "[LinkedInApp]/9.31.1540",
  line:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/22G86 " +
    "Safari Line/15.12.0",
  androidWebView:
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP2A.240805.005; wv) AppleWebKit/537.36 (KHTML, like Gecko) " +
    "Version/4.0 Chrome/140.0.7339.51 Mobile Safari/537.36",
  chromeAndroid:
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
  safariIos:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 " +
    "Mobile/15E148 Safari/604.1",
  chromeMac:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  firefoxWindows: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
};

describe("turnstileAllowed", () => {
  it.each(["/login", "/signup", "/forgot-password"])("is true in a document loaded at %s", async (path) => {
    const { turnstileAllowed, TURNSTILE_PATHS } = await loadAt(path);
    expect(TURNSTILE_PATHS).toEqual(["/login", "/signup", "/forgot-password"]);
    expect(turnstileAllowed()).toBe(true);
  });

  it.each(["/", "/plans", "/verify-email", "/login/", "/signup/google", "/forgot-password-x"])(
    "is false in a document loaded at %s",
    async (path) => {
      const { turnstileAllowed } = await loadAt(path);
      expect(turnstileAllowed()).toBe(false);
    },
  );

  it("keeps the answer of the document's first path when the app navigates", async () => {
    const { turnstileAllowed } = await loadAt("/");
    window.history.pushState(null, "", "/login");
    expect(turnstileAllowed()).toBe(false);
  });
});

describe("reloadForTurnstile", () => {
  it("reloads once for a Turnstile page in a strict document, then gives up", async () => {
    const { reloadForTurnstile } = await loadAt("/");
    const location = fakeLocation("/login");

    expect(reloadForTurnstile()).toBe(true);
    expect(reloadForTurnstile()).toBe(true); // a second widget while the reload is under way
    expect(location.reload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("rag.turnstileReload")).toBe("/login");

    // The reloaded document is strict again (nginx didn't send the wider CSP): no second reload.
    vi.unstubAllGlobals();
    const again = await loadAt("/plans");
    const second = fakeLocation("/login");
    expect(again.reloadForTurnstile()).toBe(false);
    expect(second.reload).not.toHaveBeenCalled();
  });

  it("forgets the reload once a document allows the widget, so a later page may reload again", async () => {
    sessionStorage.setItem("rag.turnstileReload", "/login");
    await loadAt("/login");
    expect(sessionStorage.getItem("rag.turnstileReload")).toBeNull();

    const { reloadForTurnstile } = await loadAt("/");
    const location = fakeLocation("/login");
    expect(reloadForTurnstile()).toBe(true);
    expect(location.reload).toHaveBeenCalledTimes(1);
  });

  it("reloads for another Turnstile page after one for a different page", async () => {
    sessionStorage.setItem("rag.turnstileReload", "/login");
    const { reloadForTurnstile } = await loadAt("/");
    const location = fakeLocation("/signup");
    expect(reloadForTurnstile()).toBe(true);
    expect(location.reload).toHaveBeenCalledTimes(1);
  });

  it("never reloads a page whose path can't get the widget's CSP", async () => {
    const { reloadForTurnstile } = await loadAt("/");
    const location = fakeLocation("/verify-email");
    expect(reloadForTurnstile()).toBe(false);
    expect(location.reload).not.toHaveBeenCalled();
  });

  it("doesn't reload without storage to remember it by (no loop guard)", async () => {
    const { reloadForTurnstile } = await loadAt("/");
    const location = fakeLocation("/login");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    expect(reloadForTurnstile()).toBe(false);
    expect(location.reload).not.toHaveBeenCalled();
  });
});

describe("safeNextPath", () => {
  let safeNext: AuthDocument["safeNextPath"];
  beforeEach(async () => {
    ({ safeNextPath: safeNext } = await loadAt("/"));
  });

  it.each([
    ["/plans", "/plans"],
    ["/c/42?settings=usage#end", "/c/42?settings=usage#end"],
    ["/?settings=account&change=email", "/?settings=account&change=email"],
    ["/", "/"],
  ])("keeps the same-site path %s", (value, expected) => {
    expect(safeNext(value)).toBe(expected);
  });

  it.each([
    "//evil.example/plans",
    "/\\evil.example",
    "/api/auth/logout",
    "https://evil.example/",
    "plans",
    "",
    "/plans\nSet-Cookie: x",
    "/plans\u0000",
    `/${"a".repeat(512)}`,
    null,
    undefined,
    42,
    { pathname: "/plans" },
  ])("turns %j into /", (value) => {
    expect(safeNext(value)).toBe("/");
  });
});

describe("leaveForApp", () => {
  it("loads the next page in full, never an off-site one", async () => {
    const { leaveForApp } = await loadAt("/login");
    const location = fakeLocation("/login");
    leaveForApp("/plans?next=1");
    leaveForApp("//evil.example");
    expect(location.assign.mock.calls).toEqual([["/plans?next=1"], ["/"]]);
  });
});

describe("readFragmentToken", () => {
  it("reads the link's token and removes the fragment, keeping the path, query and router state", async () => {
    const { readFragmentToken } = await loadAt("/");
    window.history.replaceState({ idx: 3 }, "", "/verify-email?from=mail#t=Ab9_-x");
    expect(readFragmentToken()).toBe("Ab9_-x");
    expect(window.location.pathname + window.location.search + window.location.hash).toBe("/verify-email?from=mail");
    expect(window.history.state).toEqual({ idx: 3 });
    expect(readFragmentToken()).toBeNull();
  });

  it("returns null for a fragment without a token, and removes it too", async () => {
    const { readFragmentToken } = await loadAt("/");
    window.history.replaceState(null, "", "/reset-password#x=1");
    expect(readFragmentToken()).toBeNull();
    expect(window.location.hash).toBe("");
    window.history.replaceState(null, "", "/reset-password#t=");
    expect(readFragmentToken()).toBeNull();
  });
});

describe("inAppBrowser", () => {
  it.each([
    ["Instagram", UA.instagram],
    ["Facebook on iOS", UA.facebookIos],
    ["Facebook on Android", UA.facebookAndroid],
    ["LinkedIn", UA.linkedIn],
    ["LINE", UA.line],
    ["an Android WebView", UA.androidWebView],
  ])("spots %s", async (_, userAgent) => {
    const { inAppBrowser } = await loadAt("/");
    expect(inAppBrowser(userAgent)).toBe(true);
  });

  it.each([
    ["Chrome on Android", UA.chromeAndroid],
    ["Safari on iOS", UA.safariIos],
    ["Chrome on a Mac", UA.chromeMac],
    ["Firefox on Windows", UA.firefoxWindows],
    ["a browser naming a timeline", "Mozilla/5.0 TimeLine/2.0"],
  ])("lets %s through", async (_, userAgent) => {
    const { inAppBrowser } = await loadAt("/");
    expect(inAppBrowser(userAgent)).toBe(false);
  });

  it("reads this browser's User-Agent by default", async () => {
    const { inAppBrowser } = await loadAt("/");
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(UA.instagram);
    expect(inAppBrowser()).toBe(true);
  });
});

describe("stash and take", () => {
  it("hands a value over once, in this tab's sessionStorage", async () => {
    const { stash, take } = await loadAt("/");
    stash("email", "maya@example.com", 60_000);
    expect(Object.keys(sessionStorage)).toEqual(["rag.auth.email"]);
    expect(take("email")).toBe("maya@example.com");
    expect(take("email")).toBeNull();
    expect(take("confirm_token")).toBeNull();
  });

  it("drops a value past its time", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-01T10:00:00Z"));
    const { stash, take } = await loadAt("/");
    stash("confirm_token", "Ab9_-x", 600_000);
    stash("email", "maya@example.com", 600_000);
    vi.setSystemTime(new Date("2026-11-01T10:09:59Z"));
    expect(take("confirm_token")).toBe("Ab9_-x");
    vi.setSystemTime(new Date("2026-11-01T10:10:00Z"));
    expect(take("email")).toBeNull();
    expect(sessionStorage.getItem("rag.auth.email")).toBeNull();
  });

  it("treats an unreadable entry as absent and survives storage that throws", async () => {
    const { stash, take } = await loadAt("/");
    sessionStorage.setItem("rag.auth.email", "{not json");
    expect(take("email")).toBeNull();
    sessionStorage.setItem("rag.auth.email", JSON.stringify({ value: 42, expires: Date.now() + 1000 }));
    expect(take("email")).toBeNull();

    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    expect(() => stash("email", "maya@example.com", 1000)).not.toThrow();
    expect(take("email")).toBeNull();
  });
});
