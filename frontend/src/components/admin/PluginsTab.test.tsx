import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdminPlugin, AdminPluginUpdate, RequestCheckTestResult } from "../../api/types";
import { makeActivityReport, makeAdminPlugin } from "../../test/adminTabs";
import { makeAdminModel } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { monthLabel } from "./MonthPicker";
import PluginsTab from "./PluginsTab";

const chatModels = [
  makeAdminModel(),
  makeAdminModel({ id: 2, display_name: "GPT-5.5", litellm_model: "openai/gpt-5.5", is_default: false }),
  makeAdminModel({ id: 3, display_name: "Old model", is_default: false, enabled: false }),
  makeAdminModel({ id: 4, display_name: "Embeddings", kind: "embedding" }),
];

function applyUpdate(plugin: AdminPlugin, update: AdminPluginUpdate): AdminPlugin {
  return {
    ...plugin,
    enabled: update.enabled ?? plugin.enabled,
    capabilities: plugin.capabilities.map((capability) => ({
      ...capability,
      enabled: update.capabilities?.[capability.id] ?? capability.enabled,
    })),
  };
}

function stubPlugins(plugins: AdminPlugin[] | Route = [makeAdminPlugin()], extra: Record<string, Route> = {}) {
  return stubFetch({
    "GET /admin/plugins": plugins,
    "GET /admin/plugins/gmail/activity": makeActivityReport(),
    "GET /admin/models": chatModels,
    "PATCH /admin/plugins/gmail": ({ body }) => applyUpdate(makeAdminPlugin(), body as AdminPluginUpdate),
    ...extra,
  });
}

async function renderTab() {
  render(<PluginsTab />);
  return within(await screen.findByRole("region", { name: /^Gmail/ }));
}

const patches = (calls: FetchCall[]) => calls.filter((call) => call.method === "PATCH").map((call) => call.body);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T09:30:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("PluginsTab", () => {
  it("shows a configured plugin with its capabilities, Google setup and connections", async () => {
    stubPlugins([makeAdminPlugin({ testing_mode: true, connections: { connected: 3, reconnect_needed: 1, old_key: 2 } })]);
    const gmail = await renderTab();

    expect(gmail.getByRole("heading", { level: 2 })).toHaveTextContent("GmailConfigured");
    expect(gmail.getByRole("switch", { name: "Enabled" })).toBeChecked();
    expect(gmail.getByRole("switch", { name: "Read email" })).toBeChecked();
    expect(gmail.getByRole("switch", { name: "Read email" })).toHaveAccessibleDescription(
      "Search and read email.https://www.googleapis.com/auth/gmail.readonly",
    );
    expect(gmail.getByRole("switch", { name: "Send email" })).not.toBeChecked();
    expect(gmail.getByText("Google's draft permission also allows sending.")).toBeInTheDocument();
    expect(gmail.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "https://www.googleapis.com/auth/gmail.readonly",
      "https://www.googleapis.com/auth/gmail.compose",
      "http://localhost:8080/api/plugins/gmail/callbackCopy",
    ]);
    expect(gmail.getByText(/^Testing mode: only the Google accounts you add as test users/)).toBeInTheDocument();
    expect(gmail.getByText("3 connected, 1 needs reconnecting")).toBeInTheDocument();
    expect(gmail.getByText(/2 connections still use an older encryption key/)).toBeInTheDocument();
  });

  it("explains what a plugin that isn't set up still needs", async () => {
    stubPlugins([
      makeAdminPlugin({
        configured: false,
        enabled: false,
        missing_settings: ["GOOGLE_OAUTH_CLIENT_ID", "TOKEN_ENCRYPTION_KEY"],
        test_endpoints: true,
        redirect_uris: [],
        connections: { connected: 0, reconnect_needed: 0, old_key: 0 },
      }),
    ]);
    const gmail = await renderTab();

    expect(gmail.getByText("Not configured")).toBeInTheDocument();
    expect(gmail.getByText("Test endpoints")).toBeInTheDocument();
    const enabled = gmail.getByRole("switch", { name: "Enabled" });
    expect(enabled).toBeDisabled();
    expect(enabled).toHaveAccessibleDescription("Set the missing settings first");
    expect(gmail.getByText(/^Missing in \.env: GOOGLE_OAUTH_CLIENT_ID, TOKEN_ENCRYPTION_KEY\./)).toBeInTheDocument();
    expect(gmail.getByText(/The OAuth or API endpoints aren't Google's/)).toBeInTheDocument();
    expect(gmail.getByText("No valid PUBLIC_BASE_URLS entry: connecting fails until one is set.")).toBeInTheDocument();
    expect(gmail.getByText("No users have connected.")).toBeInTheDocument();
    expect(gmail.queryByRole("button", { name: "Disconnect all users" })).not.toBeInTheDocument();
  });

  it("turns the plugin and its capabilities on and off", async () => {
    const user = userEvent.setup();
    const calls = stubPlugins();
    const gmail = await renderTab();

    await user.click(gmail.getByRole("switch", { name: "Enabled" }));
    expect(
      await gmail.findByText("Gmail is off. Its tools and screens are gone from chat; connections stay until users disconnect."),
    ).toBeInTheDocument();
    expect(gmail.getByRole("switch", { name: "Enabled" })).not.toBeChecked();

    await user.click(gmail.getByRole("switch", { name: "Send email" }));
    expect(await gmail.findByText("Send email turned on.")).toBeInTheDocument();
    expect(gmail.getByRole("switch", { name: "Send email" })).toBeChecked();
    expect(patches(calls)).toEqual([{ enabled: false }, { capabilities: { send: true } }]);
    expect(calls.filter((call) => call.method === "PATCH").map((call) => call.path)).toEqual([
      "/admin/plugins/gmail",
      "/admin/plugins/gmail",
    ]);
  });

  it("shows the change while it saves and puts it back when the save fails", async () => {
    const user = userEvent.setup();
    const reply = deferred<Response>();
    stubPlugins([makeAdminPlugin()], { "PATCH /admin/plugins/gmail": () => reply.promise });
    const gmail = await renderTab();

    await user.click(gmail.getByRole("switch", { name: "Read email" }));
    expect(gmail.getByRole("switch", { name: "Read email" })).not.toBeChecked();
    expect(gmail.getByRole("switch", { name: "Create drafts" })).toBeDisabled();

    reply.resolve(errorResponse(422, "At least one capability must stay on."));
    expect(await gmail.findByText("At least one capability must stay on.")).toBeInTheDocument();
    expect(gmail.getByRole("switch", { name: "Read email" })).toBeChecked();
    expect(gmail.getByRole("switch", { name: "Create drafts" })).toBeEnabled();
  });

  it("disconnects every user after confirmation and reloads", async () => {
    const user = userEvent.setup();
    const calls = stubPlugins([makeAdminPlugin()], { "POST /admin/plugins/gmail/disconnect-all": { disconnected: 4 } });
    const gmail = await renderTab();

    await user.click(gmail.getByRole("button", { name: "Disconnect all users" }));
    expect(gmail.getByRole("group", { name: /^Disconnect every user's Gmail account\?/ })).toBeInTheDocument();
    await user.click(gmail.getByRole("button", { name: "Disconnect all" }));

    expect(await gmail.findByText("Disconnected 4 connections.")).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((call) => call.path === "/admin/plugins")).toHaveLength(2));
  });

  it("copies a redirect URI", async () => {
    const user = userEvent.setup();
    stubPlugins();
    const gmail = await renderTab();
    const uri = "http://localhost:8080/api/plugins/gmail/callback";

    await user.click(gmail.getByRole("button", { name: `Copy ${uri}` }));
    expect(gmail.getByRole("button", { name: `Copy ${uri}` })).toHaveTextContent("Copied");
    expect(gmail.getByText("Copied to the clipboard")).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe(uri);
  });

  it("says when no plugin is available, and when the list can't load", async () => {
    stubPlugins([]);
    const { unmount } = render(<PluginsTab />);
    expect(await screen.findByText("No plugins are available.")).toBeInTheDocument();
    unmount();

    stubPlugins(() => errorResponse(500, "Plugins are unavailable."));
    render(<PluginsTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Plugins are unavailable.");
  });
});

describe("plugin activity", () => {
  it("counts this month's actions overall and per user", async () => {
    const calls = stubPlugins();
    const gmail = await renderTab();
    const activity = within(gmail.getByRole("group", { name: `Gmail activity in ${monthLabel("2026-10")}` }));
    const table = (caption: string) =>
      within(activity.getByRole("table", { name: caption }))
        .getAllByRole("row")
        .map((row) => within(row).queryAllByRole("cell").map((cell) => cell.textContent));

    await activity.findByRole("table", { name: "Actions this month" });
    expect(table("Actions this month").slice(1)).toEqual([
      ["Connected", "3", "0", "0"],
      ["Searches", "14", "1", "0"],
      ["Emails sent", "2", "0", "1"],
    ]);
    expect(table("By user").slice(1)).toEqual([
      ["maya.chen", "10", "0", "0", "0", "0", "2", "0", "0"],
      ["sam.ortiz", "4", "0", "0", "0", "0", "0", "1", "1"],
    ]);
    expect(calls.find((call) => call.path.startsWith("/admin/plugins/gmail/activity"))?.path).toBe(
      "/admin/plugins/gmail/activity?month=2026-10",
    );
  });

  it("loads another month and says when nothing was recorded", async () => {
    const user = userEvent.setup();
    const calls = stubPlugins([makeAdminPlugin()], {
      "GET /admin/plugins/gmail/activity?month=2026-09": makeActivityReport({ month: "2026-09", totals: [], by_user: [] }),
    });
    const gmail = await renderTab();
    await gmail.findByRole("table", { name: "Actions this month" });

    await user.click(gmail.getByRole("button", { name: "Previous month" }));
    expect(await gmail.findByText(`Nothing recorded in ${monthLabel("2026-09")}.`)).toBeInTheDocument();
    expect(gmail.getByRole("heading", { name: `Gmail activity in ${monthLabel("2026-09")}` })).toBeInTheDocument();
    expect(calls.at(-1)?.path).toBe("/admin/plugins/gmail/activity?month=2026-09");
  });
});

describe("the request check", () => {
  const passed: RequestCheckTestResult = {
    ok: true,
    message: "The model asked for a draft only.",
    latency_ms: 812.4,
    answer: { file: false, image: false, draft: true, send: false, import: false },
  };

  it("tests an enabled chat model, the default first", async () => {
    const user = userEvent.setup();
    const reply = deferred<RequestCheckTestResult>();
    const calls = stubPlugins([makeAdminPlugin()], { "POST /admin/plugins/request-check/test": () => reply.promise });
    render(<PluginsTab />);
    const card = within(await screen.findByRole("region", { name: "Request check" }));

    const select = await card.findByRole("combobox", { name: "Chat model" });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual(["Claude Opus 5.5", "GPT-5.5"]);
    expect(select).toHaveDisplayValue("Claude Opus 5.5");
    await user.selectOptions(select, "GPT-5.5");
    await user.click(card.getByRole("button", { name: "Test" }));

    expect(card.getByText("Running the request check with GPT-5.5…")).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Test" })).toBeDisabled();
    reply.resolve(passed);
    const result = await card.findByText("The request check works with GPT-5.5");
    expect(result.parentElement).toHaveTextContent(" · 812 ms · the model found: draft");
    expect(card.getByText("The model asked for a draft only.")).toBeInTheDocument();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({ model_id: 2 });

    await user.click(card.getByRole("button", { name: "Dismiss test result" }));
    expect(card.queryByText("The request check works with GPT-5.5")).not.toBeInTheDocument();
  });

  it("reports a model that fails the check or a request that fails", async () => {
    const user = userEvent.setup();
    let reply: Route = { ...passed, ok: false, message: "The answer wasn't valid JSON.", answer: null, latency_ms: 0 };
    stubPlugins([makeAdminPlugin()], { "POST /admin/plugins/request-check/test": () => reply });
    render(<PluginsTab />);
    const card = within(await screen.findByRole("region", { name: "Request check" }));

    await user.click(await card.findByRole("button", { name: "Test" }));
    const alert = await card.findByRole("alert");
    expect(alert).toHaveTextContent("The request check failed with Claude Opus 5.5");
    expect(alert).toHaveTextContent("The answer wasn't valid JSON.");
    expect(alert).not.toHaveTextContent("ms");

    reply = errorResponse(502, "The provider timed out.");
    await user.click(card.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(card.getByRole("alert")).toHaveTextContent("The provider timed out."));
  });

  it("says when no chat model can be tested", async () => {
    stubPlugins([makeAdminPlugin()], { "GET /admin/models": [chatModels[2], chatModels[3]] });
    render(<PluginsTab />);
    const card = within(await screen.findByRole("region", { name: "Request check" }));
    expect(await card.findByText("No chat model is enabled.")).toBeInTheDocument();
    expect(card.queryByRole("button", { name: "Test" })).not.toBeInTheDocument();
  });
});
