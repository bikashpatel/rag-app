import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import type { LoginRedirectState } from "../auth/loginRedirect";
import { setMediaQueries } from "../test/browser";
import { makeMe } from "../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall } from "../test/http";
import { renderWithAuth } from "../test/render";
import LoginPage from "./LoginPage";

const signedOut = { "GET /auth/me": () => errorResponse(401, "Not authenticated") };
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
    username: screen.getByRole("textbox", { name: "Username" }),
    password: screen.getByLabelText("Password"),
    submit: screen.getByRole("button", { name: "Sign in" }),
  };
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
  it("shows the sign-in form with Sign in disabled until both fields are filled", async () => {
    stubFetch(signedOut);
    const user = userEvent.setup();
    await renderLoginPage();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome back to AskNuvexusDoc");
    const { username, password, submit } = fields();
    expect(username).toHaveFocus();
    expect(password).toHaveAttribute("type", "password");
    expect(submit).toBeDisabled();

    await user.type(username, "   ");
    await user.type(password, "hunter2-long");
    expect(submit).toBeDisabled();

    await user.clear(username);
    await user.type(username, "maya.chen");
    expect(submit).toBeEnabled();

    await user.clear(password);
    expect(submit).toBeDisabled();
  });

  it("puts the form before the copy and the tour, so the keyboard and screen readers reach it first", async () => {
    stubFetch(signedOut);
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
    stubFetch(signedOut);
    await renderLoginPage();

    const aside = screen.getByRole("complementary", { name: "About AskNuvexusDoc" });
    const [headline, hero, subheading, lead, ...pitch] = expectCopy();
    const tour = screen.getByRole("region", { name: "Product tour" });
    for (const part of [headline, hero, subheading, lead, ...pitch]) expect(aside).toContainElement(part);
    // The picture, the subheading and the first paragraph, then the tour, then the second paragraph and the close.
    expect(precedes(lead, tour) && precedes(tour, pitch[0])).toBe(true);

    // The large picture: both sizes to choose from, its box reserved before it loads, fetched first.
    expect(hero).toHaveAttribute("srcset", expect.stringMatching(/hero-800.*\.webp 800w, .*hero-1600.*\.webp 1600w$/));
    expect(hero).toHaveAttribute("sizes", "(min-width: 1280px) 560px, calc(52vw - 80px)");
    expect(hero).toHaveAttribute("width", "1600");
    expect(hero).toHaveAttribute("height", "900");
    expect(hero).toHaveAttribute("decoding", "async");
    expect(hero).toHaveAttribute("fetchpriority", "high");

    // One h1 (the form's), the headline as h2 and the tour's scene titles under it.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 2 })).toEqual([headline]);
    expect(within(tour).getByRole("heading", { level: 3 })).toHaveTextContent("Cited answers");

    // The old hero and points row are gone.
    expect(screen.queryByText(/Ask your documents/)).not.toBeInTheDocument();
    expect(screen.queryByText("Private to you")).not.toBeInTheDocument();
    expect(screen.queryByText("Files and images on request")).not.toBeInTheDocument();
  });

  it("on phones, leaves the tour out and shows the copy after the form, the picture placed above it", async () => {
    setMediaQueries((query) => query === PHONE);
    stubFetch(signedOut);
    await renderLoginPage();

    const main = screen.getByRole("main");
    const [headline, hero, ...rest] = expectCopy();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Product tour" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    for (const part of [headline, hero, ...rest]) expect(main).toContainElement(part);

    // The small picture only, its box reserved before it loads.
    expect(hero).toHaveAttribute("src", expect.stringMatching(/hero-800.*\.webp$/));
    expect(hero).not.toHaveAttribute("srcset");
    expect(hero).not.toHaveAttribute("fetchpriority");
    expect(hero).toHaveAttribute("width", "800");
    expect(hero).toHaveAttribute("height", "450");

    // The form comes first in the DOM (CSS shows the picture above it); the copy ends before the credit line.
    expect(precedes(fields().submit, headline)).toBe(true);
    expect(headline.parentElement).toHaveClass("login-mobile-hero");
    expect(headline.parentElement).toContainElement(hero);
    expect(precedes(rest[rest.length - 1], screen.getByText(/designed, developed/))).toBe(true);
    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "Welcome back to AskNuvexusDoc",
      HEADLINE,
    ]);
    expect(fields().username).toHaveFocus();

    act(() => setMediaQueries(() => false)); // the window widens
    expect(screen.getByRole("region", { name: "Product tour" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 2, name: HEADLINE })).toHaveLength(1);
    expect(screen.getAllByRole("img", { name: HERO_ALT })).toHaveLength(1);
    expect(screen.getByRole("complementary")).toContainElement(screen.getByRole("heading", { level: 2 }));
    expect(screen.getByRole("img", { name: HERO_ALT })).toHaveAttribute("fetchpriority", "high");
  });

  it("links the public pages Razorpay's review reads under the card", async () => {
    stubFetch(signedOut);
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

  it("toggles the password's visibility", async () => {
    stubFetch(signedOut);
    const user = userEvent.setup();
    await renderLoginPage();

    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(fields().password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide password" }));
    expect(fields().password).toHaveAttribute("type", "password");
  });

  it("signs in with the trimmed username and goes to the chat", async () => {
    const calls = stubFetch({ ...signedOut, "POST /auth/login": makeMe() });
    const user = userEvent.setup();
    const view = await renderLoginPage();

    const { username, password } = fields();
    await user.type(username, "  maya.chen ");
    await user.type(password, "correct horse");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(view.location()).toBe("/"));
    expect(loginCalls(calls).map((call) => call.body)).toEqual([{ username: "maya.chen", password: "correct horse" }]);
  });

  it("returns to the page in the redirect state after signing in", async () => {
    stubFetch({ ...signedOut, "POST /auth/login": makeMe() });
    const user = userEvent.setup();
    const state: LoginRedirectState = { from: { pathname: "/c/42", search: "?settings=usage", hash: "" } };
    const view = await renderLoginPage(state);

    await user.type(fields().username, "maya.chen");
    await user.type(fields().password, "correct horse");
    await user.click(fields().submit);

    await waitFor(() => expect(view.location()).toBe("/c/42?settings=usage"));
  });

  it("ignores a redirect state pointing off the app", async () => {
    stubFetch({ ...signedOut, "POST /auth/login": makeMe() });
    const user = userEvent.setup();
    const state: LoginRedirectState = { from: { pathname: "//evil.example", search: "", hash: "" } };
    const view = await renderLoginPage(state);

    await user.type(fields().username, "maya.chen");
    await user.type(fields().password, "correct horse");
    await user.click(fields().submit);

    await waitFor(() => expect(view.location()).toBe("/"));
  });

  it("disables Sign in and shows a spinner while signing in", async () => {
    const pending = deferred<Response>();
    stubFetch({ ...signedOut, "POST /auth/login": () => pending.promise });
    const user = userEvent.setup();
    await renderLoginPage();

    await user.type(fields().username, "maya.chen");
    await user.type(fields().password, "wrong password");
    await user.click(fields().submit);

    expect(fields().submit).toBeDisabled();
    expect(fields().submit).toHaveAttribute("aria-busy", "true");

    pending.resolve(errorResponse(401, "Incorrect username or password."));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect username or password.");
    expect(fields().submit).toBeEnabled();
    expect(fields().submit).not.toHaveAttribute("aria-busy");
  });

  it("shows a wrong password as an alert that describes both fields, and stays on the form", async () => {
    stubFetch({ ...signedOut, "POST /auth/login": () => errorResponse(401, "Incorrect username or password.") });
    const user = userEvent.setup();
    const view = await renderLoginPage();

    await user.type(fields().username, "maya.chen");
    await user.type(fields().password, "wrong password");
    await user.click(fields().submit);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Incorrect username or password.");
    expect(fields().username).toHaveAccessibleDescription("Incorrect username or password.");
    expect(fields().password).toHaveAccessibleDescription("Incorrect username or password.");
    expect(view.location()).toBe("/login");
  });

  it("clears the previous error when submitting again", async () => {
    const replies = [errorResponse(401, "Incorrect username or password."), makeMe()];
    stubFetch({ ...signedOut, "POST /auth/login": () => replies.shift() });
    const user = userEvent.setup();
    const view = await renderLoginPage();

    await user.type(fields().username, "maya.chen");
    await user.type(fields().password, "wrong password");
    await user.click(fields().submit);
    await screen.findByRole("alert");

    await user.click(fields().submit);
    await waitFor(() => expect(view.location()).toBe("/"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains when the server can't be reached", async () => {
    stubFetch({
      ...signedOut,
      "POST /auth/login": () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const user = userEvent.setup();
    await renderLoginPage();

    await user.type(fields().username, "maya.chen");
    await user.type(fields().password, "correct horse");
    await user.click(fields().submit);

    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server. Check that the app is running.");
  });

  it("shows a generic message for a server error", async () => {
    stubFetch({ ...signedOut, "POST /auth/login": () => new Response("Bad gateway", { status: 502 }) });
    const user = userEvent.setup();
    await renderLoginPage();

    await user.type(fields().username, "maya.chen");
    await user.type(fields().password, "correct horse");
    await user.click(fields().submit);

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
