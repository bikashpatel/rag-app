import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me, PluginInfo } from "../../api/types";
import { onConversationsDeleted } from "../../lib/appEvents";
import {
  makeAccount,
  makeAdminMe,
  makeConnection,
  makeDocument,
  makeGmailPlugin,
  makeLapsedPlanAccess,
  makeMe,
} from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import PluginsDataTab, { type PluginsIntent } from "./PluginsDataTab";

const NO_INTENT: PluginsIntent = { oauthReturn: null, closedPluginId: null, focusPluginId: null };

function exportResponse(headers: Record<string, string>): Response {
  return new Response("PK\u0003\u0004zip", { status: 200, headers: { "Content-Type": "application/zip", ...headers } });
}

async function renderTab(overrides: Record<string, Route> = {}, me: Me = makeMe()) {
  const onBrowse = vi.fn();
  const onAllChatsDeleted = vi.fn();
  const calls = stubFetch({
    "GET /auth/me": me,
    "GET /me/account": makeAccount({ conversation_count: 12 }),
    "GET /plugins": [makeGmailPlugin()],
    ...overrides,
  });
  const view = await renderWithAuth(
    <PluginsDataTab
      user={me}
      intent={NO_INTENT}
      intentKey=""
      onBrowse={onBrowse}
      onAllChatsDeleted={onAllChatsDeleted}
      onClose={() => {}}
    />,
  );
  return { calls, onBrowse, onAllChatsDeleted, view };
}

function sent(calls: FetchCall[], method: string, path: string): unknown[] {
  return calls.filter((call) => call.method === method && call.path === path).map((call) => call.body);
}

describe("PluginsDataTab", () => {
  describe("Your data", () => {
    it("lists a lapsed user's own documents and chats above the plugins", async () => {
      await renderTab(
        {
          "GET /documents": [makeDocument({ filename: "Q3 report.pdf" })],
          "GET /conversations": [{ id: 4, title: "Notice period", created_at: "2026-10-01T09:00:00Z", updated_at: "2026-10-02T09:00:00Z" }],
        },
        makeMe({ plan: makeLapsedPlanAccess() }),
      );
      expect(await screen.findByText("Q3 report.pdf")).toBeInTheDocument();
      expect(await screen.findByText("Notice period")).toBeInTheDocument();
      expect(screen.getByRole("group", { name: "Your documents" })).toBeInTheDocument();
      expect(screen.getByRole("group", { name: "Your chats" })).toBeInTheDocument();
      // No documents panel without a plan: the export points at the list above.
      expect(screen.getByText(/^A ZIP with your conversations/)).toHaveTextContent(
        "Document files aren't included: download them from Your documents above.",
      );
    });

    it("isn't shown to a user with a plan", async () => {
      const { calls } = await renderTab();
      expect(await screen.findByRole("heading", { name: "Gmail" })).toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "Your documents" })).not.toBeInTheDocument();
      expect(screen.getByText(/^A ZIP with your conversations/)).toHaveTextContent(
        "Document files aren't included: download them from the documents panel.",
      );
      expect(calls.some((call) => call.path === "/documents")).toBe(false);
    });

    it("isn't shown to the admin", async () => {
      await renderTab({}, makeAdminMe({ plan: makeLapsedPlanAccess("none") }));
      expect(await screen.findByRole("heading", { name: "Gmail" })).toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "Your chats" })).not.toBeInTheDocument();
    });
  });

  describe("Export my data", () => {
    let downloads: string[];

    beforeEach(() => {
      downloads = [];
      vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:http://localhost/export");
      vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        downloads.push(`${this.download} ${this.href}`);
      });
    });

    it("downloads the export under the server's file name", async () => {
      const user = userEvent.setup();
      const response = deferred<Response>();
      const { calls } = await renderTab({ "POST /me/export": () => response.promise });
      const button = screen.getByRole("button", { name: "Export" });
      await user.click(button);
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByRole("status")).toHaveTextContent("Preparing your export…");
      response.resolve(
        exportResponse({
          "Content-Disposition": 'attachment; filename="rag-assistant-export-2026-10-20.zip"',
          "X-Export-Conversations": "12",
          "X-Export-Omitted": "0",
        }),
      );
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved rag-assistant-export-2026-10-20.zip (7 B)."));
      expect(downloads).toEqual(["rag-assistant-export-2026-10-20.zip blob:http://localhost/export"]);
      expect(sent(calls, "POST", "/me/export")).toEqual([{}]);
      expect(button).not.toHaveAttribute("aria-disabled");
    });

    it("falls back to a default name and says which conversations didn't fit", async () => {
      const user = userEvent.setup();
      await renderTab({
        "POST /me/export": () => exportResponse({ "X-Export-Conversations": "50", "X-Export-Omitted": "3" }),
      });
      await user.click(screen.getByRole("button", { name: "Export" }));
      expect(await screen.findByText("Saved rag-assistant-export.zip (7 B).")).toBeInTheDocument();
      expect(
        screen.getByText("The export holds your newest 50 conversations; 3 older ones didn't fit."),
      ).toBeInTheDocument();
      expect(downloads).toEqual(["rag-assistant-export.zip blob:http://localhost/export"]);
    });

    it("reports a refused export", async () => {
      const user = userEvent.setup();
      await renderTab({ "POST /me/export": errorResponse(409, "An export is already being prepared.") });
      await user.click(screen.getByRole("button", { name: "Export" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("An export is already being prepared.");
      expect(downloads).toEqual([]);
    });
  });

  describe("Delete all chats", () => {
    it("asks for DELETE to be typed before deleting", async () => {
      const user = userEvent.setup();
      const deletedEvents = vi.fn();
      const unsubscribe = onConversationsDeleted(deletedEvents);
      const { calls, onAllChatsDeleted } = await renderTab({ "DELETE /conversations": { deleted: 12 } });
      expect(await screen.findByText(/Deletes every conversation \(12 now\)/)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Delete all chats…" }));

      const form = screen.getByRole("form", { name: "Confirm deleting all chats" });
      const word = within(form).getByRole("textbox", { name: "Type DELETE to confirm" });
      const submit = within(form).getByRole("button", { name: "Delete 12 chats" });
      expect(word).toHaveFocus();
      expect(submit).toBeDisabled();
      await user.type(word, "DELET");
      expect(submit).toBeDisabled();
      await user.type(word, "e ");
      expect(submit).toBeEnabled();
      await user.click(submit);

      const result = await screen.findByText("Deleted 12 conversations.");
      await waitFor(() => expect(result.closest("[role=status]")).toHaveFocus());
      expect(screen.queryByRole("form", { name: "Confirm deleting all chats" })).not.toBeInTheDocument();
      expect(screen.getByText("You have no conversations right now.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete all chats…" })).toHaveAttribute("aria-disabled", "true");
      expect(sent(calls, "DELETE", "/conversations")).toHaveLength(1);
      expect(onAllChatsDeleted).toHaveBeenCalledTimes(1);
      expect(deletedEvents).toHaveBeenCalledTimes(1);
      unsubscribe();
    });

    it("never sends without the confirmation word, even on Enter", async () => {
      const user = userEvent.setup();
      const { calls } = await renderTab({ "DELETE /conversations": { deleted: 12 } });
      await user.click(await screen.findByRole("button", { name: "Delete all chats…" }));
      await user.type(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), "yes{Enter}");
      expect(sent(calls, "DELETE", "/conversations")).toHaveLength(0);
      expect(screen.getByRole("form", { name: "Confirm deleting all chats" })).toBeInTheDocument();
    });

    it("cancels back to its button", async () => {
      const user = userEvent.setup();
      await renderTab();
      await user.click(await screen.findByRole("button", { name: "Delete all chats…" }));
      await user.type(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), "DELETE");
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("form", { name: "Confirm deleting all chats" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete all chats…" })).toHaveFocus();
      await user.click(screen.getByRole("button", { name: "Delete all chats…" }));
      expect(screen.getByRole("textbox", { name: "Type DELETE to confirm" })).toHaveValue("");
    });

    it("keeps the confirmation open with the error when deleting fails", async () => {
      const user = userEvent.setup();
      const { onAllChatsDeleted } = await renderTab({ "DELETE /conversations": errorResponse(500, null) });
      await user.click(await screen.findByRole("button", { name: "Delete all chats…" }));
      await user.type(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), "DELETE{Enter}");
      expect(await screen.findByRole("alert")).toHaveTextContent("The server ran into a problem. Please try again.");
      expect(screen.getByRole("form", { name: "Confirm deleting all chats" })).toBeInTheDocument();
      expect(onAllChatsDeleted).not.toHaveBeenCalled();
    });

    it("can't be opened without conversations", async () => {
      const user = userEvent.setup();
      await renderTab({ "GET /me/account": makeAccount({ conversation_count: 0 }) });
      expect(await screen.findByText("You have no conversations right now.")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Delete all chats…" }));
      expect(screen.queryByRole("form", { name: "Confirm deleting all chats" })).not.toBeInTheDocument();
    });
  });

  describe("Plugins", () => {
    it("shows a connected plugin with what it may do", async () => {
      const user = userEvent.setup();
      const gmail = makeGmailPlugin({
        capabilities: [
          ...makeGmailPlugin().capabilities.slice(0, 2),
          { id: "send", name: "Send email", description: "Send email after you confirm it.", enabled: false, granted: false },
        ],
      });
      const { onBrowse } = await renderTab({ "GET /plugins": [gmail] });
      const card = await screen.findByRole("region", { name: "Gmail" });
      expect(within(card).getByText("Connected as maya@example.com")).toBeInTheDocument();
      const capabilities = within(within(card).getByRole("list", { name: "What Gmail can be used for" })).getAllByRole("listitem");
      expect(capabilities[0]).toHaveTextContent("Read email (allowed)");
      expect(capabilities[2]).toHaveTextContent("Send email · off (turned off by the admin)");
      expect(within(card).getByRole("switch", { name: "Use in chat" })).toBeChecked();
      await user.click(within(card).getByRole("button", { name: "Browse Gmail" }));
      expect(onBrowse).toHaveBeenCalledWith(gmail);
    });

    it("keeps a connection outside the plan, with Upgrade and Disconnect but nothing that uses it", async () => {
      const { view } = await renderTab({ "GET /plugins": [makeGmailPlugin({ in_plan: false })] });
      const card = await screen.findByRole("region", { name: "Gmail" });
      expect(within(card).getByText("Not in your plan")).toBeInTheDocument();
      expect(card).toHaveTextContent("maya@example.com is still connected.");
      expect(card).toHaveTextContent(
        "Your plan doesn't include Gmail. Upgrade to Professional to use it again; you won't need to reconnect.",
      );
      expect(within(card).getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
      expect(within(card).queryByRole("switch", { name: "Use in chat" })).not.toBeInTheDocument();
      expect(within(card).queryByRole("button", { name: "Browse Gmail" })).not.toBeInTheDocument();
      expect(within(card).queryByRole("list", { name: "What Gmail can be used for" })).not.toBeInTheDocument();
      await userEvent.setup().click(within(card).getByRole("link", { name: "Upgrade" }));
      expect(view.location()).toBe("/plans");
    });

    it("offers Upgrade instead of Connect for a plugin outside the plan", async () => {
      await renderTab({ "GET /plugins": [makeGmailPlugin({ in_plan: false, connection: null })] });
      const card = await screen.findByRole("region", { name: "Gmail" });
      expect(card).toHaveTextContent("Included in Professional.");
      expect(within(card).getByRole("link", { name: "Upgrade" })).toHaveAttribute("href", "/plans");
      expect(within(card).queryByRole("button", { name: "Connect Gmail" })).not.toBeInTheDocument();
    });

    it.each<[string, Partial<PluginInfo>, string]>([
      ["Not set up", { configured: false, enabled: false, connection: null }, "Gmail isn't set up yet."],
      ["Turned off by the admin", { enabled: false, connection: null }, "The admin turned Gmail off, so the assistant can't use it."],
      ["Not connected", { connection: null }, "Search, read, draft and send email from chat."],
    ])("labels a plugin %s", async (status, overrides, text) => {
      await renderTab({ "GET /plugins": [makeGmailPlugin(overrides)] });
      const card = await screen.findByRole("region", { name: "Gmail" });
      expect(within(card).getByText(status)).toBeInTheDocument();
      expect(within(card).getByText(text, { exact: false })).toBeInTheDocument();
      expect(within(card).queryByRole("switch", { name: "Use in chat" })).not.toBeInTheDocument();
    });

    it("offers Connect only when the plugin can be connected", async () => {
      await renderTab({ "GET /plugins": [makeGmailPlugin({ connection: null })] });
      expect(await screen.findByRole("button", { name: "Connect Gmail" })).toBeEnabled();
    });

    it("asks for a reconnect with the server's reason", async () => {
      const connection = makeConnection({
        status: "reconnect_needed",
        status_reason: "revoked",
        status_message: "Google access was removed. Reconnect to keep using Gmail.",
      });
      await renderTab({ "GET /plugins": [makeGmailPlugin({ connection })] });
      const card = await screen.findByRole("region", { name: "Gmail" });
      expect(within(card).getByText("Reconnect needed")).toBeInTheDocument();
      expect(within(card).getByRole("status")).toHaveTextContent("Google access was removed. Reconnect to keep using Gmail.");
      expect(within(card).getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
      expect(within(card).queryByRole("button", { name: "Browse Gmail" })).not.toBeInTheDocument();
    });

    it("turns Use in chat off", async () => {
      const user = userEvent.setup();
      const { calls } = await renderTab({
        "PATCH /plugins/gmail/connection": makeConnection({ chat_enabled: false }),
      });
      const toggle = await screen.findByRole("switch", { name: "Use in chat" });
      await user.click(toggle);
      await waitFor(() => expect(toggle).toBeEnabled());
      expect(toggle).not.toBeChecked();
      expect(sent(calls, "PATCH", "/plugins/gmail/connection")).toEqual([{ chat_enabled: false }]);
    });

    it("disconnects after a confirmation", async () => {
      const user = userEvent.setup();
      let connected = true;
      const { calls } = await renderTab({
        "GET /plugins": () => [makeGmailPlugin({ connection: connected ? makeConnection() : null })],
        "DELETE /plugins/gmail/connection": () => {
          connected = false;
          return new Response(null, { status: 204 });
        },
      });
      await user.click(await screen.findByRole("button", { name: "Disconnect" }));
      expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
      await user.click(screen.getByRole("button", { name: "Disconnect Gmail" }));
      expect(await screen.findByRole("button", { name: "Connect Gmail" })).toBeInTheDocument();
      expect(screen.getByText(/^Disconnected\. Google access is also removed/)).toBeInTheDocument();
      expect(sent(calls, "DELETE", "/plugins/gmail/connection")).toHaveLength(1);
    });

    it("reports a failed plugin list with Retry", async () => {
      const user = userEvent.setup();
      let fail = true;
      await renderTab({ "GET /plugins": () => (fail ? errorResponse(503, "Plugins are unavailable.") : [makeGmailPlugin()]) });
      expect(await screen.findByRole("alert")).toHaveTextContent("Plugins are unavailable.");
      fail = false;
      await user.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByRole("region", { name: "Gmail" })).toBeInTheDocument();
    });
  });
});
