import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthConfig } from "../api/types";
import { forgetAuthConfig } from "../auth/AuthContext";
import type { TurnstileApi } from "../components/auth/Turnstile";
import { stash, turnstileAllowed } from "../lib/authDocument";
import { makeAuthConfig, makeClosedAuthConfig } from "../test/auth";
import { setMediaQueries } from "../test/browser";
import { errorResponse, jsonResponse, stubFetch, type FetchCall, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import SignupPage from "./SignupPage";

vi.mock("../lib/authDocument", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/authDocument")>()),
  turnstileAllowed: vi.fn(),
}));

const SENT_KEY = "rag.auth.signupSent";
const ACCEPTED = () => jsonResponse({ status: "accepted" }, { status: 202 });

function routes(extra: Record<string, Route> = {}, config: AuthConfig = makeAuthConfig()): Record<string, Route> {
  return { "GET /auth/me": () => errorResponse(401, "Not authenticated"), "GET /auth/config": config, ...extra };
}

function codeError(status: number, detail: string, code: string, reason?: string): Response {
  return jsonResponse({ detail, code, reason }, { status });
}

type WidgetOptions = Parameters<TurnstileApi["render"]>[1];

function installTurnstile() {
  const widgets: WidgetOptions[] = [];
  const api = {
    render: vi.fn((_: HTMLElement, options: WidgetOptions) => {
      widgets.push(options);
      return `widget-${widgets.length}`;
    }),
    reset: vi.fn(),
    remove: vi.fn(),
  };
  vi.stubGlobal("turnstile", api);
  return {
    api,
    widget: () => widgets.at(-1),
    async solve(token = "XXXX.DUMMY.TOKEN.XXXX") {
      await waitFor(() => expect(widgets.length).toBeGreaterThan(0));
      act(() => widgets.at(-1)?.callback(token));
    },
  };
}

function posts(calls: FetchCall[], path: string): unknown[] {
  return calls.filter((call) => call.method === "POST" && call.path === path).map((call) => call.body);
}

async function renderSignup(route = "/signup") {
  const view = await renderWithAuth(<SignupPage />, { route });
  return view;
}

async function fillForm(email = "maya@example.com", password = "correct horse battery") {
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox", { name: "Email" }), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("checkbox", { name: /I agree to the Terms/ }));
  return user;
}

const createButton = () => screen.getByRole("button", { name: /^Create account/ });

beforeEach(() => {
  forgetAuthConfig();
  vi.mocked(turnstileAllowed).mockReturnValue(true);
});

describe("SignupPage: the form", () => {
  it("offers Google first, then email, password, the terms and the robot check", async () => {
    stubFetch(routes());
    await renderSignup();

    expect(await screen.findByRole("heading", { level: 1, name: "Create your account" })).toBeInTheDocument();
    expect(screen.getByText("Chat with your documents. Choose a plan after you sign up.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    const google = screen.getByRole("button", { name: "Continue with Google" });
    expect(screen.getByRole("separator", { name: "or sign up with email" })).toBeInTheDocument();
    const email = screen.getByRole("textbox", { name: "Email" });
    expect(google.compareDocumentPosition(email) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(email).toHaveAttribute("autocomplete", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByText("At least 8 characters")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /I agree to the Terms/ })).not.toBeChecked();
    expect(createButton()).toBeEnabled();
  });

  it("says what is missing on submit and focuses the first field with a problem", async () => {
    const calls = stubFetch(routes());
    const user = userEvent.setup();
    await renderSignup();
    await user.click(await screen.findByRole("button", { name: "Create account" }));

    expect(screen.getByText("Enter your email.")).toBeInTheDocument();
    expect(screen.getByText("Use at least 8 characters.")).toBeInTheDocument();
    expect(screen.getByText("Tick the box to accept the terms.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveFocus();

    await user.type(screen.getByRole("textbox", { name: "Email" }), "maya@");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(createButton());
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /I agree/ })).not.toHaveFocus();

    await user.type(screen.getByRole("textbox", { name: "Email" }), "example.com");
    await user.click(createButton());
    expect(screen.getByRole("checkbox", { name: /I agree/ })).toHaveFocus();
    expect(posts(calls, "/auth/signup")).toEqual([]);
  });

  it("waits for the robot check, sends the sign-up with ?next=, then shows Check your inbox", async () => {
    const turnstile = installTurnstile();
    const calls = stubFetch(routes({ "POST /auth/signup": ACCEPTED }));
    await renderSignup("/signup?next=/plans/checkout/standard");
    const user = await fillForm(" maya@example.com ");
    await user.click(createButton());

    expect(screen.getByText("Checking you're not a robot…")).toBeInTheDocument();
    expect(turnstile.widget()).toMatchObject({ action: "signup" });
    await turnstile.solve("TOKEN-1");

    const heading = await screen.findByRole("heading", { name: "Check your inbox" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(posts(calls, "/auth/signup")).toEqual([
      {
        email: "maya@example.com",
        password: "correct horse battery",
        accept_terms: true,
        turnstile_token: "TOKEN-1",
        next: "/plans/checkout/standard",
      },
    ]);
    expect(screen.getByText(/We sent an email to/)).toHaveTextContent("We sent an email to maya@example.com.");
    const steps = heading.parentElement?.querySelector("ol") as HTMLElement;
    expect(within(steps).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Open the link in it within 24 hours.",
      "In this browser that's all it takes. On another device, enter the password you just chose.",
      "Choose a plan to start chatting with your documents.",
    ]);
    expect(screen.getByRole("button", { name: /Resend in 1:00|Resend in 0:5\d/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(JSON.parse(sessionStorage.getItem(SENT_KEY) ?? "null")).toMatchObject({ address: "maya@example.com" });
    // Google keeps the page to land on, for a Google sign-up from here.
    expect(sessionStorage.getItem("rag.auth.googleNext")).toBe("/plans/checkout/standard");
  });

  it("when the check couldn't load, Create account keeps the warning and sends after Try again", async () => {
    const turnstile = installTurnstile();
    const calls = stubFetch(routes({ "POST /auth/signup": ACCEPTED }));
    await renderSignup();
    await waitFor(() => expect(turnstile.widget()).toBeDefined());
    act(() => turnstile.widget()?.["error-callback"]());
    const user = await fillForm();
    await user.click(createButton());

    expect(screen.queryByText("Checking you're not a robot…")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("The security check couldn't load.");
    const retry = screen.getByRole("button", { name: "Try again" });
    expect(retry).toHaveFocus();
    expect(posts(calls, "/auth/signup")).toEqual([]);

    await user.click(retry);
    await waitFor(() => expect(turnstile.api.render).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Checking you're not a robot…")).toBeInTheDocument();
    await turnstile.solve("TOKEN-1");
    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();
    expect(posts(calls, "/auth/signup")).toMatchObject([{ email: "maya@example.com", turnstile_token: "TOKEN-1" }]);
  });

  it("shows a refused password in place of the rule it broke, until the password is edited", async () => {
    void installTurnstile().solve();
    stubFetch(
      routes({
        "POST /auth/signup": () =>
          codeError(422, "This password has appeared in a data breach. Choose a different one.", "password_rejected", "breached"),
      }),
    );
    await renderSignup();
    const user = await fillForm();
    await user.click(createButton());

    const refusal = await screen.findByText("This password has appeared in a data breach. Choose a different one.");
    expect(refusal).toHaveAttribute("role", "alert");
    expect(screen.queryByText("Not a common or leaked password, and not your email")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Password")).toHaveFocus();

    await user.type(screen.getByLabelText("Password"), "!");
    expect(screen.getByText("Not a common or leaked password, and not your email")).toBeInTheDocument();
  });

  it("puts a refused address under the email field", async () => {
    void installTurnstile().solve();
    stubFetch(routes({ "POST /auth/signup": () => errorResponse(422, "Enter a valid email address.") }));
    await renderSignup();
    const user = await fillForm("maya@example.c0m");
    await user.click(createButton());
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveFocus();
  });

  it.each([
    ["mail_unavailable", 503, "We can't send emails right now. Try again later, or continue with Google.", true],
    ["too_many_attempts", 429, "Too many attempts. Try again in 40 minutes.", true],
    ["challenge_unavailable", 503, "We couldn't check that you're not a robot. Try again in a minute.", true],
    ["signup_closed", 403, "New sign-ups are closed right now.", false],
  ])("shows %s above the button, with Google under it when it helps", async (code, status, detail, google) => {
    void installTurnstile().solve();
    stubFetch(routes({ "POST /auth/signup": () => codeError(status, detail, code) }));
    await renderSignup();
    const user = await fillForm();
    await user.click(createButton());

    const alert = await screen.findByText(detail);
    const block = alert.closest(".auth-refusal") as HTMLElement;
    expect(within(block).queryAllByRole("button", { name: "Continue with Google" })).toHaveLength(google ? 1 : 0);
  });

  it("starts the check again after challenge_failed and sends once more", async () => {
    const turnstile = installTurnstile();
    const replies = [codeError(400, "The check expired or failed. Try again.", "challenge_failed"), ACCEPTED()];
    const calls = stubFetch(routes({ "POST /auth/signup": () => replies.shift() }));
    await renderSignup();
    await turnstile.solve("TOKEN-1");
    const user = await fillForm();
    await user.click(createButton());

    await waitFor(() => expect(turnstile.api.reset).toHaveBeenCalled());
    await turnstile.solve("TOKEN-2");
    await screen.findByRole("heading", { name: "Check your inbox" });
    expect(posts(calls, "/auth/signup").map((body) => (body as { turnstile_token: string }).turnstile_token)).toEqual([
      "TOKEN-1",
      "TOKEN-2",
    ]);
  });

  it("shows only Google when email sign-up is off", async () => {
    stubFetch(routes({}, makeAuthConfig({ signup: { open: true, email: false, google: true } })));
    await renderSignup();
    expect(await screen.findByRole("button", { name: "Continue with Google" })).toBeInTheDocument();
    expect(screen.queryByRole("separator")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Email" })).not.toBeInTheDocument();
  });

  it("shows only the email form when Google sign-up is off", async () => {
    stubFetch(routes({}, makeAuthConfig({ signup: { open: true, email: true, google: false } })));
    await renderSignup();
    expect(await screen.findByRole("textbox", { name: "Email" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue with Google" })).not.toBeInTheDocument();
  });

  it("says sign-ups are closed, with Sign in, and no picture on phones", async () => {
    setMediaQueries((query) => query === "(max-width: 799.98px)");
    stubFetch(routes({}, makeClosedAuthConfig()));
    await renderSignup();
    expect(await screen.findByText("New sign-ups are closed right now.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("textbox", { name: "Email" })).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /friendly robot/ })).not.toBeInTheDocument();
  });

  it("offers a retry when the configuration can't be loaded", async () => {
    let up = false;
    stubFetch(routes({ "GET /auth/config": () => (up ? makeAuthConfig() : errorResponse(502, "Bad gateway")) }));
    const user = userEvent.setup();
    await renderSignup();
    expect(await screen.findByText("Couldn't reach the server.")).toBeInTheDocument();
    up = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("textbox", { name: "Email" })).toBeInTheDocument();
  });
});

describe("SignupPage: Check your inbox", () => {
  const sent = (sends: number[], address = "maya@example.com") =>
    sessionStorage.setItem(SENT_KEY, JSON.stringify({ address, sends }));

  it("keeps the state through a reload, and resends with its own check when the minute is up", async () => {
    sent([Date.now() - 5 * 60_000]);
    const turnstile = installTurnstile();
    const calls = stubFetch(routes({ "POST /auth/signup/resend": ACCEPTED }));
    const user = userEvent.setup();
    await renderSignup();

    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Resend email" }));
    expect(screen.getByRole("button", { name: "Sending…" })).toHaveAttribute("aria-busy", "true");
    await turnstile.solve("TOKEN-R");
    expect(turnstile.widget()).toMatchObject({ action: "resend" });

    expect(await screen.findByText("Sent again")).toBeInTheDocument();
    expect(posts(calls, "/auth/signup/resend")).toEqual([{ email: "maya@example.com", turnstile_token: "TOKEN-R" }]);
    expect(screen.getByRole("button", { name: /^Resend in / })).toHaveAttribute("aria-disabled", "true");
    expect(JSON.parse(sessionStorage.getItem(SENT_KEY) ?? "null").sends).toHaveLength(2);
  });

  it("shows a refused resend under the row", async () => {
    sent([Date.now() - 5 * 60_000]);
    void installTurnstile().solve();
    stubFetch(
      routes({
        "POST /auth/signup/resend": () =>
          codeError(503, "We can't send emails right now. Try again later, or continue with Google.", "mail_unavailable"),
      }),
    );
    const user = userEvent.setup();
    await renderSignup();
    await user.click(await screen.findByRole("button", { name: "Resend email" }));
    expect(await screen.findByText(/We can't send emails right now/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resend email" })).toBeInTheDocument();
  });

  it("after the third mail in an hour, says what to do instead of offering Resend", async () => {
    const now = Date.now();
    sent([now - 50 * 60_000, now - 30 * 60_000, now - 2 * 60_000]);
    stubFetch(routes());
    await renderSignup();
    expect(
      await screen.findByText(/We've sent 3 emails. Check Spam and Gmail's Promotions and Updates tabs/),
    ).toHaveTextContent("use Continue with Google, or try again in an hour.");
    expect(screen.queryByRole("button", { name: /Resend/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeInTheDocument();
  });

  it("Start again goes back to the form with the address kept, and counts down for the same address", async () => {
    sent([Date.now() - 10_000]);
    stubFetch(routes());
    const user = userEvent.setup();
    await renderSignup();
    await user.click(await screen.findByRole("button", { name: "Start again" }));

    expect(await screen.findByRole("heading", { name: "Create your account" })).toHaveFocus();
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveValue("maya@example.com");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(createButton()).toHaveTextContent(/^Create account in 0:\d\d$/);
    expect(createButton()).toHaveAttribute("aria-disabled", "true");

    await user.type(screen.getByRole("textbox", { name: "Email" }), "x");
    expect(createButton()).toHaveTextContent(/^Create account$/);
  });

  it("opens on the sent state for the address carried from the sign-in hint, Resend ready", async () => {
    stash("email", "maya@example.com", 60_000);
    stubFetch(routes());
    await renderSignup("/signup?resend=1");
    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();
    expect(screen.getByText(/We sent an email to/)).toHaveTextContent("maya@example.com");
    expect(screen.getByRole("button", { name: "Resend email" })).not.toHaveAttribute("aria-disabled");
  });

  it("asks for the address when nothing was carried, then sends the link again", async () => {
    void installTurnstile().solve("TOKEN-R");
    const calls = stubFetch(routes({ "POST /auth/signup/resend": ACCEPTED }));
    const user = userEvent.setup();
    await renderSignup("/signup?resend=1");

    expect(await screen.findByRole("heading", { name: "Send the link again" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send the link again" }));
    expect(screen.getByText("Enter your email.")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Email" }), "maya@example.com");
    await user.click(screen.getByRole("button", { name: "Send the link again" }));

    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();
    expect(posts(calls, "/auth/signup/resend")).toEqual([{ email: "maya@example.com", turnstile_token: "TOKEN-R" }]);
  });

  it("shows a refused request to send the link again", async () => {
    void installTurnstile().solve();
    stubFetch(routes({ "POST /auth/signup/resend": () => codeError(429, "Too many attempts.", "too_many_attempts") }));
    const user = userEvent.setup();
    await renderSignup("/signup?resend=1");
    await user.type(await screen.findByRole("textbox", { name: "Email" }), "maya@example.com");
    await user.click(screen.getByRole("button", { name: "Send the link again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
  });
});
