import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeBillingStatus, makeCatalog, makeOrder, makePaymentView, makePublicPlan, makePublicSite } from "../test/billing";
import { deferred, errorResponse, jsonResponse, streamResponse, stubFetch, type FetchCall } from "../test/http";
import { makeMe } from "../test/fixtures";
import {
  api,
  ApiError,
  apiErrorFromBody,
  billingApi,
  changePassword,
  documentDownloadUrl,
  documentImageUrl,
  downloadMyData,
  errorMessageFromBody,
  getSessionUser,
  isPlanRequired,
  notifyPlanRequired,
  onPluginProblem,
  postSSE,
  publicApi,
  receiptUrl,
  saveBlob,
  searchGmailThreads,
  setPlanRequiredHandler,
  setUnauthorizedHandler,
  updatePluginConnection,
  type SSEMessage,
} from "./client";
import type { ChatStreamEvent, GmailThreadPage } from "./types";

const onUnauthorized = vi.fn();
const onPlanRequired = vi.fn();

beforeEach(() => {
  setUnauthorizedHandler(onUnauthorized);
  setPlanRequiredHandler(onPlanRequired);
});

afterEach(() => {
  setUnauthorizedHandler(null);
  setPlanRequiredHandler(null);
  onUnauthorized.mockReset();
  onPlanRequired.mockReset();
  vi.useRealTimers();
});

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => {
      throw new Error("Expected the promise to reject");
    },
    (err: unknown) => err,
  );
}

describe("errorMessageFromBody", () => {
  it("uses a string detail as it is", () => {
    expect(errorMessageFromBody(400, { detail: "That username is taken." })).toBe("That username is taken.");
  });

  it("joins validation errors with their field paths, without the body prefix", () => {
    const body = {
      detail: [
        { loc: ["body", "password"], msg: "String should have at least 8 characters" },
        { loc: ["body", "limits", "max_upload_mb"], msg: "Input should be greater than 0" },
        { msg: "Value error" },
        {},
      ],
    };
    expect(errorMessageFromBody(422, body)).toBe(
      "password: String should have at least 8 characters; limits.max_upload_mb: Input should be greater than 0; Value error; invalid",
    );
  });

  it.each([
    [413, null, "The file is too large."],
    [502, "<html>Bad gateway</html>", "The server ran into a problem. Please try again."],
    [500, { detail: "" }, "The server ran into a problem. Please try again."],
    [404, { detail: [] }, "Request failed (404)"],
    [429, { message: "slow down" }, "Request failed (429)"],
  ])("falls back by status %d", (status, body, expected) => {
    expect(errorMessageFromBody(status, body)).toBe(expected);
  });
});

describe("api requests", () => {
  it("sends JSON with same-origin credentials and parses the response", async () => {
    const calls = stubFetch({ "PATCH /me/preferences": ({ body }) => ({ ...makeMe().preferences, ...(body as object) }) });
    const result = await api.patch("/me/preferences", { theme: "dark" });
    expect(result).toMatchObject({ theme: "dark", accent: "clay" });
    expect(calls).toMatchObject([{ method: "PATCH", path: "/me/preferences", body: { theme: "dark" } }]);
    const init = vi.mocked(fetch).mock.calls[0][1];
    expect(init).toMatchObject({ credentials: "same-origin", headers: { "Content-Type": "application/json" } });
  });

  it("posts an empty JSON object when there is no body, and sends forms as they are", async () => {
    const calls = stubFetch({ "POST /auth/logout": () => new Response(null, { status: 204 }), "POST /upload": {} });
    await expect(api.post("/auth/logout")).resolves.toBeUndefined();
    const form = new FormData();
    form.append("scope", "private");
    await api.post("/upload", form);
    expect(calls[0].body).toEqual({});
    expect(calls[1].body).toBe(form);
    expect(vi.mocked(fetch).mock.calls[1][1]?.headers).toBeUndefined();
  });

  it("treats an empty 200 body as undefined", async () => {
    stubFetch({ "DELETE /documents/5": () => new Response("", { status: 200 }) });
    await expect(api.del("/documents/5")).resolves.toBeUndefined();
  });

  it("rejects with the server's message and status", async () => {
    stubFetch({ "GET /models": () => errorResponse(403, "Model not allowed.") });
    const err = await rejection(api.get("/models"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 403, message: "Model not allowed." });
  });

  it("explains a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.get("/models")).rejects.toMatchObject({
      status: 0,
      message: "Cannot reach the server. Check that the app is running.",
    });
  });

  it("passes an abort through", async () => {
    stubFetch({ "GET /models": () => deferred<object>().promise });
    const controller = new AbortController();
    const pending = api.get("/models", { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("reports a 401 as an ended session, except for the login and session check", async () => {
    stubFetch({
      "GET /documents": () => errorResponse(401, "Not authenticated"),
      "POST /auth/login": () => errorResponse(401, "Wrong username or password."),
      "GET /auth/me": () => errorResponse(401, "Not authenticated"),
    });
    await expect(api.post("/auth/login", { username: "maya", password: "nope" })).rejects.toMatchObject({ status: 401 });
    await expect(api.get("/auth/me?fresh=1")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).not.toHaveBeenCalled();
    await expect(api.get("/documents")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("builds document URLs", () => {
    expect(documentDownloadUrl(12)).toBe("/api/documents/12/download");
    expect(documentImageUrl(12)).toBe("/api/documents/12/image?size=full");
    expect(documentImageUrl(12, "thumb")).toBe("/api/documents/12/image?size=thumb");
  });
});

describe("error codes and the plan handler", () => {
  const planError = (code: string, extra: object = {}) =>
    jsonResponse({ detail: "Choose a plan to start using the assistant.", code, ...extra }, { status: 402 });

  it("reads code, feature and plan from the error body", async () => {
    stubFetch({
      "POST /plugins/gmail/connect": () =>
        planError("feature_not_in_plan", {
          detail: "Your Starter plan doesn't include Gmail.",
          feature: "plugin:gmail",
          plan: "Professional",
        }),
      "GET /models": () => errorResponse(403, "You are not allowed to use this model."),
    });
    expect(await rejection(api.post("/plugins/gmail/connect"))).toMatchObject({
      status: 402,
      message: "Your Starter plan doesn't include Gmail.",
      code: "feature_not_in_plan",
      feature: "plugin:gmail",
      plan: "Professional",
    });
    expect(await rejection(api.get("/models"))).toMatchObject({ status: 403, code: null, feature: null, plan: null });
  });

  it("ignores fields that aren't strings", () => {
    const error = apiErrorFromBody(429, { detail: "Slow down.", code: 7, feature: "", plan: null });
    expect(error).toMatchObject({ status: 429, message: "Slow down.", code: null, feature: null, plan: null });
    expect(apiErrorFromBody(500, "<html>")).toMatchObject({
      code: null,
      message: "The server ran into a problem. Please try again.",
    });
  });

  it("calls the plan handler for a 402 without a plan, wherever it comes from", async () => {
    stubFetch({
      "GET /models": () => planError("plan_required"),
      "POST /chat/stream": () => planError("plan_expired", { plan: "Standard" }),
      "POST /me/export": () => planError("plan_expired"),
    });
    await expect(api.get("/models")).rejects.toMatchObject({ status: 402, code: "plan_required" });
    await expect(postSSE("/chat/stream", {}, () => {})).rejects.toMatchObject({ code: "plan_expired", plan: "Standard" });
    await expect(downloadMyData()).rejects.toMatchObject({ code: "plan_expired" });
    expect(onPlanRequired).toHaveBeenCalledTimes(3);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("leaves a feature outside the plan and other refusals to the caller", async () => {
    stubFetch({
      "POST /chat/stream": () => planError("feature_not_in_plan", { feature: "model" }),
      "GET /models": () => jsonResponse({ detail: "Payment required" }, { status: 402 }),
      "POST /billing/checkout": () => jsonResponse({ detail: "Too many.", code: "too_many_checkouts" }, { status: 429 }),
    });
    await expect(postSSE("/chat/stream", {}, () => {})).rejects.toMatchObject({ code: "feature_not_in_plan" });
    await expect(api.get("/models")).rejects.toMatchObject({ status: 402, code: null });
    await expect(api.post("/billing/checkout", {})).rejects.toMatchObject({ code: "too_many_checkouts" });
    expect(onPlanRequired).not.toHaveBeenCalled();
  });

  it("tells a 402 without a plan apart, and lets callers outside `request` report one", () => {
    expect(isPlanRequired(new ApiError(402, "x", { code: "plan_required" }))).toBe(true);
    expect(isPlanRequired(new ApiError(402, "x", { code: "plan_expired" }))).toBe(true);
    expect(isPlanRequired(new ApiError(402, "x", { code: "feature_not_in_plan" }))).toBe(false);
    expect(isPlanRequired(new ApiError(403, "x", { code: "plan_required" }))).toBe(false);
    expect(isPlanRequired(new ApiError(402, "x"))).toBe(false);
    expect(isPlanRequired(new Error("x"))).toBe(false);
    notifyPlanRequired();
    expect(onPlanRequired).toHaveBeenCalledTimes(1);
    setPlanRequiredHandler(null);
    expect(() => notifyPlanRequired()).not.toThrow();
  });
});

describe("billingApi", () => {
  it("reads the catalog, status, payments and profile", async () => {
    const catalog = makeCatalog();
    const status = makeBillingStatus();
    const calls = stubFetch({
      "GET /billing/plans": catalog,
      "GET /billing/status": status,
      "GET /billing/payments": [makePaymentView()],
      "GET /billing/profile": { name: "Maya Chen", email: null },
    });
    await expect(billingApi.plans()).resolves.toEqual(catalog);
    await expect(billingApi.status()).resolves.toEqual(status);
    await expect(billingApi.payments()).resolves.toEqual([makePaymentView()]);
    await expect(billingApi.profile()).resolves.toEqual({ name: "Maya Chen", email: null });
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /billing/plans",
      "GET /billing/status",
      "GET /billing/payments",
      "GET /billing/profile",
    ]);
  });

  it("starts a checkout with the consent, and saves the profile with PUT", async () => {
    const started = { order: makeOrder(), pay_url: "https://rzp.io/rzp/q3Zx8Lm" };
    const calls = stubFetch({
      "POST /billing/checkout": () => jsonResponse(started, { status: 201 }),
      "PUT /billing/profile": ({ body }) => body as object,
    });
    await expect(billingApi.checkout({ plan_id: 2, kind: "period", accept_terms: true })).resolves.toEqual(started);
    await expect(billingApi.saveProfile({ name: "Maya", email: "maya@example.com" })).resolves.toEqual({
      name: "Maya",
      email: "maya@example.com",
    });
    expect(calls).toMatchObject([
      { method: "POST", path: "/billing/checkout", body: { plan_id: 2, kind: "period", accept_terms: true } },
      { method: "PUT", path: "/billing/profile", body: { name: "Maya", email: "maya@example.com" } },
    ]);
  });

  it("reads an order, asking Razorpay first on request, and cancels it", async () => {
    const calls = stubFetch({
      "GET /billing/orders/q3Zx8LmP0aB2cD4eF6gH": makeOrder(),
      "GET /billing/orders/q3Zx8LmP0aB2cD4eF6gH?refresh=true": makeOrder({ status: "paid" }),
      "POST /billing/orders/q3Zx8LmP0aB2cD4eF6gH/cancel": makeOrder({ status: "cancelled" }),
      "GET /billing/orders/a%2Fb": makeOrder(),
    });
    await expect(billingApi.order("q3Zx8LmP0aB2cD4eF6gH")).resolves.toMatchObject({ status: "pending" });
    await expect(billingApi.order("q3Zx8LmP0aB2cD4eF6gH", true)).resolves.toMatchObject({ status: "paid" });
    await expect(billingApi.cancel("q3Zx8LmP0aB2cD4eF6gH")).resolves.toMatchObject({ status: "cancelled" });
    await billingApi.order("a/b");
    expect(calls.map((call) => call.path)).toEqual([
      "/billing/orders/q3Zx8LmP0aB2cD4eF6gH",
      "/billing/orders/q3Zx8LmP0aB2cD4eF6gH?refresh=true",
      "/billing/orders/q3Zx8LmP0aB2cD4eF6gH/cancel",
      "/billing/orders/a%2Fb",
    ]);
  });

  it("links receipts", () => {
    expect(receiptUrl(41)).toBe("/api/billing/receipts/41");
  });
});

describe("publicApi", () => {
  it("reads the site and the plans without a session", async () => {
    stubFetch({ "GET /public/site": makePublicSite(), "GET /public/plans": [makePublicPlan()] });
    await expect(publicApi.site()).resolves.toEqual(makePublicSite());
    await expect(publicApi.plans()).resolves.toEqual([makePublicPlan()]);
  });

  it("fetches a policy page's Markdown from /legal", async () => {
    const calls = stubFetch({
      "GET /legal/terms.md": () => new Response("<!-- draft -->\n# Terms of service", { headers: { "Content-Type": "text/markdown" } }),
    });
    await expect(publicApi.page("terms")).resolves.toBe("<!-- draft -->\n# Terms of service");
    expect(calls[0].path).toBe("/legal/terms.md");
  });

  it("treats a missing page, or the app's HTML in its place, as not found", async () => {
    stubFetch({
      "GET /legal/about.md": () => new Response("Not found", { status: 404 }),
      "GET /legal/shipping.md": () => new Response("<!doctype html>", { headers: { "Content-Type": "text/html; charset=utf-8" } }),
      "GET /legal/privacy.md": () => new Response("", { status: 503 }),
    });
    await expect(publicApi.page("about")).rejects.toMatchObject({ status: 404, message: "This page doesn't exist." });
    await expect(publicApi.page("shipping")).rejects.toMatchObject({ status: 404 });
    await expect(publicApi.page("privacy")).rejects.toMatchObject({
      status: 503,
      message: "The server ran into a problem. Please try again.",
    });
  });

  it("explains a network failure and passes an abort through", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(publicApi.page("terms")).rejects.toMatchObject({ status: 0 });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError")));
    await expect(publicApi.page("terms")).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("getSessionUser", () => {
  it("returns the user, or null on 401 without reporting it", async () => {
    const me = makeMe();
    stubFetch({ "GET /auth/me": me });
    await expect(getSessionUser()).resolves.toEqual(me);
    stubFetch({ "GET /auth/me": () => errorResponse(401, "Not authenticated") });
    await expect(getSessionUser()).resolves.toBeNull();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("rejects on other failures", async () => {
    stubFetch({ "GET /auth/me": () => errorResponse(503, "Starting up") });
    await expect(getSessionUser()).rejects.toMatchObject({ status: 503, message: "Starting up" });
  });

  it("waits for a password change in flight before asking", async () => {
    const reissue = deferred<Response>();
    const calls = stubFetch({ "POST /auth/change-password": () => reissue.promise, "GET /auth/me": makeMe() });
    const change = changePassword("old-password", "new-password-123");
    const check = getSessionUser();
    await Promise.resolve();
    expect(calls.map((call) => call.path)).toEqual(["/auth/change-password"]);
    reissue.resolve(new Response(null, { status: 204 }));
    await change;
    await expect(check).resolves.toMatchObject({ username: "maya.chen" });
    expect(calls[0].body).toEqual({ current_password: "old-password", new_password: "new-password-123" });
  });
});

describe("downloadMyData", () => {
  function exportResponse(headers: Record<string, string>): Response {
    return new Response("PK", { status: 200, headers: { "Content-Type": "application/zip", ...headers } });
  }

  it("names the file from Content-Disposition and reads the counts", async () => {
    const calls = stubFetch({
      "POST /me/export": () =>
        exportResponse({
          "Content-Disposition": 'attachment; filename="maya-export-2026-10-20.zip"',
          "X-Export-Conversations": "12",
          "X-Export-Omitted": "1",
        }),
    });
    const result = await downloadMyData();
    expect(result).toMatchObject({ filename: "maya-export-2026-10-20.zip", conversations: 12, omitted: 1 });
    expect(await result.blob.text()).toBe("PK");
    expect(calls[0]).toMatchObject({ method: "POST", body: {} });
  });

  it("falls back to a safe name and zero counts", async () => {
    stubFetch({
      "POST /me/export": () =>
        exportResponse({ "Content-Disposition": 'attachment; filename="../../etc/passwd"', "X-Export-Omitted": "-3" }),
    });
    await expect(downloadMyData()).resolves.toMatchObject({
      filename: "asknuvexusdoc-export.zip",
      conversations: 0,
      omitted: 0,
    });
  });

  it("rejects with the server's message and reports a 401", async () => {
    stubFetch({ "POST /me/export": () => errorResponse(401, "Not authenticated") });
    await expect(downloadMyData()).rejects.toMatchObject({ status: 401, message: "Not authenticated" });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});

describe("saveBlob", () => {
  it("clicks a temporary download link and revokes its URL a minute later", () => {
    vi.useFakeTimers();
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:export-1");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });
    const blob = new Blob(["PK"]);

    saveBlob(blob, "export.zip");
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clicked).toHaveLength(1);
    expect(clicked[0]).toHaveAttribute("download", "export.zip");
    expect(clicked[0]).toHaveAttribute("href", "blob:export-1");
    expect(clicked[0].isConnected).toBe(false);
    vi.advanceTimersByTime(59_999);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:export-1");
  });
});

describe("plugin requests", () => {
  it("tell listeners about a 403 or 409, not about other failures", async () => {
    const listener = vi.fn();
    const unsubscribe = onPluginProblem(listener);
    stubFetch({
      "PATCH /plugins/gmail/connection": ({ body }) =>
        (body as { chat_enabled: boolean }).chat_enabled
          ? errorResponse(409, "Gmail needs to be reconnected.")
          : errorResponse(500, "boom"),
      "PATCH /plugins/off/connection": () => errorResponse(403, "The admin turned this plugin off."),
    });
    await expect(updatePluginConnection("gmail", { chat_enabled: true })).rejects.toMatchObject({ status: 409 });
    await expect(updatePluginConnection("off", { chat_enabled: true })).rejects.toMatchObject({ status: 403 });
    await expect(updatePluginConnection("gmail", { chat_enabled: false })).rejects.toMatchObject({ status: 500 });
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    await expect(updatePluginConnection("off", { chat_enabled: true })).rejects.toMatchObject({ status: 403 });
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("search Gmail with the query in the body, never the URL", async () => {
    const page: GmailThreadPage = { threads: [], next_page_token: null, result_size_estimate: 0 };
    const calls = stubFetch({ "POST /plugins/gmail/threads/search": page });
    await searchGmailThreads({ q: "from:priya invoice", pageToken: "t2", maxResults: 10 });
    await searchGmailThreads({ q: "" });
    expect(calls.map((call) => call.path)).toEqual(["/plugins/gmail/threads/search", "/plugins/gmail/threads/search"]);
    expect(calls[0].body).toEqual({ q: "from:priya invoice", page_token: "t2", max_results: 10 });
    expect(calls[1].body).toEqual({ q: null, page_token: null });
  });
});

describe("postSSE", () => {
  async function collect(chunks: string[]): Promise<SSEMessage[]> {
    stubFetch({ "POST /chat/stream": () => streamResponse(chunks) });
    const events: SSEMessage[] = [];
    await postSSE("/chat/stream", { message: "hi" }, (event) => events.push(event));
    return events;
  }

  const meta: ChatStreamEvent = {
    event: "meta",
    data: { conversation_id: 9, title: "Q3 review", user_message_id: 40, model: { id: 1, display_name: "Claude Opus 5.5" } },
  };

  it("posts JSON asking for an event stream", async () => {
    const calls = stubFetch({ "POST /chat/stream": () => streamResponse([]) });
    await postSSE("/chat/stream", { conversation_id: null, message: "Hello" }, () => {});
    expect(calls[0].body).toEqual({ conversation_id: null, message: "Hello" });
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toMatchObject({ Accept: "text/event-stream" });
  });

  it("parses events split anywhere across chunks", async () => {
    const stream = `event: meta\ndata: ${JSON.stringify(meta.data)}\n\nevent: delta\ndata: {"content":"Revenue"}\n\nevent: delta\ndata: {"content":" grew"}\n\n`;
    const chunks = [stream.slice(0, 7), stream.slice(7, 30), stream.slice(30, 131), stream.slice(131, 133), stream.slice(133)];
    expect(await collect(chunks)).toEqual([
      { event: "meta", data: meta.data },
      { event: "delta", data: { content: "Revenue" } },
      { event: "delta", data: { content: " grew" } },
    ]);
  });

  it("skips keepalive comments", async () => {
    const events = await collect([": keepalive\n\n", 'event: delta\ndata: {"content":"a"}\n\n', ": keepalive\n\n"]);
    expect(events).toEqual([{ event: "delta", data: { content: "a" } }]);
  });

  it("handles CRLF line ends, multi-line data, text data and a last event without a blank line", async () => {
    const events = await collect([
      "event: warning\r\ndata: {\"message\":\r\ndata: \"Document search unavailable\"}\r\n\r\n",
      "data: plain text\n\n",
      'event: done\ndata: {"assistant_message_id":41,"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12,"cost_usd":0.001}}',
    ]);
    expect(events).toEqual([
      { event: "warning", data: { message: "Document search unavailable" } },
      { event: "message", data: "plain text" },
      {
        event: "done",
        data: { assistant_message_id: 41, usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12, cost_usd: 0.001 } },
      },
    ]);
  });

  it("handles a CRLF split across chunks", async () => {
    const events = await collect(["event: delta\r", '\ndata: {"content":"a"}\r', "\n\r", "\n"]);
    expect(events).toEqual([{ event: "delta", data: { content: "a" } }]);
  });

  it("delivers a terminal error event like any other", async () => {
    const events = await collect([
      'event: delta\ndata: {"content":"Partial"}\n\n',
      'event: error\ndata: {"message":"The model provider returned an error.","assistant_message_id":41}\n\n',
    ]);
    expect(events.at(-1)).toEqual({
      event: "error",
      data: { message: "The model provider returned an error.", assistant_message_id: 41 },
    });
  });

  it("rejects with the JSON error before streaming, reporting a 401", async () => {
    stubFetch({ "POST /chat/stream": () => errorResponse(429, "Monthly token budget reached.") });
    await expect(postSSE("/chat/stream", {}, () => {})).rejects.toMatchObject({
      status: 429,
      message: "Monthly token budget reached.",
    });
    stubFetch({ "POST /chat/stream": () => errorResponse(401, "Not authenticated") });
    await expect(postSSE("/chat/stream", {}, () => {})).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("explains a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(postSSE("/chat/stream", {}, () => {})).rejects.toMatchObject({ status: 0 });
  });

  it("stops with an AbortError when aborted mid-stream", async () => {
    const encoder = new TextEncoder();
    stubFetch({
      "POST /chat/stream": (call: FetchCall) =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode('event: delta\ndata: {"content":"Rev"}\n\n'));
              call.signal?.addEventListener("abort", () =>
                controller.error(new DOMException("The operation was aborted.", "AbortError")),
              );
            },
          }),
          { status: 200 },
        ),
    });
    const controller = new AbortController();
    const events: SSEMessage[] = [];
    const done = postSSE("/chat/stream", {}, (event) => {
      events.push(event);
      controller.abort();
    }, controller.signal);
    await expect(done).rejects.toMatchObject({ name: "AbortError" });
    expect(events).toEqual([{ event: "delta", data: { content: "Rev" } }]);
  });

  it("rejects an abort before the response arrives", async () => {
    stubFetch({ "POST /chat/stream": () => deferred<Response>().promise });
    const controller = new AbortController();
    const done = postSSE("/chat/stream", {}, () => {}, controller.signal);
    controller.abort();
    await expect(done).rejects.toMatchObject({ name: "AbortError" });
  });
});
