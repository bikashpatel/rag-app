import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { AuthConfig } from "../api/types";
import { forgetAuthConfig } from "../auth/AuthContext";
import type { LoginRedirectState } from "../auth/loginRedirect";
import type { TurnstileApi } from "../components/auth/Turnstile";
import { leaveForApp, take, turnstileAllowed } from "../lib/authDocument";
import { makeAuthConfig, makeClosedAuthConfig } from "../test/auth";
import { setMediaQueries } from "../test/browser";
import { makeMe } from "../test/fixtures";
import { deferred, errorResponse, jsonResponse, stubFetch, type FetchCall, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import LoginPage from "./LoginPage";

vi.mock("../lib/authDocument", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/authDocument")>()),
  turnstileAllowed: vi.fn(),
  leaveForApp: vi.fn(),
}));

const unauthorized = () => errorResponse(401, "Not authenticated");
const signedOut: Record<string, Route> = { "GET /auth/me": unauthorized, "GET /auth/config": makeAuthConfig() };
const WRONG = "Incorrect email, username or password.";

function routes(extra: Record<string, Route> = {}, config: AuthConfig = makeAuthConfig()): Record<string, Route> {
  return { ...signedOut, "GET /auth/config": config, ...extra };
}

function codeError(status: number, detail: string, code: string): Response {
  return jsonResponse({ detail, code }, { status });
}

type WidgetOptions = Parameters<TurnstileApi["render"]>[1];

/** Cloudflare's window.turnstile: the test hands out the tokens. */
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
    solve(token = "XXXX.DUMMY.TOKEN.XXXX") {
      const widget = widgets.at(-1);
      if (!widget) throw new Error("No widget");
      act(() => widget.callback(token));
    },
  };
}

beforeEach(() => {
  forgetAuthConfig();
  vi.mocked(turnstileAllowed).mockReturnValue(true);
});
const PHONE = "(max-width: 799.98px)";

// The owner's copy, verbatim (visual spec 6.28).
const HEADLINE = "Your documents. Your AI. Your answers.";
const SUBHEADING = "Unlock the intelligence hidden in your documents—without compromising confidentiality.";
const PARAGRAPH_1 =
  "Upload your contracts, internal reports, policies, and confidential handbooks. Ask questions in natural language, " +
  "explore complex information, and get clear, context-aware answers with references to the exact passages " +
  "behind them.";
const PARAGRAPH_2 =
  "No endless searching. No reading hundreds of pages. Just a smarter way to work with your knowledge, with privacy " +
  "and security at the heart of the experience.";
const CLOSING = "Your knowledge deserves a more private way to AI.";
const HERO_ALT =
  "AskNuvexusDoc.com: a friendly robot gathers your PDF, Word, Excel and PowerPoint files and answers a question " +
  "with page references";

function renderLoginPage(state?: LoginRedirectState) {
  return renderWithAuth(<LoginPage />, { route: { pathname: "/login", state } });
}

function fields() {
  return {
    username: screen.getByRole("textbox", { name: "Email or username" }),
    password: screen.getByLabelText("Password"),
    submit: screen.getByRole("button", { name: "Sign in" }),
  };
}

async function fillAndSubmit(identifier = "maya.chen", password = "correct horse") {
  const user = userEvent.setup();
  await user.type(fields().username, identifier);
  await user.type(fields().password, password);
  await user.click(fields().submit);
  return user;
}

/** A paragraph whose whole text is `text` (it may hold inline markup). */
function paragraph(text: string): HTMLElement {
  return screen.getByText((_, element) => element?.tagName === "P" && element.textContent === text);
}

/**
 * The owner's copy, checked word for word: the headline (visually hidden: the hero picture shows it), both paragraphs
 * (the second as items and a sentence) and the closing line. None of it is hidden from assistive technology. Returns
 * the parts in reading order, the picture after the headline.
 */
function expectCopy(): HTMLElement[] {
  const headline = screen.getByRole("heading", { level: 2, name: HEADLINE });
  expect(headline).toHaveClass("sr-only");
  const hero = screen.getByRole("img", { name: HERO_ALT });
  const items = ["No endless searching.", "No reading hundreds of pages."].map((text) => screen.getByText(text));
  expect(items.map((item) => item.tagName)).toEqual(["LI", "LI"]);
  const rest = paragraph(PARAGRAPH_2.slice(PARAGRAPH_2.indexOf("Just")));
  expect([...items, rest].map((part) => part.textContent).join(" ")).toBe(PARAGRAPH_2);
  const parts = [headline, hero, paragraph(SUBHEADING), paragraph(PARAGRAPH_1), ...items, rest, paragraph(CLOSING)];
  for (const part of parts) expect(part.closest('[aria-hidden="true"]')).toBeNull();
  parts.slice(1).forEach((part, i) => expect(precedes(parts[i], part)).toBe(true));
  return parts;
}

function precedes(first: Node, second: Node): boolean {
  return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function loginCalls(calls: FetchCall[]): FetchCall[] {
  return calls.filter((call) => call.method === "POST" && call.path === "/auth/login");
}

describe("LoginPage", () => {
  it("shows the sign-in form; Sign in stays enabled and says what is missing", async () => {
    const calls = stubFetch(routes());
    const user = userEvent.setup();
    await renderLoginPage();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome back to AskNuvexusDoc");
    const { username, password, submit } = fields();
    expect(username).toHaveFocus();
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(username).toHaveAttribute("inputmode", "email");
    expect(password).toHaveAttribute("type", "password");
    expect(submit).toBeEnabled();

    await user.click(submit);
    expect(screen.getByText("Enter your email or username.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(username).toHaveFocus();
    expect(username).toHaveAttribute("aria-invalid", "true");

    await user.type(username, "   ");
    await user.type(password, "hunter2-long");
    await user.click(submit);
    expect(screen.getByText("Enter your email or username.")).toBeInTheDocument();
    expect(screen.queryByText("Enter your password.")).not.toBeInTheDocument();
    expect(loginCalls(calls)).toHaveLength(0);
  });

  it("puts the form before the copy and the tour, so the keyboard and screen readers reach it first", async () => {
    stubFetch(routes());
    await renderLoginPage();

    const main = screen.getByRole("main");
    const aside = screen.getByRole("complementary", { name: "About AskNuvexusDoc" });
    const tour = screen.getByRole("region", { name: "Product tour" });
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(precedes(main, aside)).toBe(true);
    expect(main).not.toContainElement(aside);
    expect(aside).toContainElement(tour);
    expect(fields().username).toHaveFocus();
  });

  it("leads the aside with the hero picture and the owner's copy around the product tour", async () => {
    stubFetch(routes());
    await renderLoginPage();

    const aside = screen.getByRole("complementary", { name: "About AskNuvexusDoc" });
    const [headline, hero, subheading, lead, ...pitch] = expectCopy();
    const tour = screen.getByRole("region", { name: "Product tour" });
    for (const part of [headline, hero, subheading, lead, ...pitch]) expect(aside).toContainElement(part);
    expect(precedes(lead, tour) && precedes(tour, pitch[0])).toBe(true);

    expect(hero).toHaveAttribute("srcset", expect.stringMatching(/hero-800.*\.webp 800w, .*hero-1600.*\.webp 1600w$/));
    expect(hero).toHaveAttribute("sizes", "(min-width: 1280px) 560px, calc(52vw - 80px)");
    expect(hero).toHaveAttribute("width", "1600");
    expect(hero).toHaveAttribute("height", "900");
    expect(hero).toHaveAttribute("decoding", "async");
    expect(hero).toHaveAttribute("fetchpriority", "high");

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 2 })).toEqual([headline]);
    expect(within(tour).getByRole("heading", { level: 3 })).toHaveTextContent("Cited answers");
  });

  it("on phones, leaves the tour out and shows the copy after the form, the picture placed above it", async () => {
    setMediaQueries((query) => query === PHONE);
    stubFetch(routes());
    await renderLoginPage();
    await screen.findByRole("button", { name: "Continue with Google" });

    const main = screen.getByRole("main");
    const [headline, hero, ...rest] = expectCopy();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Product tour" })).not.toBeInTheDocument();
    for (const part of [headline, hero, ...rest]) expect(main).toContainElement(part);
    expect(hero).toHaveAttribute("src", expect.stringMatching(/hero-800.*\.webp$/));
    expect(hero).not.toHaveAttribute("srcset");

    expect(precedes(fields().submit, headline)).toBe(true);
    expect(headline.parentElement).toHaveClass("login-mobile-hero");
    expect(headline.parentElement).toContainElement(hero);
    expect(precedes(rest[rest.length - 1], screen.getByText(/designed, developed/))).toBe(true);
    expect(fields().username).toHaveFocus();

    act(() => setMediaQueries(() => false));
    expect(screen.getByRole("region", { name: "Product tour" })).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: HERO_ALT })).toHaveLength(1);
  });

  it("links the public pages Razorpay's review reads under the card", async () => {
    stubFetch(routes());
    await renderLoginPage();
    const policies = screen.getByRole("navigation", { name: "Policies" });
    expect(within(policies).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Pricing", "/pricing"],
      ["Terms", "/terms"],
      ["Privacy", "/privacy"],
      ["Cancellation and refunds", "/refunds"],
      ["Contact", "/contact"],
    ]);
  });

  it("with sign-up open: Create an account under the lede, Google first, Forgot password, no admin note", async () => {
    stubFetch(routes());
    await renderLoginPage();

    const create = await screen.findByRole("link", { name: "Create an account" });
    expect(create).toHaveAttribute("href", "/signup");
    expect(create.closest("p")).toHaveTextContent("New to AskNuvexusDoc? Create an account");
    const google = screen.getByRole("button", { name: "Continue with Google" });
    expect(precedes(create, google) && precedes(google, fields().username)).toBe(true);
    expect(screen.getByRole("separator", { name: "or" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute("href", "/forgot-password");
    expect(screen.queryByText(/Accounts are created by your administrator/)).not.toBeInTheDocument();
  });

  it("as shipped (sign-up closed, no Google or reset): today's card with its admin note", async () => {
    stubFetch(routes({}, makeClosedAuthConfig()));
    await renderLoginPage();

    expect(await screen.findByText(/Accounts are created by your administrator/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create an account" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue with Google" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Forgot password?" })).not.toBeInTheDocument();
  });

  it("keeps today's admin note when the configuration can't be loaded", async () => {
    stubFetch({ "GET /auth/me": unauthorized, "GET /auth/config": () => errorResponse(404, "Not Found") });
    await renderLoginPage();
    expect(await screen.findByText(/Accounts are created by your administrator/)).toBeInTheDocument();
  });

  it("toggles the password's visibility", async () => {
    stubFetch(routes());
    const user = userEvent.setup();
    await renderLoginPage();

    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(fields().password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide password" }));
    expect(fields().password).toHaveAttribute("type", "password");
  });

  it("signs in with the trimmed identifier and leaves for the chat with a full page load", async () => {
    const calls = stubFetch(routes({ "POST /auth/login": makeMe() }));
    await renderLoginPage();

    await fillAndSubmit("  maya@example.com ", "correct horse");

    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/"));
    expect(loginCalls(calls).map((call) => call.body)).toEqual([
      { identifier: "maya@example.com", password: "correct horse" },
    ]);
    // It stays busy until the page goes.
    expect(fields().submit).toHaveAttribute("aria-busy", "true");
  });

  it("returns to the page in the redirect state, or the one ?next= names", async () => {
    stubFetch(routes({ "POST /auth/login": makeMe() }));
    const state: LoginRedirectState = { from: { pathname: "/c/42", search: "?settings=usage", hash: "" } };
    const view = await renderLoginPage(state);
    await fillAndSubmit();
    await waitFor(() => expect(leaveForApp).toHaveBeenLastCalledWith("/c/42?settings=usage"));
    view.unmount();

    await renderWithAuth(<LoginPage />, { route: "/login?next=/confirm-email" });
    await fillAndSubmit();
    await waitFor(() => expect(leaveForApp).toHaveBeenLastCalledWith("/confirm-email"));
  });

  it("ignores a redirect state pointing off the app", async () => {
    stubFetch(routes({ "POST /auth/login": makeMe() }));
    const state: LoginRedirectState = { from: { pathname: "//evil.example", search: "", hash: "" } };
    await renderLoginPage(state);
    await fillAndSubmit();
    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/"));
  });

  it("shows a spinner while signing in", async () => {
    const pending = deferred<Response>();
    stubFetch(routes({ "POST /auth/login": () => pending.promise }));
    await renderLoginPage();
    await fillAndSubmit();

    expect(fields().submit).toHaveAttribute("aria-busy", "true");
    pending.resolve(errorResponse(401, WRONG));
    expect(await screen.findByRole("alert")).toHaveTextContent(WRONG);
    expect(fields().submit).not.toHaveAttribute("aria-busy");
  });

  it("shows a wrong password with the two hints, describing both fields, and stays on the form", async () => {
    stubFetch(routes({ "POST /auth/login": () => errorResponse(401, WRONG) }));
    const view = await renderLoginPage();
    await fillAndSubmit("maya@example.com", "wrong password");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(WRONG);
    expect(alert).toHaveTextContent("Just signed up? Open the link we emailed you, or send it again.");
    expect(alert).toHaveTextContent("Signed up with Google? Use Continue with Google.");
    expect(fields().username).toHaveAccessibleDescription(expect.stringContaining(WRONG));
    expect(fields().password).toHaveAccessibleDescription(expect.stringContaining(WRONG));
    expect(view.location()).toBe("/login");
    expect(leaveForApp).not.toHaveBeenCalled();

    // "send it again" carries the typed address to the sign-up page in this tab; the other hint points at Google.
    const resend = within(alert).getByRole("link", { name: "send it again" });
    expect(resend).toHaveAttribute("href", "/signup?resend=1");
    resend.addEventListener("click", (event) => event.preventDefault());
    const user = userEvent.setup();
    await user.click(resend);
    expect(take("email")).toBe("maya@example.com");
    await user.click(within(alert).getByRole("link", { name: "Continue with Google" }));
    expect(screen.getByRole("button", { name: "Continue with Google" })).toHaveFocus();
  });

  it("leaves the hints out when neither email sign-up nor Google is available, and carries no username", async () => {
    stubFetch(routes({ "POST /auth/login": () => errorResponse(401, WRONG) }, makeClosedAuthConfig()));
    await renderLoginPage();
    await fillAndSubmit("maya.chen", "wrong password");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(WRONG);
    expect(within(alert).queryByRole("list")).not.toBeInTheDocument();
  });

  it("clears the previous error when submitting again", async () => {
    const replies = [errorResponse(401, WRONG), makeMe()];
    stubFetch(routes({ "POST /auth/login": () => replies.shift() }));
    await renderLoginPage();
    const user = await fillAndSubmit("maya.chen", "wrong password");
    await screen.findByRole("alert");

    await user.click(fields().submit);
    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("asks for the robot check after challenge_required, then sends its token with the same values", async () => {
    const turnstile = installTurnstile();
    const replies = [codeError(401, "Confirm you're not a robot to continue.", "challenge_required"), makeMe()];
    const calls = stubFetch(routes({ "POST /auth/login": () => replies.shift() }));
    await renderLoginPage();
    const user = await fillAndSubmit("maya@example.com", "correct horse");

    const caption = await screen.findByText("Confirm you're not a robot, then sign in again.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Focus moves to the caption, so a screen reader says why nothing else happened.
    expect(caption.closest("p")).toHaveFocus();
    await waitFor(() => expect(turnstile.api.render).toHaveBeenCalledTimes(1));
    expect(turnstile.api.render.mock.calls[0][1]).toMatchObject({ action: "login", sitekey: "1x00000000000000000000AA" });
    expect(fields().username).toHaveValue("maya@example.com");

    // Pressed before the check is done: it says so, and the form goes when the token arrives.
    await user.click(fields().submit);
    expect(screen.getByText("Checking you're not a robot…")).toBeInTheDocument();
    expect(loginCalls(calls)).toHaveLength(1);
    turnstile.solve("TOKEN-1");
    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/"));
    expect(loginCalls(calls)[1].body).toEqual({
      identifier: "maya@example.com",
      password: "correct horse",
      turnstile_token: "TOKEN-1",
    });
  });

  it("starts the check again after challenge_failed and sends once more with the next token", async () => {
    const turnstile = installTurnstile();
    const replies = [
      codeError(401, "Confirm you're not a robot to continue.", "challenge_required"),
      codeError(400, "The check expired or failed. Try again.", "challenge_failed"),
      codeError(400, "The check expired or failed. Try again.", "challenge_failed"),
    ];
    const calls = stubFetch(routes({ "POST /auth/login": () => replies.shift() }));
    await renderLoginPage();
    const user = await fillAndSubmit();
    await waitFor(() => expect(turnstile.api.render).toHaveBeenCalled());
    turnstile.solve("TOKEN-1");
    await user.click(fields().submit);

    await waitFor(() => expect(turnstile.api.reset).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    turnstile.solve("TOKEN-2");
    expect(await screen.findByRole("alert")).toHaveTextContent("The check expired or failed. Try again.");
    expect(loginCalls(calls).map((call) => (call.body as { turnstile_token?: string }).turnstile_token)).toEqual([
      undefined,
      "TOKEN-1",
      "TOKEN-2",
    ]);
  });

  it.each([
    ["denied", "Google sign-in was cancelled."],
    ["expired", "That took too long. Try again."],
    ["error", "We couldn't sign you in with Google. Try again."],
    ["link_required", "An account already uses this email. Sign in with your password, then connect Google in Settings → Account."],
    ["use_password", "Sign in with your email or username, then connect Google in Settings → Account."],
    ["signup_closed", "New sign-ups are closed right now."],
    ["unavailable", "We couldn't sign you in. If you think this is a mistake, contact us."],
  ])("shows ?google=%s once, then drops the parameter", async (result, text) => {
    stubFetch(routes());
    const view = await renderWithAuth(<LoginPage />, { route: `/login?google=${result}&next=/plans` });
    expect(screen.getByRole("alert")).toHaveTextContent(text);
    await waitFor(() => expect(view.location()).toBe("/login?next=/plans"));
    expect(screen.getByRole("alert")).toHaveTextContent(text);
  });

  it("links the contact address in ?google=unavailable, and ignores unknown results", async () => {
    stubFetch(routes());
    const view = await renderWithAuth(<LoginPage />, { route: "/login?google=unavailable" });
    expect(await screen.findByRole("link", { name: "contact us" })).toHaveAttribute(
      "href",
      "mailto:support@asknuvexusdoc.com",
    );
    view.unmount();

    await renderWithAuth(<LoginPage />, { route: "/login?google=nonsense" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("starts Google sign-in from the sign-in page, and says when it can't", async () => {
    const calls = stubFetch(routes({ "POST /auth/google/start": () => errorResponse(503, "Google sign-in isn't set up.") }));
    const user = userEvent.setup();
    await renderWithAuth(<LoginPage />, { route: "/login?next=/plans" });
    await user.click(await screen.findByRole("button", { name: "Continue with Google" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Google sign-in isn't set up.");
    expect(calls.find((call) => call.path === "/auth/google/start")?.body).toEqual({
      intent: "signin",
      from: "login",
      next: "/plans",
    });
  });

  it("explains when the server can't be reached", async () => {
    stubFetch(
      routes({
        "POST /auth/login": () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    );
    await renderLoginPage();
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server. Check that the app is running.");
  });

  it("shows a generic message for a server error", async () => {
    stubFetch(routes({ "POST /auth/login": () => new Response("Bad gateway", { status: 502 }) }));
    await renderLoginPage();
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent("The server ran into a problem. Please try again.");
  });
});

describe("signing in from the app", () => {
  it("sends a signed-out visitor to the login form", async () => {
    stubFetch(signedOut);
    window.history.replaceState(null, "", "/c/42");
    render(<App />);

    expect(await screen.findByRole("heading", { name: /^Welcome back/ })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  it("offers a retry when the session can't be checked", async () => {
    const replies = [errorResponse(503, "Service unavailable"), errorResponse(401, "Not authenticated")];
    stubFetch({ "GET /auth/me": () => replies.shift() });
    window.history.replaceState(null, "", "/");
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Can't reach the server" })).toBeInTheDocument();
    expect(screen.getByText("Service unavailable")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: /^Welcome back/ })).toBeInTheDocument();
  });
});
