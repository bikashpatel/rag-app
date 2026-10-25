import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PluginInfo } from "../../api/types";
import { announceUserChanged } from "../../lib/appEvents";
import { makeConnection, makeGmailPlugin } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type Route } from "../../test/http";
import { renderWithRouter } from "../../test/render";
import { reloadPlugins } from "../connections/pluginStore";
import PluginsMenu from "./PluginsMenu";

const notion = (overrides: Partial<PluginInfo> = {}): PluginInfo =>
  makeGmailPlugin({ id: "notion", name: "Notion", description: "Search your Notion pages.", ...overrides });

async function setup(plugins: Route, { isAdmin = false } = {}) {
  stubFetch({ "GET /plugins": plugins });
  const user = userEvent.setup();
  const onOpenPlugin = vi.fn();
  const view = renderWithRouter(<PluginsMenu isAdmin={isAdmin} onOpenPlugin={onOpenPlugin} />);
  await act(() => reloadPlugins());
  return { user, view, onOpenPlugin };
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, name: string | RegExp = "Plugins") {
  await user.click(screen.getByRole("button", { name }));
  return screen.getByRole("menu", { name: "Plugins" });
}

beforeEach(() => {
  announceUserChanged(null);
});

describe("PluginsMenu", () => {
  it.each<[string, PluginInfo, string]>([
    ["connected", makeGmailPlugin(), "Connected · maya@example.com"],
    [
      "connected but off in chat",
      makeGmailPlugin({ connection: makeConnection({ chat_enabled: false }) }),
      "Connected · maya@example.com · off in chat",
    ],
    ["not connected", makeGmailPlugin({ connection: null }), "Not connected"],
    [
      "reconnect needed",
      makeGmailPlugin({
        connection: makeConnection({
          status: "reconnect_needed",
          status_reason: "revoked",
          status_message: "Access was revoked in your Google account.",
        }),
      }),
      "Reconnect needed",
    ],
    ["turned off with a connection", makeGmailPlugin({ enabled: false }), "Turned off by the admin"],
    [
      "not set up with a connection",
      makeGmailPlugin({ configured: false, enabled: false }),
      "Not set up · maya@example.com still connected",
    ],
  ])("shows the status of a plugin that is %s and opens its card", async (_, plugin, status) => {
    const { user, onOpenPlugin } = await setup([plugin]);
    await openMenu(user, /^Plugins/);
    const item = screen.getByRole("menuitem", { name: "Gmail" });
    expect(item).toHaveTextContent(status);
    expect(item).not.toHaveAttribute("aria-disabled");
    await user.click(item);
    expect(onOpenPlugin).toHaveBeenCalledExactlyOnceWith("gmail");
  });

  it("flags a connection that needs to be reconnected on the button and in the row", async () => {
    const reconnect = makeConnection({
      status: "reconnect_needed",
      status_reason: "expired_testing",
      status_message: "Google ended the connection after 7 days.",
    });
    const { user } = await setup([makeGmailPlugin({ connection: reconnect }), notion({ connection: reconnect })]);
    await openMenu(user, "Plugins, 2 need attention");
    const item = screen.getByRole("menuitem", { name: "Gmail" });
    expect(item).toHaveTextContent("Google ended the connection after 7 days.");
    expect(within(item).getByText("Reconnect")).toBeInTheDocument();
  });

  it("offers Connect for a plugin that isn't connected, with its description", async () => {
    const { user } = await setup([makeGmailPlugin({ connection: null })]);
    await openMenu(user);
    const item = screen.getByRole("menuitem", { name: "Gmail" });
    expect(item).toHaveTextContent("Search, read, draft and send email from chat.");
    expect(within(item).getByText("Connect")).toBeInTheDocument();
  });

  it("disables a plugin the admin turned off when the user has no connection", async () => {
    const { user, onOpenPlugin } = await setup([makeGmailPlugin({ enabled: false, connection: null })]);
    await openMenu(user);
    const item = screen.getByRole("menuitem", { name: "Gmail" });
    expect(item).toHaveTextContent("Turned off by the admin");
    expect(item).toHaveTextContent("The admin turned Gmail off.");
    expect(item).toHaveAttribute("aria-disabled", "true");
    await user.click(item);
    expect(onOpenPlugin).not.toHaveBeenCalled();
  });

  it("tells a member that a plugin isn't set up yet", async () => {
    const { user } = await setup([makeGmailPlugin({ configured: false, enabled: false, connection: null })]);
    await openMenu(user);
    const item = screen.getByRole("menuitem", { name: "Gmail" });
    expect(item).toHaveTextContent("Not set up yet");
    expect(item).toHaveTextContent("The admin hasn't set up Gmail yet.");
    expect(item).toHaveAttribute("aria-disabled", "true");
  });

  it("links the admin to Admin → Plugins for a plugin that isn't set up", async () => {
    const { user, view, onOpenPlugin } = await setup([makeGmailPlugin({ configured: false, enabled: false, connection: null })], {
      isAdmin: true,
    });
    await openMenu(user);
    const item = screen.getByRole("menuitem", { name: "Gmail" });
    expect(item).toHaveTextContent("Not set up");
    expect(item).toHaveTextContent("Needs a Google OAuth client before anyone can connect.");
    expect(item).toHaveTextContent("Set up in Admin → Plugins");
    await user.click(item);
    expect(view.location()).toBe("/admin#plugins");
    expect(onOpenPlugin).not.toHaveBeenCalled();
  });

  it("opens Settings without a particular plugin from Manage plugins…", async () => {
    const { user, onOpenPlugin } = await setup([makeGmailPlugin()]);
    await openMenu(user);
    await user.click(screen.getByRole("menuitem", { name: "Manage plugins…" }));
    expect(onOpenPlugin).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("says the list is loading", async () => {
    const pending = deferred<PluginInfo[]>();
    stubFetch({ "GET /plugins": () => pending.promise });
    const user = userEvent.setup();
    renderWithRouter(<PluginsMenu isAdmin={false} onOpenPlugin={vi.fn()} />);
    void reloadPlugins();
    const menu = await openMenu(user);
    expect(menu).toHaveAccessibleDescription("Apps the assistant can use for you in chat. Loading plugins…");
    expect(screen.queryByRole("menuitem", { name: "Gmail" })).not.toBeInTheDocument();
    await act(async () => pending.resolve([makeGmailPlugin()]));
    expect(screen.getByRole("menuitem", { name: "Gmail" })).toBeInTheDocument();
  });

  it("says when no plugins are available", async () => {
    const { user } = await setup([]);
    const menu = await openMenu(user);
    expect(menu).toHaveAccessibleDescription("Apps the assistant can use for you in chat. No plugins are available.");
  });

  it("shows a load error with Try again, which reloads the list", async () => {
    let fail = true;
    const { user } = await setup(() => (fail ? errorResponse(503, "The plugin service is unavailable.") : [makeGmailPlugin()]));
    const menu = await openMenu(user);
    expect(menu).toHaveAccessibleDescription(
      "Apps the assistant can use for you in chat. The plugin service is unavailable.",
    );
    fail = false;
    await user.click(screen.getByRole("menuitem", { name: "Try again" }));
    await openMenu(user);
    expect(await screen.findByRole("menuitem", { name: "Gmail" })).toHaveTextContent("Connected · maya@example.com");
  });
});
