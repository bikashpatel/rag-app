import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountInfo, AuthConfig } from "../../api/types";
import { forgetAuthConfig } from "../../auth/AuthContext";
import { makeAuthConfig, makeClosedAuthConfig, makeGoogleLink, makePendingEmailInfo } from "../../test/auth";
import { makeAccount, makeAdminMe, makeLoginEvent, makeMe } from "../../test/fixtures";
import { deferred, errorResponse, jsonResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import AccountTab from "./AccountTab";

const noContent = () => new Response(null, { status: 204 });
const accepted = () => jsonResponse({ status: "accepted" }, { status: 202 });

type Options = { config?: AuthConfig; route?: string };

async function renderAccount(
  account: AccountInfo = makeAccount(),
  overrides: Record<string, Route> = {},
  { config = makeAuthConfig(), route = "/?settings=account" }: Options = {},
) {
  const calls = stubFetch({
    "GET /auth/me": account.is_admin ? makeAdminMe() : makeMe(),
    "GET /auth/config": config,
    "GET /me/account": account,
    "POST /auth/change-password": noContent,
    "POST /auth/logout-others": noContent,
    "POST /auth/logout": noContent,
    ...overrides,
  });
  const view = await renderWithAuth(<AccountTab onClose={() => {}} />, { route });
  await screen.findByText("Recent sign-ins");
  return { calls, view };
}

function codeError(status: number, detail: string, code: string, reason?: string): Response {
  return jsonResponse({ detail, code, reason }, { status });
}

const signIns = () => screen.getByRole("list", { name: "Recent sign-ins" });
const methods = () => screen.getByRole("list", { name: "Sign-in methods" });

/** A self-signed-up account with a confirmed email, a password and Google connected. */
function emailAccount(overrides: Partial<AccountInfo> = {}): AccountInfo {
  return makeAccount({
    username: "user-k3m7q2xa",
    email: "maya@example.com",
    email_verified_at: "2026-10-01T10:00:00Z",
    signup_source: "email",
    google: makeGoogleLink(),
    ...overrides,
  });
}

function posted(calls: FetchCall[], path: string): unknown[] {
  return calls.filter((call) => call.method === "POST" && call.path === path).map((call) => call.body);
}

beforeEach(() => {
  forgetAuthConfig();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AccountTab", () => {
  it("shows the profile and the recent sign-ins, each with how it signed in", async () => {
    await renderAccount();
    expect(screen.getAllByText("maya.chen").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Member").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { level: 3, name: "Sign-in methods" }).previousElementSibling).toHaveClass(
      "mark mark-security",
    );
    expect(screen.getByText("Your account was created by the administrator")).toBeInTheDocument();
    expect(screen.getByText("Also works for sign-in")).toBeInTheDocument();
    expect(screen.getByText("No email")).toBeInTheDocument();
    const logins = within(signIns()).getAllByRole("listitem");
    expect(logins).toHaveLength(2);
    expect(logins[0]).toHaveTextContent("Firefox 144 on macOS");
    expect(logins[0]).toHaveTextContent("with password · 203.0.113.x");
    expect(logins[0]).toHaveTextContent("This device");
    expect(logins[0]).toHaveTextContent("Today, 08:00");
    expect(logins[1]).toHaveTextContent("Safari 26 on iOS");
    expect(logins[1]).toHaveTextContent("18 Oct, 19:30");
    expect(within(logins[1]).getByText(/18 Oct 2026, 19:30/)).toHaveClass("sr-only");
    expect(logins[1]).not.toHaveTextContent("This device");
  });

  it("names the person with the email under it, says how they signed up and that the email is confirmed", async () => {
    await renderAccount(
      emailAccount({ recent_logins: [makeLoginEvent({ method: "google" })], signup_source: "google" }),
    );
    expect(screen.getByText("maya", { selector: ".settings-profile-name" })).toBeInTheDocument();
    expect(screen.getByText("maya@example.com")).toHaveClass("settings-profile-email");
    expect(screen.getByText("You signed up with Google")).toBeInTheDocument();
    expect(screen.getByText("user-k3m7q2xa")).toBeInTheDocument();
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
    expect(screen.getByText("since 1 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText(/Firefox 144 on macOS · desktop · with Google · 203.0.113.x/)).toBeInTheDocument();
    expect(within(signIns()).getByRole("listitem")).toHaveTextContent("with Google · 203.0.113.x");
    // The address appears once, under the name (the pending one only in its notice).
    expect(screen.getAllByText("maya@example.com")).toHaveLength(1);
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
    const logins = within(signIns()).getAllByRole("listitem");
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
    const list = signIns();
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
    expect(screen.queryByRole("list", { name: "Recent sign-ins" })).not.toBeInTheDocument();
  });

  it("points the env admin to .env instead of offering an email, Google or a password change", async () => {
    await renderAccount(
      makeAccount({
        id: 1,
        username: "admin",
        is_admin: true,
        password_managed_by_env: true,
        signup_source: "env",
        email_change_needs: "not_allowed",
      }),
    );
    expect(screen.getAllByText("Administrator").length).toBeGreaterThan(0);
    expect(screen.getByText("The administrator account is set in the server's settings")).toBeInTheDocument();
    expect(screen.getByText("The admin account is managed in .env.")).toBeInTheDocument();
    expect(screen.getByText("ADMIN_PASSWORD")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Password" }).previousElementSibling).toHaveClass(
      "mark mark-security",
    );
    expect(screen.queryByText("Also works for sign-in")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change password…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /email…/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Sign-in methods" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add name…" })).toBeInTheDocument();
  });

  it("reports a failed load and loads again on Try again", async () => {
    const user = userEvent.setup();
    let fail = true;
    stubFetch({
      "GET /auth/me": makeMe(),
      "GET /auth/config": makeAuthConfig(),
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

  describe("Name", () => {
    const named = (calls: FetchCall[]) => calls.filter((call) => call.method === "PUT" && call.path === "/me/name");
    const gets = (calls: FetchCall[], path: string) => calls.filter((call) => call.method === "GET" && call.path === path);

    it("adds a name inline, checks it as the server does, saves it and updates the account and the greeting", async () => {
      const user = userEvent.setup();
      let account = makeAccount();
      const { calls } = await renderAccount(account, {
        "GET /me/account": () => account,
        "PUT /me/name": (call) => {
          account = makeAccount({ display_name: (call.body as { name: string }).name });
          return { display_name: account.display_name };
        },
      });
      const group = screen.getByRole("heading", { name: "Name" }).closest(".settings-group") as HTMLElement;
      expect(within(group).getByText("Not set")).toBeInTheDocument();
      const toggle = within(group).getByRole("button", { name: "Add name…" });
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      const form = screen.getByRole("form", { name: "Add your name" });
      const field = within(form).getByRole("textbox", { name: "Name" });
      expect(field).toHaveFocus();
      expect(field).toHaveAttribute("autocomplete", "name");

      await user.click(within(form).getByRole("button", { name: "Save" }));
      expect(within(form).getByRole("alert")).toHaveTextContent("Enter your name.");
      expect(field).toHaveFocus();
      await user.type(field, "  Maya   Chen ");
      await user.click(within(form).getByRole("button", { name: "Save" }));

      expect(await within(group).findByRole("status")).toHaveTextContent("Name saved");
      expect(toggle).toHaveFocus();
      expect(toggle).toHaveTextContent("Edit name…");
      expect(within(group).getByText("Maya Chen")).toHaveClass("settings-name");
      expect(named(calls).map((call) => call.body)).toEqual([{ name: "Maya Chen" }]);
      await waitFor(() => expect(gets(calls, "/me/account")).toHaveLength(2));
      await waitFor(() => expect(gets(calls, "/auth/me")).toHaveLength(2));
      expect(await screen.findByText("Maya Chen", { selector: ".settings-profile-name" })).toBeInTheDocument();
    });

    it("edits a name, and closes without a request on Cancel or when nothing changed", async () => {
      const user = userEvent.setup();
      const { calls } = await renderAccount(makeAccount({ display_name: "Maya Chen" }));
      const toggle = screen.getByRole("button", { name: "Edit name…" });
      await user.click(toggle);
      const form = screen.getByRole("form", { name: "Change your name" });
      expect(within(form).getByRole("textbox", { name: "Name" })).toHaveValue("Maya Chen");
      await user.click(within(form).getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("form", { name: "Change your name" })).not.toBeInTheDocument();
      expect(toggle).toHaveFocus();

      await user.click(toggle);
      await user.type(screen.getByRole("textbox", { name: "Name" }), " ");
      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(screen.queryByRole("form", { name: "Change your name" })).not.toBeInTheDocument();
      expect(toggle).toHaveFocus();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(named(calls)).toEqual([]);
    });

    it("puts the server's refusal of the name on the field, and other refusals above the buttons", async () => {
      const user = userEvent.setup();
      const replies = [
        jsonResponse({ detail: [{ loc: ["body", "name"], msg: "Value error, Use at most 80 characters." }] }, { status: 422 }),
        jsonResponse({ detail: "Too many attempts. Try again in 40 minutes.", code: "too_many_attempts" }, { status: 429 }),
      ];
      await renderAccount(makeAccount({ display_name: "Maya" }), { "PUT /me/name": () => replies.shift() });
      await user.click(screen.getByRole("button", { name: "Edit name…" }));
      const field = screen.getByRole("textbox", { name: "Name" });
      await user.type(field, " Chen");
      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByText("Use at most 80 characters.")).toBeInTheDocument();
      expect(field).toHaveAttribute("aria-invalid", "true");
      expect(field).toHaveFocus();

      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByText("Too many attempts. Try again in 40 minutes.")).toBeInTheDocument();
      expect(field).not.toHaveAttribute("aria-invalid");
      expect(screen.getByRole("form", { name: "Change your name" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Save" })).not.toHaveAttribute("aria-busy");
    });
  });

  describe("Change password", () => {
    it("checks the new password against the rules before it can be sent", async () => {
      const user = userEvent.setup();
      const { calls } = await renderAccount();
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
      expect(next).toHaveAccessibleDescription(
        "At least 8 characters Not a common or leaked password, and not your email",
      );

      await user.type(current, "old-secret");
      await user.type(next, "short");
      await user.type(confirm, "shor");
      expect(confirm).toHaveAttribute("aria-invalid", "true");
      expect(confirm).toHaveAccessibleDescription("The passwords don't match.");
      expect(submit).toBeDisabled();

      await user.type(confirm, "t");
      expect(confirm).not.toHaveAttribute("aria-invalid");
      expect(submit).toBeEnabled();
      await user.click(submit);
      expect(within(form).getByText("Use at least 8 characters.")).toBeInTheDocument();
      expect(next).toHaveAttribute("aria-invalid", "true");
      expect(next).toHaveFocus();
      expect(posted(calls, "/auth/change-password")).toEqual([]);

      await user.type(next, "-but-long-now");
      expect(next).not.toHaveAttribute("aria-invalid");
      expect(within(form).getByText("At least 8 characters")).toBeInTheDocument();
    });

    it("shows the policy's refusal in place of the rule it broke", async () => {
      const user = userEvent.setup();
      await renderAccount(makeAccount(), {
        "POST /auth/change-password": () =>
          codeError(422, "This password is too common. Choose one that's harder to guess.", "password_rejected", "common"),
      });
      await user.click(screen.getByRole("button", { name: "Change password…" }));
      await user.type(screen.getByLabelText("Current password"), "old-secret");
      await user.type(screen.getByLabelText("New password"), "password1234");
      await user.type(screen.getByLabelText("Confirm new password"), "password1234");
      await user.click(screen.getByRole("button", { name: "Change password" }));
      expect(await screen.findByText("This password is too common. Choose one that's harder to guess.")).toBeInTheDocument();
      expect(screen.getByLabelText("New password")).toHaveFocus();
      expect(screen.queryByText("Not a common or leaked password, and not your email")).not.toBeInTheDocument();
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
      const { calls } = await renderAccount();
      await user.click(screen.getByRole("button", { name: "Change password…" }));
      await user.type(screen.getByLabelText("Current password"), "old-secret");
      await user.type(screen.getByLabelText("New password"), "correct-horse-battery");
      await user.type(screen.getByLabelText("Confirm new password"), "correct-horse-battery{Enter}");
      const result = await screen.findByRole("status");
      expect(result).toHaveTextContent("Your password was changed. Your other sessions were signed out.");
      await waitFor(() => expect(result).toHaveFocus());
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
      const { calls } = await renderAccount(makeAccount(), { "POST /auth/logout-others": () => response.promise });
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
      await waitFor(() => expect(result).toHaveFocus());
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
      const { calls } = await renderAccount();
      const others = screen.getByRole("button", { name: "Log out of all other sessions" });
      await user.click(others);
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("button", { name: "Log out others" })).not.toBeInTheDocument();
      expect(others).toHaveFocus();
      expect(posted(calls, "/auth/logout-others")).toHaveLength(0);
    });

    it("logs out of this session", async () => {
      const user = userEvent.setup();
      const { calls } = await renderAccount();
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

  describe("Email", () => {
    it("adds an email with the password, then shows it waiting for its confirmation", async () => {
      const user = userEvent.setup();
      let account = makeAccount();
      const { calls } = await renderAccount(account, {
        "GET /me/account": () => account,
        "POST /me/email": () => {
          account = makeAccount({ pending_email: makePendingEmailInfo({ sent_at: "2026-10-20T11:57:00Z" }) });
          return accepted();
        },
      });
      expect(
        screen.getByText("Add an email to sign in with it, reset your password and get receipts and plan reminders."),
      ).toBeInTheDocument();
      const toggle = screen.getByRole("button", { name: "Add email…" });
      await user.click(toggle);
      const form = screen.getByRole("form", { name: "Add email" });
      const address = within(form).getByRole("textbox", { name: "Email address" });
      expect(address).toHaveFocus();
      expect(address).toHaveAccessibleDescription(
        "We'll email a link to this address. It becomes your email when you open it while signed in.",
      );

      await user.click(within(form).getByRole("button", { name: "Send confirmation link" }));
      expect(within(form).getByText("Enter the new address.")).toBeInTheDocument();
      await user.type(address, "maya.chen@example.org");
      await user.click(within(form).getByRole("button", { name: "Send confirmation link" }));
      expect(within(form).getByText("Enter your password.")).toBeInTheDocument();
      await user.type(within(form).getByLabelText("Your password"), "correct horse");
      await user.keyboard("{Enter}");
      // Focus never drops to the page while the form closes: the toggle holds it, then the notice takes it.
      expect(toggle).toHaveFocus();

      const pending = await screen.findByText(/Waiting for you to confirm/);
      await waitFor(() => expect(pending.closest(".settings-pending")).toHaveFocus());
      expect(pending).toHaveTextContent("Waiting for you to confirm maya.chen@example.org");
      expect(pending).toHaveTextContent("We emailed a link 3 minutes ago. Open it while signed in here; it works for 24 hours.");
      expect(screen.queryByRole("form", { name: "Add email" })).not.toBeInTheDocument();
      expect(posted(calls, "/me/email")).toEqual([{ email: "maya.chen@example.org", password: "correct horse" }]);
    });

    it("puts a wrong password under its field and a refused address under the address", async () => {
      const user = userEvent.setup();
      const replies = [
        codeError(400, "Your password is incorrect.", "wrong_password"),
        errorResponse(422, "That's already your email."),
        codeError(429, "Too many changes today.", "too_many_attempts"),
      ];
      await renderAccount(emailAccount(), { "POST /me/email": () => replies.shift() });
      await user.click(screen.getByRole("button", { name: "Change email…" }));
      const form = screen.getByRole("form", { name: "Change email" });
      await user.type(within(form).getByRole("textbox", { name: "Email address" }), "maya@example.com");
      await user.type(within(form).getByLabelText("Your password"), "guess");
      await user.click(within(form).getByRole("button", { name: "Send confirmation link" }));
      expect(await within(form).findByText("Your password is incorrect.")).toBeInTheDocument();
      expect(within(form).getByLabelText("Your password")).toHaveFocus();

      await user.click(within(form).getByRole("button", { name: "Send confirmation link" }));
      expect(await within(form).findByText("That's already your email.")).toBeInTheDocument();
      expect(within(form).getByRole("textbox", { name: "Email address" })).toHaveFocus();

      await user.click(within(form).getByRole("button", { name: "Send confirmation link" }));
      expect(await within(form).findByText("Too many changes today.")).toBeInTheDocument();
      await user.click(within(form).getByRole("button", { name: "Cancel" }));
      expect(screen.getByRole("button", { name: "Change email…" })).toHaveFocus();
    });

    it("resends or cancels a pending address", async () => {
      const user = userEvent.setup();
      let account = emailAccount({ pending_email: makePendingEmailInfo({ sent_at: null }) });
      const { calls } = await renderAccount(account, {
        "GET /me/account": () => account,
        "POST /me/email/resend": accepted,
        "DELETE /me/email/pending": () => {
          account = emailAccount();
          return noContent();
        },
      });
      const pending = screen.getByText(/Waiting for you to confirm/);
      expect(pending).toHaveTextContent("We're sending a link.");
      expect(pending).toHaveTextContent("Until then your email stays maya@example.com.");
      await user.click(screen.getByRole("button", { name: "Resend" }));
      await waitFor(() => expect(posted(calls, "/me/email/resend")).toHaveLength(1));
      expect(await screen.findByText(/Sent again\./)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Resend" })).toHaveFocus();

      await user.click(screen.getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByText(/Waiting for you to confirm/)).not.toBeInTheDocument());
      expect(calls.some((call) => call.method === "DELETE" && call.path === "/me/email/pending")).toBe(true);
      expect(screen.getByRole("button", { name: "Change email…" })).toHaveFocus();
    });

    it("says when resending failed", async () => {
      const user = userEvent.setup();
      await renderAccount(emailAccount({ pending_email: makePendingEmailInfo() }), {
        "POST /me/email/resend": () => codeError(429, "Too many emails.", "too_many_attempts"),
      });
      await user.click(screen.getByRole("button", { name: "Resend" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Too many emails.");
    });

    it("without a password, asks for Google's confirmation first, coming back to the form", async () => {
      const user = userEvent.setup();
      const { calls } = await renderAccount(
        emailAccount({ has_password: false, email_change_needs: "google" }),
        { "POST /auth/google/start": () => errorResponse(503, "Google sign-in isn't set up.") },
        { route: "/plans?settings=account" },
      );
      await user.click(screen.getByRole("button", { name: "Change email…" }));
      expect(screen.getByText("For your safety, confirm it's you with Google before changing your email.")).toBeInTheDocument();
      expect(screen.queryByRole("form", { name: "Change email" })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Continue with Google" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Google sign-in isn't set up.");
      expect(calls.find((call) => call.path === "/auth/google/start")?.body).toEqual({
        intent: "reauth",
        next: "/plans?settings=account&change=email",
      });
    });

    it("back from the step-up, opens the form without a password and tidies the address bar", async () => {
      const user = userEvent.setup();
      const { calls, view } = await renderAccount(
        emailAccount({ has_password: false, email_change_needs: "google", stepup_until: "2026-10-20T12:04:00Z" }),
        { "POST /me/email": accepted },
        { route: "/?settings=account&change=email&google=reauth_ok" },
      );
      const form = screen.getByRole("form", { name: "Change email" });
      expect(within(form).getByText("Confirmed with Google · 5 minutes")).toBeInTheDocument();
      expect(within(form).queryByLabelText("Your password")).not.toBeInTheDocument();
      await waitFor(() => expect(view.location()).toBe("/?settings=account"));

      await user.type(within(form).getByRole("textbox", { name: "Email address" }), "maya.chen@example.org");
      await user.click(within(form).getByRole("button", { name: "Send confirmation link" }));
      await waitFor(() => expect(posted(calls, "/me/email")).toEqual([{ email: "maya.chen@example.org", password: null }]));
    });

    it("goes back to the step-up when the grant ran out", async () => {
      const user = userEvent.setup();
      await renderAccount(
        emailAccount({ has_password: false, email_change_needs: "google", stepup_until: "2026-10-20T12:04:00Z" }),
        { "POST /me/email": () => codeError(403, "Confirm it's you with Google first.", "reauth_required") },
      );
      await user.click(screen.getByRole("button", { name: "Change email…" }));
      await user.type(screen.getByRole("textbox", { name: "Email address" }), "maya.chen@example.org");
      await user.click(screen.getByRole("button", { name: "Send confirmation link" }));
      expect(
        await screen.findByText("For your safety, confirm it's you with Google before changing your email."),
      ).toBeInTheDocument();
    });

    it("explains when there is no way to re-authenticate, and stays out of the way without a mailer", async () => {
      const view = await renderAccount(emailAccount({ has_password: false, google: null, email_change_needs: "not_allowed" }));
      expect(screen.getByText(/set a password first with Forgot password/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Change email…" })).not.toBeInTheDocument();
      view.view.unmount();

      forgetAuthConfig();
      await renderAccount(makeAccount(), {}, { config: makeClosedAuthConfig() });
      expect(screen.queryByRole("button", { name: "Add email…" })).not.toBeInTheDocument();
      // Google sign-in isn't set up either: only the password is a way in.
      expect(within(methods()).getAllByRole("listitem")).toHaveLength(1);
    });
  });

  describe("Sign-in methods", () => {
    it("sets a first password after Google's step-up, without signing out", async () => {
      const user = userEvent.setup();
      let account = emailAccount({ has_password: false, email_change_needs: "google", stepup_until: "2026-10-20T12:04:00Z" });
      const { calls } = await renderAccount(account, {
        "GET /me/account": () => account,
        "POST /me/password/setup": () => {
          account = emailAccount();
          return noContent();
        },
      });
      const row = within(methods()).getAllByRole("listitem")[0];
      expect(row).toHaveTextContent("Not set. Add one to sign in without Google.");
      await user.click(within(row).getByRole("button", { name: "Set a password" }));
      const form = screen.getByRole("form", { name: "Set a password" });
      expect(within(form).getByLabelText("New password")).toHaveFocus();
      await user.type(within(form).getByLabelText("New password"), "a long passphrase");
      await user.type(within(form).getByLabelText("Repeat it"), "a long passphrase");
      await user.click(within(form).getByRole("button", { name: "Save password" }));

      expect(await screen.findByText("Password added. You can now sign in with your email and this password.")).toBeInTheDocument();
      expect(posted(calls, "/me/password/setup")).toEqual([{ new_password: "a long passphrase" }]);
      await waitFor(() => expect(within(methods()).getAllByRole("listitem")[0]).toHaveTextContent("Changing it signs you out"));
    });

    it("asks for Google first without a grant, and again when the server says it ran out", async () => {
      const user = userEvent.setup();
      const { calls } = await renderAccount(
        emailAccount({ has_password: false, email_change_needs: "google", stepup_until: "2026-10-20T11:00:00Z" }),
        { "POST /auth/google/start": () => errorResponse(503, "No.") },
      );
      await user.click(screen.getByRole("button", { name: "Set a password" }));
      expect(screen.getByText("For your safety, confirm it's you with Google before adding a password.")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Continue with Google" }));
      await waitFor(() =>
        expect(calls.find((call) => call.path === "/auth/google/start")?.body).toEqual({
          intent: "reauth",
          next: "/?settings=account&setup=password",
        }),
      );
    });

    it("opens the password form back from the step-up, and goes back to Google when refused", async () => {
      const user = userEvent.setup();
      await renderAccount(
        emailAccount({ has_password: false, email_change_needs: "google", stepup_until: "2026-10-20T12:04:00Z" }),
        { "POST /me/password/setup": () => codeError(403, "Confirm it's you with Google first.", "reauth_required") },
        { route: "/?settings=account&setup=password&google=reauth_ok" },
      );
      const form = screen.getByRole("form", { name: "Set a password" });
      await user.type(within(form).getByLabelText("New password"), "a long passphrase");
      await user.type(within(form).getByLabelText("Repeat it"), "a long passphrase");
      await user.click(within(form).getByRole("button", { name: "Save password" }));
      expect(
        await screen.findByText("For your safety, confirm it's you with Google before adding a password."),
      ).toBeInTheDocument();
    });

    it("disconnects Google with the password", async () => {
      const user = userEvent.setup();
      let account = emailAccount();
      const { calls } = await renderAccount(account, {
        "GET /me/account": () => account,
        "DELETE /me/google": () => {
          account = emailAccount({ google: null });
          return noContent();
        },
      });
      const google = within(methods()).getAllByRole("listitem")[1];
      expect(google).toHaveTextContent("Connected as maya.chen@gmail.com since 1 Oct 2026");
      await user.click(within(google).getByRole("button", { name: "Disconnect…" }));
      const form = screen.getByRole("form", { name: "Disconnect Google" });
      await user.click(within(form).getByRole("button", { name: "Disconnect Google" }));
      expect(within(form).getByText("Enter your password.")).toBeInTheDocument();
      await user.type(within(form).getByLabelText("Your password"), "correct horse");
      await user.click(within(form).getByRole("button", { name: "Disconnect Google" }));

      expect(await screen.findByText("Google sign-in is disconnected.")).toBeInTheDocument();
      expect(calls.find((call) => call.method === "DELETE" && call.path === "/me/google")?.body).toEqual({
        password: "correct horse",
      });
    });

    it("shows a refused disconnection, and cancels back", async () => {
      const user = userEvent.setup();
      await renderAccount(emailAccount(), {
        "DELETE /me/google": () => codeError(400, "Your password is incorrect.", "wrong_password"),
      });
      await user.click(screen.getByRole("button", { name: "Disconnect…" }));
      await user.type(screen.getByLabelText("Your password"), "guess");
      await user.click(screen.getByRole("button", { name: "Disconnect Google" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Your password is incorrect.");
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.getByRole("button", { name: "Disconnect…" })).toHaveFocus();
    });

    it.each([
      [{ has_password: false }, "Set a password first, so you can still sign in without Google."],
      [{ email: null }, "Confirm an email first, so you can still sign in without Google."],
    ])("can't disconnect the last way in (%o)", async (overrides, reason) => {
      await renderAccount(emailAccount(overrides));
      expect(screen.getByRole("button", { name: "Disconnect…" })).toBeDisabled();
      expect(screen.getByText(reason)).toBeInTheDocument();
    });

    it("connects Google after the password", async () => {
      const user = userEvent.setup();
      const { calls } = await renderAccount(makeAccount(), {
        "POST /auth/google/start": () => codeError(400, "Your password is incorrect.", "wrong_password"),
      });
      const google = within(methods()).getAllByRole("listitem")[1];
      expect(google).toHaveTextContent("Not connected. Sign in with your Google account too.");
      await user.click(within(google).getByRole("button", { name: "Connect Google…" }));
      expect(screen.getByText("Enter your password, then choose your Google account.")).toBeInTheDocument();
      const start = screen.getByRole("button", { name: "Continue with Google" });
      expect(start).toBeEnabled();
      await user.click(start);
      expect(screen.getByRole("alert")).toHaveTextContent("Enter your password.");
      expect(screen.getByLabelText("Your password")).toHaveFocus();
      expect(screen.getByLabelText("Your password")).toHaveAccessibleDescription("Enter your password.");
      expect(screen.getByLabelText("Your password")).toHaveAttribute("aria-invalid", "true");
      expect(calls.some((call) => call.path === "/auth/google/start")).toBe(false);
      await user.type(screen.getByLabelText("Your password"), "guess");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      await user.click(start);
      expect(await screen.findByRole("alert")).toHaveTextContent("Your password is incorrect.");
      expect(calls.find((call) => call.path === "/auth/google/start")?.body).toEqual({
        intent: "link",
        password: "guess",
      });
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.getByRole("button", { name: "Connect Google…" })).toHaveFocus();
    });

    it("offers no Connect for an account without a password", async () => {
      await renderAccount(makeAccount({ has_password: false, email_change_needs: "not_allowed" }));
      expect(screen.queryByRole("button", { name: "Connect Google…" })).not.toBeInTheDocument();
    });

    it.each([
      ["linked", "Google is connected."],
      ["in_use", "That Google account is already connected to another AskNuvexusDoc account."],
      ["already_linked", "A Google account is already connected to your account."],
      ["denied", "Google sign-in was cancelled."],
      ["error", "We couldn't sign you in with Google. Try again."],
      ["expired", "That took too long. Try again."],
      ["reauth_mismatch", "That's a different Google account from the one connected here."],
    ])("shows Google's ?google=%s result under the list", async (result, text) => {
      const { view } = await renderAccount(emailAccount(), {}, { route: `/?settings=account&google=${result}` });
      expect(screen.getByText(text)).toBeInTheDocument();
      await waitFor(() => expect(view.location()).toBe("/?settings=account"));
    });

    it("drops Google's result once the account changes: no \"connected\" after a disconnection", async () => {
      const user = userEvent.setup();
      let account = emailAccount();
      await renderAccount(
        account,
        {
          "GET /me/account": () => account,
          "DELETE /me/google": () => {
            account = emailAccount({ google: null });
            return noContent();
          },
        },
        { route: "/?settings=account&google=linked" },
      );
      expect(screen.getByText("Google is connected.")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Disconnect…" }));
      expect(screen.queryByText("Google is connected.")).not.toBeInTheDocument();
      await user.type(screen.getByLabelText("Your password"), "correct horse");
      await user.click(screen.getByRole("button", { name: "Disconnect Google" }));
      expect(await screen.findByText("Google sign-in is disconnected.")).toBeInTheDocument();
      expect(screen.queryByText("Google is connected.")).not.toBeInTheDocument();
    });

    it.each([
      ["Change email…", "/?settings=account&google=in_use"],
      ["Change password…", "/?settings=account&google=error"],
      ["Resend", "/?settings=account&google=denied"],
    ])("drops Google's result when %s starts something else", async (name, route) => {
      const user = userEvent.setup();
      await renderAccount(
        emailAccount({ pending_email: makePendingEmailInfo() }),
        { "POST /me/email/resend": accepted },
        { route },
      );
      const notice = within(screen.getByText("Sign-in methods").closest(".settings-group") as HTMLElement)
        .getAllByRole("status")
        .at(-1);
      expect(notice).toHaveClass("settings-result");
      await user.click(screen.getByRole("button", { name }));
      expect(notice).not.toBeInTheDocument();
    });
  });
});
