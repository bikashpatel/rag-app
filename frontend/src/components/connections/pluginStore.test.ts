import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PluginInfo } from "../../api/types";
import { makeCapability, makeConnection, makeGmailPlugin } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch } from "../../test/http";

type Store = typeof import("./pluginStore");
type Modules = Store & {
  client: typeof import("../../api/client");
  appEvents: typeof import("../../lib/appEvents");
};

/** The store, the client and the app events as one fresh module graph: the store keeps its state at module level. */
async function load(): Promise<Modules> {
  vi.resetModules();
  const store = await import("./pluginStore");
  const client = await import("../../api/client");
  const appEvents = await import("../../lib/appEvents");
  return { ...store, client, appEvents };
}

const disabledPlugin = makeGmailPlugin({ id: "drive", name: "Drive", enabled: false, connection: null });

let now = Date.parse("2026-10-20T09:30:00Z");

beforeEach(() => {
  now = Date.parse("2026-10-20T09:30:00Z");
  vi.spyOn(Date, "now").mockImplementation(() => now);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("plugin status", () => {
  it.each<[string, PluginInfo, string]>([
    ["not configured", makeGmailPlugin({ configured: false, enabled: false }), "Not set up"],
    ["turned off", makeGmailPlugin({ enabled: false }), "Turned off by the admin"],
    ["outside the plan", makeGmailPlugin({ in_plan: false }), "Not in your plan"],
    ["turned off and outside the plan", makeGmailPlugin({ enabled: false, in_plan: false }), "Turned off by the admin"],
    [
      "needing a reconnect",
      makeGmailPlugin({ connection: makeConnection({ status: "reconnect_needed", status_reason: "revoked" }) }),
      "Reconnect needed",
    ],
    ["connected", makeGmailPlugin(), "Connected"],
    ["not connected", makeGmailPlugin({ connection: null }), "Not connected"],
  ])("labels a plugin %s", async (_, plugin, label) => {
    const { pluginStatus, PLUGIN_STATUS_LABEL } = await load();
    expect(PLUGIN_STATUS_LABEL[pluginStatus(plugin)]).toBe(label);
  });

  it("finds a capability usable only when enabled and granted", async () => {
    const { capabilityUsable } = await load();
    const plugin = makeGmailPlugin({
      capabilities: [
        makeCapability({ id: "read" }),
        makeCapability({ id: "draft", granted: false }),
        makeCapability({ id: "send", enabled: false }),
      ],
    });
    expect(capabilityUsable(plugin, "read")).toBe(true);
    expect(capabilityUsable(plugin, "draft")).toBe(false);
    expect(capabilityUsable(plugin, "send")).toBe(false);
    expect(capabilityUsable(plugin, "calendar")).toBe(false);
  });

  it("offers Gmail browsing only when it is enabled, connected and can read", async () => {
    const { gmailReady } = await load();
    const gmail = makeGmailPlugin();
    expect(gmailReady([disabledPlugin, gmail])).toBe(gmail);
    expect(gmailReady(null)).toBeNull();
    expect(gmailReady([makeGmailPlugin({ enabled: false })])).toBeNull();
    expect(gmailReady([makeGmailPlugin({ in_plan: false })])).toBeNull();
    expect(gmailReady([makeGmailPlugin({ connection: makeConnection({ status: "reconnect_needed" }) })])).toBeNull();
    expect(gmailReady([makeGmailPlugin({ capabilities: [makeCapability({ granted: false })] })])).toBeNull();
  });

  it("explains why a capability can't be used", async () => {
    const { unusableReason } = await load();
    const reason = (plugin: PluginInfo | undefined, capability = "send") => unusableReason(plugin, capability, "Gmail");
    expect(reason(makeGmailPlugin())).toBeNull();
    expect(reason(makeGmailPlugin({ configured: false }))).toBe("Gmail isn't set up yet.");
    expect(reason(undefined)).toBe("The admin turned Gmail off.");
    expect(reason(makeGmailPlugin({ enabled: false }))).toBe("The admin turned Gmail off.");
    expect(reason(makeGmailPlugin({ in_plan: false }))).toBe("Your plan doesn't include Gmail.");
    expect(reason(makeGmailPlugin({ connection: null }))).toBe("Gmail is no longer connected.");
    expect(reason(makeGmailPlugin({ connection: makeConnection({ status: "reconnect_needed" }) }))).toBe(
      "Gmail needs to be reconnected.",
    );
    expect(reason(makeGmailPlugin({ capabilities: [makeCapability({ id: "send", name: "Send email", enabled: false })] }))).toBe(
      "The admin turned off “Send email” for Gmail.",
    );
    expect(reason(makeGmailPlugin(), "calendar")).toBe("The admin turned off “calendar” for Gmail.");
    expect(reason(makeGmailPlugin({ capabilities: [makeCapability({ id: "send", name: "Send email", granted: false })] }))).toBe(
      "Your connection no longer allows “Send email”. Grant access again below.",
    );
  });
});

describe("the plugin list", () => {
  it("loads into every usePlugins hook", async () => {
    const { usePlugins, reloadPlugins } = await load();
    stubFetch({ "GET /plugins": [makeGmailPlugin()] });
    const { result } = renderHook(() => usePlugins());
    expect(result.current.plugins).toBeNull();
    await act(() => reloadPlugins());
    expect(result.current.plugins?.map((plugin) => plugin.name)).toEqual(["Gmail"]);
    expect(result.current.error).toBeNull();
  });

  it("keeps the list it has and shows why a reload failed", async () => {
    const { usePlugins, reloadPlugins } = await load();
    stubFetch({ "GET /plugins": [makeGmailPlugin()] });
    const { result } = renderHook(() => usePlugins());
    await act(() => reloadPlugins());
    stubFetch({ "GET /plugins": () => errorResponse(503, "Plugins are unavailable.") });
    await act(() => result.current.reload());
    expect(result.current.error).toBe("Plugins are unavailable.");
    expect(result.current.plugins).toHaveLength(1);
    stubFetch({ "GET /plugins": () => new Response("<html>proxy error</html>", { status: 200 }) });
    await act(() => result.current.reload());
    expect(result.current.error).toBe("Couldn't load your connections.");
    expect(result.current.plugins).toHaveLength(1);
  });

  it("drops the answer to a superseded request", async () => {
    const { usePlugins, reloadPlugins } = await load();
    const slow = deferred<PluginInfo[]>();
    let request = 0;
    stubFetch({ "GET /plugins": () => (++request === 1 ? slow.promise : [disabledPlugin]) });
    const { result } = renderHook(() => usePlugins());
    const first = reloadPlugins();
    await act(() => reloadPlugins());
    slow.resolve([makeGmailPlugin()]);
    await act(() => first);
    expect(result.current.plugins).toEqual([disabledPlugin]);
    expect(result.current.error).toBeNull();
  });

  it("applies a returned connection at once", async () => {
    const { usePlugins, reloadPlugins, setPluginConnection } = await load();
    stubFetch({ "GET /plugins": [makeGmailPlugin(), disabledPlugin] });
    const { result } = renderHook(() => usePlugins());
    setPluginConnection("gmail", makeConnection());
    expect(result.current.plugins).toBeNull();
    await act(() => reloadPlugins());
    act(() => setPluginConnection("gmail", makeConnection({ chat_enabled: false })));
    expect(result.current.plugins?.[0].connection?.chat_enabled).toBe(false);
    expect(result.current.plugins?.[1]).toEqual(disabledPlugin);
  });

  it("reloads after a plugin request is refused", async () => {
    const { reloadPlugins, client } = await load();
    const calls = stubFetch({
      "GET /plugins": [makeGmailPlugin()],
      "PATCH /plugins/gmail/connection": () => errorResponse(409, "Gmail needs to be reconnected."),
    });
    await reloadPlugins();
    await expect(client.updatePluginConnection("gmail", { chat_enabled: true })).rejects.toMatchObject({ status: 409 });
    await waitFor(() => expect(calls.filter((call) => call.path === "/plugins")).toHaveLength(2));
  });
});

describe("usePluginLifecycle", () => {
  it("loads on mount, reloads on focus at most every 30 s, and forgets the list on unmount", async () => {
    const { usePluginLifecycle, usePlugins } = await load();
    const calls = stubFetch({ "GET /plugins": [makeGmailPlugin()] });
    const loads = () => calls.filter((call) => call.path === "/plugins").length;
    const lifecycle = renderHook(() => usePluginLifecycle());
    const { result } = renderHook(() => usePlugins());
    await waitFor(() => expect(result.current.plugins).toHaveLength(1));
    expect(loads()).toBe(1);

    now += 29_000;
    window.dispatchEvent(new Event("focus"));
    expect(loads()).toBe(1);
    now += 1_000;
    window.dispatchEvent(new Event("focus"));
    expect(loads()).toBe(2);
    now += 30_000;
    document.dispatchEvent(new Event("visibilitychange"));
    expect(loads()).toBe(3);

    lifecycle.unmount();
    expect(result.current.plugins).toBeNull();
    now += 60_000;
    window.dispatchEvent(new Event("focus"));
    expect(loads()).toBe(3);
  });

  it("clears the list when the user changes and loads the new user's while mounted", async () => {
    const { usePluginLifecycle, usePlugins, appEvents } = await load();
    let user = "maya";
    const calls = stubFetch({ "GET /plugins": () => (user === "maya" ? [makeGmailPlugin()] : [disabledPlugin]) });
    renderHook(() => usePluginLifecycle());
    const { result } = renderHook(() => usePlugins());
    await waitFor(() => expect(result.current.plugins?.[0]?.id).toBe("gmail"));

    user = "sam";
    act(() => appEvents.announceUserChanged(12));
    await waitFor(() => expect(result.current.plugins?.[0]?.id).toBe("drive"));
    act(() => appEvents.announceUserChanged(null));
    expect(result.current.plugins).toBeNull();
    expect(calls.filter((call) => call.path === "/plugins")).toHaveLength(2);
  });
});

describe("cross-page requests", () => {
  it("open Settings → Plugins & data with the plugin whose browser closed", async () => {
    const { requestConnections, onConnectionsRequest } = await load();
    const listener = vi.fn();
    const unsubscribe = onConnectionsRequest(listener);
    requestConnections();
    requestConnections("gmail");
    unsubscribe();
    requestConnections();
    expect(listener.mock.calls).toEqual([[null], ["gmail"]]);
  });

  it("deliver a browser request to the chat page at once, or when it mounts within 5 s", async () => {
    const { requestPluginBrowser, onPluginBrowserRequest } = await load();
    const early = vi.fn();
    requestPluginBrowser("gmail");
    now += 4_999;
    const unsubscribe = onPluginBrowserRequest(early);
    expect(early).toHaveBeenCalledWith("gmail");

    const later = vi.fn();
    onPluginBrowserRequest(later)();
    expect(later).not.toHaveBeenCalled();

    requestPluginBrowser("gmail");
    expect(early).toHaveBeenCalledTimes(2);
    unsubscribe();
  });

  it("drop a browser request that waited too long or outlived its user", async () => {
    const { requestPluginBrowser, onPluginBrowserRequest, appEvents } = await load();
    const listener = vi.fn();
    requestPluginBrowser("gmail");
    now += 5_000;
    onPluginBrowserRequest(listener)();
    requestPluginBrowser("gmail");
    appEvents.announceUserChanged(null);
    onPluginBrowserRequest(listener)();
    expect(listener).not.toHaveBeenCalled();
  });
});
