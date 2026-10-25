import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountInfo } from "../../api/types";
import { makeAccount, makeAdminMe, makeLoginEvent, makeMe } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import AccountTab from "./AccountTab";

const noContent = () => new Response(null, { status: 204 });

async function renderAccount(account: AccountInfo = makeAccount(), overrides: Record<string, Route> = {}) {
  const calls = stubFetch({
    "GET /auth/me": account.is_admin ? makeAdminMe() : makeMe(),
    "GET /me/account": account,
    "POST /auth/change-password": noContent,
    "POST /auth/logout-others": noContent,
    "POST /auth/logout": noContent,
    ...overrides,
  });
  await renderWithAuth(<AccountTab onClose={() => {}} />);
  await screen.findByText("Recent sign-ins");
  return calls;
}

function posted(calls: FetchCall[], path: string): unknown[] {
  return calls.filter((call) => call.method === "POST" && call.path === path).map((call) => call.body);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AccountTab", () => {
  it("shows the profile and the recent sign-ins", async () => {
    await renderAccount();
    expect(screen.getAllByText("maya.chen").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Member").length).toBeGreaterThan(0);
    expect(screen.getByText("Accounts are managed by your administrator")).toBeInTheDocument();
    const logins = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(logins).toHaveLength(2);
    expect(logins[0]).toHaveTextContent("Firefox 144 on macOS");
    expect(logins[0]).toHaveTextContent("203.0.113.x");
    expect(logins[0]).toHaveTextContent("This device");
    expect(logins[0]).toHaveTextContent("Today, 08:00");
    expect(logins[1]).toHaveTextContent("Safari 26 on iOS");
    expect(logins[1]).toHaveTextContent("18 Oct, 19:30");
    expect(within(logins[1]).getByText(/18 Oct 2026, 19:30/)).toHaveClass("sr-only");
    expect(logins[1]).not.toHaveTextContent("This device");
  });

  it("describes private and unknown addresses instead of showing them bare", async () => {
    await renderAccount(
      makeAccount({
        recent_logins: [
          makeLoginEvent({ ip: "172.18.0.x", ip_private: true }),
          makeLoginEvent({ id: 88, ip: null, ip_private: false, browser: null, os: null, current: false }),
        ],
      }),
    );
    const logins = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(logins[0]).toHaveTextContent("Via a local network or proxy (172.18.0.x)");
    expect(logins[1]).toHaveTextContent("Unknown browser");
    expect(logins[1]).toHaveTextContent("Unknown address");
  });

  it("shows five sign-ins until Show all", async () => {
    const user = userEvent.setup();
    const recent_logins = Array.from({ length: 7 }, (_, i) =>
      makeLoginEvent({ id: 100 - i, created_at: `2026-10-${String(19 - i).padStart(2, "0")}T10:00:00Z`, current: i === 0 }),
    );
    await renderAccount(makeAccount({ recent_logins }));
    const list = screen.getByRole("list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    const more = screen.getByRole("button", { name: "Show all (7)" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(within(list).getAllByRole("listitem")).toHaveLength(7);
    expect(screen.getByRole("button", { name: "Show fewer" })).toHaveAttribute("aria-expanded", "true");
  });

  it("says sign-ins are recorded from now on when there are none", async () => {
    await renderAccount(makeAccount({ recent_logins: [] }));
    expect(screen.getByText(/Sign-ins are recorded from now on\./)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("points the env admin to ADMIN_PASSWORD instead of offering a password change", async () => {
    await renderAccount(makeAccount({ id: 1, username: "admin", is_admin: true, password_managed_by_env: true }));
    expect(screen.getAllByText("Administrator").length).toBeGreaterThan(0);
    expect(screen.getByText("ADMIN_PASSWORD")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change password…" })).not.toBeInTheDocument();
  });

  it("reports a failed load and loads again on Try again", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubFetch({
      "GET /auth/me": makeMe(),
      "GET /me/account": () => (fail ? errorResponse(503, null) : makeAccount()),
    });
    await renderWithAuth(<AccountTab onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't load your account. The server ran into a problem. Please try again.",
    );
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Recent sign-ins")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("Change password", () => {
    it("validates the new password before it can be sent", async () => {
      const user = userEvent.setup();
      await renderAccount();
      const toggle = screen.getByRole("button", { name: "Change password…" });
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      const form = screen.getByRole("form", { name: "Change password" });
      const current = within(form).getByLabelText("Current password");
      const next = within(form).getByLabelText("New password");
      const confirm = within(form).getByLabelText("Confirm new password");
      const submit = within(form).getByRole("button", { name: "Change password" });
      expect(current).toHaveFocus();
      expect(submit).toBeDisabled();

      await user.type(current, "old-secret");
      await user.type(next, "short");
      expect(next).toHaveAttribute("aria-invalid", "true");
      expect(next).toHaveAccessibleDescription("At least 8 characters.");
      expect(submit).toBeDisabled();

      await user.type(next, "-but-long-now");
      expect(next).not.toHaveAttribute("aria-invalid");
      await user.type(confirm, "short-but-long");
      expect(confirm).toHaveAttribute("aria-invalid", "true");
      expect(confirm).toHaveAccessibleDescription("The passwords don't match.");
      expect(submit).toBeDisabled();

      await user.type(confirm, "-now");
      expect(confirm).not.toHaveAttribute("aria-invalid");
      expect(screen.queryByText("The passwords don't match.")).not.toBeInTheDocument();
      expect(submit).toBeEnabled();
    });

    it("shows and hides a password", async () => {
      const user = userEvent.setup();
      await renderAccount();
      await user.click(screen.getByRole("button", { name: "Change password…" }));
      const current = screen.getByLabelText("Current password");
      expect(current).toHaveAttribute("type", "password");
      await user.click(screen.getAllByRole("button", { name: "Show password" })[0]);
      expect(current).toHaveAttribute("type", "text");
      expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");
    });

    it("changes the password and confirms it", async () => {
      const user = userEvent.setup();
      const calls = await renderAccount();
      await user.click(screen.getByRole("button", { name: "Change password…" }));
      await user.type(screen.getByLabelText("Current password"), "old-secret");
      await user.type(screen.getByLabelText("New password"), "correct-horse-battery");
      await user.type(screen.getByLabelText("Confirm new password"), "correct-horse-battery{Enter}");
      const result = await screen.findByRole("status");
      expect(result).toHaveTextContent("Your password was changed. Your other sessions were signed out.");
      expect(result).toHaveFocus();
      expect(screen.queryByRole("form", { name: "Change password" })).not.toBeInTheDocument();
      expect(posted(calls, "/auth/change-password")).toEqual([
        { current_password: "old-secret", new_password: "correct-horse-battery" },
      ]);
    });

    it("sends the user back to a wrong current password", async () => {
      const user = userEvent.setup();
      await renderAccount(makeAccount(), {
        "POST /auth/change-password": errorResponse(400, "The current password is wrong."),
      });
      await user.click(screen.getByRole("button", { name: "Change password…" }));
      const current = screen.getByLabelText("Current password");
      await user.type(current, "guess");
      await user.type(screen.getByLabelText("New password"), "correct-horse-battery");
      await user.type(screen.getByLabelText("Confirm new password"), "correct-horse-battery");
      await user.click(screen.getByRole("button", { name: "Change password" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("The current password is wrong.");
      expect(current).toHaveAttribute("aria-invalid", "true");
      expect(current).toHaveAccessibleDescription("The current password is wrong.");
      expect(current).toHaveFocus();
      await user.type(current, "x");
      expect(current).not.toHaveAttribute("aria-invalid");
    });

    it("keeps the form open with the server's message on other failures", async () => {
      const user = userEvent.setup();
      await renderAccount(makeAccount(), {
        "POST /auth/change-password": errorResponse(429, "Too many attempts. Wait a minute."),
      });
      await user.click(screen.getByRole("button", { name: "Change password…" }));
      await user.type(screen.getByLabelText("Current password"), "old-secret");
      await user.type(screen.getByLabelText("New password"), "correct-horse-battery");
      await user.type(screen.getByLabelText("Confirm new password"), "correct-horse-battery");
      await user.click(screen.getByRole("button", { name: "Change password" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a minute.");
      expect(screen.getByLabelText("Current password")).not.toHaveAttribute("aria-invalid");
      expect(screen.getByRole("button", { name: "Change password" })).toBeEnabled();
    });

    it("cancels back to the toggle", async () => {
      const user = userEvent.setup();
      await renderAccount();
      const toggle = screen.getByRole("button", { name: "Change password…" });
      await user.click(toggle);
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("form", { name: "Change password" })).not.toBeInTheDocument();
      expect(toggle).toHaveFocus();
    });
  });

  describe("Sessions", () => {
    it("logs out the other sessions after a confirmation", async () => {
      const user = userEvent.setup();
      const response = deferred<Response>();
      const calls = await renderAccount(makeAccount(), { "POST /auth/logout-others": () => response.promise });
      const others = screen.getByRole("button", { name: "Log out of all other sessions" });
      await user.click(others);
      expect(others).toHaveAttribute("aria-expanded", "true");
      const confirm = within(screen.getByRole("group", { name: "Confirm logging out of other sessions" })).getByRole("button", {
        name: "Log out others",
      });
      expect(confirm).toHaveFocus();
      await user.click(confirm);
      expect(confirm).toHaveAttribute("aria-disabled", "true");
      expect(confirm).toHaveAttribute("aria-busy", "true");
      expect(confirm).toHaveAccessibleName("Log out others");
      expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
      response.resolve(noContent());
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Other sessions were signed out."));
      const result = screen.getByRole("status");
      expect(result).toHaveFocus();
      expect(screen.queryByRole("group", { name: "Confirm logging out of other sessions" })).not.toBeInTheDocument();
      expect(posted(calls, "/auth/logout-others")).toHaveLength(1);
    });

    it("keeps the confirmation open with the error when it fails", async () => {
      const user = userEvent.setup();
      await renderAccount(makeAccount(), { "POST /auth/logout-others": errorResponse(500, null) });
      await user.click(screen.getByRole("button", { name: "Log out of all other sessions" }));
      const confirm = screen.getByRole("button", { name: "Log out others" });
      await user.click(confirm);
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("The server ran into a problem. Please try again.");
      expect(confirm).toHaveAccessibleDescription("The server ran into a problem. Please try again.");
      expect(confirm).toHaveFocus();
    });

    it("cancels the confirmation back to its button", async () => {
      const user = userEvent.setup();
      const calls = await renderAccount();
      const others = screen.getByRole("button", { name: "Log out of all other sessions" });
      await user.click(others);
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("button", { name: "Log out others" })).not.toBeInTheDocument();
      expect(others).toHaveFocus();
      expect(posted(calls, "/auth/logout-others")).toHaveLength(0);
    });

    it("logs out of this session", async () => {
      const user = userEvent.setup();
      const calls = await renderAccount();
      await user.click(screen.getByRole("button", { name: "Log out" }));
      await waitFor(() => expect(posted(calls, "/auth/logout")).toHaveLength(1));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("says so when the server couldn't end the session", async () => {
      const user = userEvent.setup();
      await renderAccount(makeAccount(), { "POST /auth/logout": errorResponse(502, null) });
      await user.click(screen.getByRole("button", { name: "Log out" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't sign out. Check your connection and try again.");
    });
  });
});
