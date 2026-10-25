import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { onPluginBrowserRequest, requestConnections } from "../connections/pluginStore";
import { makeAccount, makeChatModel, makeGmailPlugin, makeMe, makeMyUsage } from "../../test/fixtures";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import SettingsHost from "./SettingsHost";

function settingsRoutes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    "GET /auth/me": makeMe(),
    "GET /models": [makeChatModel()],
    "GET /me/account": makeAccount(),
    "GET /me/usage": makeMyUsage(),
    "GET /plugins": [makeGmailPlugin()],
    ...overrides,
  };
}

function renderHost(route: string, overrides: Record<string, Route> = {}) {
  stubFetch(settingsRoutes(overrides));
  return renderWithAuth(<SettingsHost />, { route });
}

describe("SettingsHost", () => {
  it("shows nothing without ?settings", async () => {
    await renderHost("/");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows nothing while signed out", async () => {
    await renderHost("/?settings=general", { "GET /auth/me": errorResponse(401, "Not authenticated") });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the tab named in the URL as a named dialog with the tab focused", async () => {
    await renderHost("/?settings=usage");
    const dialog = screen.getByRole("dialog", { name: "Settings" });
    const tab = within(dialog).getByRole("tab", { name: "Usage" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(tab).toHaveFocus();
    expect(within(dialog).getByRole("tablist", { name: "Settings" })).toBeInTheDocument();
    expect(within(dialog).getByRole("tabpanel", { name: "Usage" })).toBeInTheDocument();
    expect(await within(dialog).findByRole("heading", { name: "Usage", level: 2 })).toBeInTheDocument();
  });

  it.each([
    ["data", "Plugins & data"],
    ["plugins", "Plugins & data"],
    ["nonsense", "General"],
  ])("maps ?settings=%s to the %s tab", async (value, label) => {
    await renderHost(`/?settings=${value}`);
    expect(screen.getByRole("tab", { name: label })).toHaveAttribute("aria-selected", "true");
  });

  it("switches tabs by click, keeping the URL in sync", async () => {
    const user = userEvent.setup();
    const view = await renderHost("/c/12?settings=general");
    await user.click(screen.getByRole("tab", { name: "Account" }));
    expect(view.location()).toBe("/c/12?settings=account");
    expect(screen.getByRole("tabpanel", { name: "Account" })).toBeInTheDocument();
    expect(await screen.findByText("Recent sign-ins")).toBeInTheDocument();
  });

  it("moves between tabs with the arrow keys, Home and End", async () => {
    const user = userEvent.setup();
    const view = await renderHost("/?settings=general");
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("tab", { name: "Account" })).toHaveFocus();
    expect(view.location()).toBe("/?settings=account");
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Plugins & data" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("tab", { name: "Plugins & data" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(view.location()).toBe("/?settings=general");
  });

  it("only the selected tab is a tab stop", async () => {
    await renderHost("/?settings=account");
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([-1, 0, -1, -1]);
  });

  it("closes from the rail's close button, keeping the page and other parameters", async () => {
    const user = userEvent.setup();
    const view = await renderHost("/c/12?settings=general&q=renewal");
    await user.click(screen.getAllByRole("button", { name: "Close settings" })[0]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(view.location()).toBe("/c/12?q=renewal");
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const view = await renderHost("/?settings=usage");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(view.location()).toBe("/");
  });

  it("turns Google's OAuth return into Plugins & data with the outcome", async () => {
    const view = await renderHost("/?plugin=gmail&oauth=connected");
    expect(await screen.findByRole("tab", { name: "Plugins & data" })).toHaveAttribute("aria-selected", "true");
    expect(view.location()).toBe("/?settings=plugins");
    expect(await screen.findByText("Gmail is connected as maya@example.com.")).toBeInTheDocument();
  });

  it("reports a refused OAuth return as an alert", async () => {
    await renderHost("/?plugin=gmail&oauth=no_access");
    expect(await screen.findByRole("alert")).toHaveTextContent("No Gmail access was allowed. Connect again and tick the boxes.");
  });

  it("opens Plugins & data when another area asks for the connections", async () => {
    const view = await renderHost("/");
    act(() => requestConnections("gmail"));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Plugins & data" })).toHaveAttribute("aria-selected", "true");
    expect(view.location()).toBe("/?settings=plugins");
  });

  it.each([
    ["/c/12?settings=plugins", "/c/12"],
    ["/admin?settings=plugins", "/"],
  ])("Browse Gmail from %s closes Settings, then asks the chat page for the browser", async (route, landing) => {
    const user = userEvent.setup();
    const browser = vi.fn();
    const unsubscribe = onPluginBrowserRequest(browser);
    const view = await renderHost(route);
    await user.click(await screen.findByRole("button", { name: "Browse Gmail" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(view.location()).toBe(landing);
    expect(browser).toHaveBeenCalledExactlyOnceWith("gmail");
    unsubscribe();
  });
});
