import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { SignupStatus } from "../../api/types";
import { makeShippedSignupStatus, makeSignupStatus } from "../../test/adminSignup";
import { deferred, errorResponse, jsonResponse, stubFetch } from "../../test/http";
import type { PageCheck } from "./policyPages";
import SignupSettingsModal from "./SignupSettingsModal";

const PUBLISHED: PageCheck[] = [
  { slug: "privacy", title: "Privacy policy", state: "final" },
  { slug: "terms", title: "Terms of service", state: "final" },
];
const DRAFTS: PageCheck[] = PUBLISHED.map((page) => ({ ...page, state: "draft" }));

type Props = ComponentProps<typeof SignupSettingsModal>;

function renderModal(overrides: Partial<Props> = {}) {
  const props: Props = {
    status: makeSignupStatus({ settings: { open: false, email: true, google: true } }),
    pages: PUBLISHED,
    onRecheckPages: vi.fn(),
    onClose: vi.fn(),
    onSaved: vi.fn(),
    ...overrides,
  };
  render(<SignupSettingsModal {...props} />);
  return props;
}

const openSwitch = () => screen.getByRole("switch", { name: "Open sign-up" });

/** The checks as "name: line", the sr-only state first. */
function checks(): string[] {
  const list = screen.getByRole("group", { name: "Checks" }).querySelector("ul") as HTMLElement;
  return within(list)
    .getAllByRole("listitem")
    .map((item) => (item.querySelector(".admin-check-text")?.textContent ?? "").trim());
}

describe("SignupSettingsModal", () => {
  it("shows the settings and saves them", async () => {
    const saved = makeSignupStatus({ settings: { open: true, email: false, google: true } });
    const calls = stubFetch({ "PUT /admin/signup-settings": saved });
    const user = userEvent.setup();
    const props = renderModal();

    expect(screen.getByRole("dialog", { name: "Sign-up" })).toBeInTheDocument();
    expect(openSwitch()).not.toBeChecked();
    expect(openSwitch()).toHaveFocus();
    expect(openSwitch()).toHaveAccessibleDescription(
      "Anyone can create an account with the methods below. New accounts have no plan until they pay. Closing it never affects existing accounts.",
    );
    expect(screen.getByRole("switch", { name: "Email and password" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Continue with Google" })).toBeChecked();

    await user.click(openSwitch());
    expect(openSwitch()).toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Email and password" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(saved));
    expect(calls.map((call) => [call.method, call.path, call.body])).toEqual([
      ["PUT", "/admin/signup-settings", { open: true, email: false, google: true }],
    ]);
  });

  it("asks for a second click before opening while something isn't ready", async () => {
    const calls = stubFetch({ "PUT /admin/signup-settings": makeSignupStatus() });
    const user = userEvent.setup();
    const props = renderModal({
      status: makeSignupStatus({
        settings: { open: false, email: true, google: true },
        warnings: ["real_ip", "admin_password_weak"],
        mail_webhook_configured: false,
      }),
      pages: DRAFTS,
    });

    await user.click(openSwitch());
    expect(openSwitch()).not.toBeChecked();
    const notice = screen.getByRole("alert");
    expect(within(notice).getByText("4 things aren't ready.")).toBeInTheDocument();
    expect(within(notice).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Visitor addresses aren't reaching the app: set NGINX_REAL_IP_FROM before opening sign-up.",
      "Bounce reports aren't set up: add Resend's webhook and MAIL_WEBHOOK_SECRET.",
      "The admin password is short or common: change ADMIN_PASSWORD in .env.",
      "The privacy policy and terms are still drafts.",
    ]);

    await user.click(within(notice).getByRole("button", { name: "Open anyway" }));
    expect(openSwitch()).toBeChecked();
    expect(openSwitch()).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled());
    expect(calls[0].body).toEqual({ open: true, email: true, google: true });
  });

  it("says one thing in the singular and keeps asking until Open anyway", async () => {
    const user = userEvent.setup();
    renderModal({ status: makeShippedSignupStatus({ mail_webhook_configured: true }) });

    await user.click(openSwitch());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "1 thing isn't ready.Online payments aren't live: new accounts can't buy a plan yet.Open anyway",
    );
    await user.click(openSwitch());
    expect(openSwitch()).not.toBeChecked();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("doesn't ask again for sign-up that is already open", async () => {
    const user = userEvent.setup();
    renderModal({ status: makeSignupStatus({ warnings: ["real_ip"] }) });

    expect(openSwitch()).toBeChecked();
    await user.click(openSwitch());
    await user.click(openSwitch());
    expect(openSwitch()).toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("disables a method whose settings are missing and names them", () => {
    renderModal({ status: makeShippedSignupStatus({ turnstile_configured: true }) });

    const email = screen.getByRole("switch", { name: "Email and password" });
    expect(email).toBeDisabled();
    expect(email).not.toBeChecked();
    expect(email).toHaveAccessibleDescription(
      "Not configured: set MAIL_SMTP_HOST and MAIL_FROM in .env, then restart the backend.",
    );
    const google = screen.getByRole("switch", { name: "Continue with Google" });
    expect(google).toBeDisabled();
    expect(google).toHaveAccessibleDescription(
      "Not configured: set GOOGLE_SIGNIN_CLIENT_ID and GOOGLE_SIGNIN_CLIENT_SECRET in .env, then restart the backend.",
    );
  });

  it("keeps a method's saved choice while its settings are missing", async () => {
    const calls = stubFetch({ "PUT /admin/signup-settings": makeShippedSignupStatus() });
    const user = userEvent.setup();
    renderModal({ status: makeShippedSignupStatus() });

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ open: false, email: true, google: true });
  });

  it("lists the checks when everything is ready", () => {
    renderModal();
    expect(checks()).toEqual([
      "Done: EmailConfigured · 12 of 80 sent today · none queued · no errors",
      "Done: Bounce reportsResend webhook set · 0 bounces today",
      "Done: TurnstileConfigured",
      "Done: Google sign-inConfigured",
      "Done: Visitor addressesVisitors' real addresses reach the app.",
      "Done: Online paymentsLive",
      "Done: Privacy policy and termsPublished",
      "Done: Admin passwordStrong enough",
    ]);
  });

  it("lists what isn't set up and what needs attention", () => {
    renderModal({
      status: makeShippedSignupStatus({ warnings: ["real_ip", "payments_not_live", "admin_password_weak"] }),
      pages: null,
    });
    expect(checks()).toEqual([
      "Not set up: EmailNot configured: set MAIL_SMTP_HOST and MAIL_FROM in .env, then restart the backend and worker.",
      "Warning: Bounce reportsNot set up: add the webhook and MAIL_WEBHOOK_SECRET.",
      "Not set up: TurnstileNot configured: set TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY in .env. Email sign-up and password resets need it.",
      "Not set up: Google sign-inNot configured. Google's setup steps are in the README.",
      "Warning: Visitor addressesVisitors' real addresses aren't reaching the app, so every per-visitor limit acts as one shared limit. Set NGINX_REAL_IP_FROM.",
      "Warning: Online paymentsNot live: new accounts can't pay yet.",
      "Not set up: Privacy policy and termsChecking the pages…",
      "Warning: Admin passwordShort or common: change ADMIN_PASSWORD in .env.",
    ]);
    expect(screen.queryByRole("button", { name: "Send a test email…" })).not.toBeInTheDocument();
  });

  it("reports mail errors, queued mail, complaints, a bounce pause and draft pages", async () => {
    const user = userEvent.setup();
    const props = renderModal({
      status: makeSignupStatus({
        mail_queued: 3,
        mail_last_error: "SMTP login refused at 09:12",
        bounces_today: 4,
        complaints_7d: 1,
        public_paused: true,
      }),
      pages: [
        { slug: "privacy", title: "Privacy policy", state: "draft" },
        { slug: "terms", title: "Terms of service", state: "missing" },
      ],
    });
    expect(checks().slice(0, 2)).toEqual([
      "Warning: EmailConfigured · 12 of 80 sent today · 3 queued · last error: SMTP login refused at 09:12",
      "Warning: Bounce reportsResend webhook set · 4 bounces today · 1 complaint in 7 days Sign-up emails are paused until 00:00 UTC: too many bounces today.",
    ]);
    expect(checks()[6]).toBe(
      "Warning: Privacy policy and termsThe privacy policy is still a draft. The terms of service aren't published. Publish the real pages before opening sign-up, then check again.",
    );
    await user.click(screen.getByRole("button", { name: "check again" }));
    expect(props.onRecheckPages).toHaveBeenCalledTimes(1);
  });

  it("sends a test email from the Email row", async () => {
    const reply = deferred<Response>();
    const calls = stubFetch({ "POST /admin/mail/test": () => reply.promise });
    const user = userEvent.setup();
    const props = renderModal();

    await user.click(screen.getByRole("button", { name: "Send a test email…" }));
    const address = screen.getByRole("textbox", { name: "Send a test email to" });
    expect(address).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Send a test email…" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter an email address.");
    expect(address).toHaveAttribute("aria-invalid", "true");
    expect(calls).toHaveLength(0);

    await user.type(address, " owner@example.com {Enter}");
    expect(screen.getByRole("button", { name: "Send" })).toHaveAttribute("aria-busy", "true");
    reply.resolve(jsonResponse({ status: "accepted" }, { status: 202 }));
    expect(
      await screen.findByText("Queued a test email to owner@example.com. It should arrive within a minute."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(calls.map((call) => [call.method, call.path, call.body])).toEqual([
      ["POST", "/admin/mail/test", { to: "owner@example.com" }],
    ]);
    expect(props.onSaved).not.toHaveBeenCalled();
  });

  it("shows why a test email wasn't sent, and returns to the button on Cancel", async () => {
    stubFetch({ "POST /admin/mail/test": () => errorResponse(429, "Too many attempts. Try again in 40 minutes.") });
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: "Send a test email…" }));
    await user.type(screen.getByRole("textbox", { name: "Send a test email to" }), "owner@example.com");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Try again in 40 minutes.");

    await user.click(screen.getByRole("button", { name: "Cancel the test email" }));
    expect(screen.queryByRole("textbox", { name: "Send a test email to" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send a test email…" })).toHaveFocus());
  });

  it("shows a failed save and allows another try", async () => {
    const replies: (Response | SignupStatus)[] = [errorResponse(503, "The database is starting."), makeSignupStatus()];
    stubFetch({ "PUT /admin/signup-settings": () => replies.shift() });
    const user = userEvent.setup();
    const props = renderModal();

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The database is starting.");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
  });

  it("disables both buttons while saving, and closes from Cancel", async () => {
    const pending = deferred<SignupStatus>();
    stubFetch({ "PUT /admin/signup-settings": () => pending.promise });
    const user = userEvent.setup();
    const props = renderModal();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    pending.resolve(makeSignupStatus());
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledTimes(1));
  });
});
