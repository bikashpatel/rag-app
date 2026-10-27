import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import type { LoginRedirectState } from "../auth/loginRedirect";
import { makeMe } from "../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall } from "../test/http";
import { renderWithAuth } from "../test/render";
import LoginPage from "./LoginPage";

const signedOut = { "GET /auth/me": () => errorResponse(401, "Not authenticated") };

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

function loginCalls(calls: FetchCall[]): FetchCall[] {
  return calls.filter((call) => call.method === "POST" && call.path === "/auth/login");
}

describe("LoginPage", () => {
  it("shows the sign-in form with Sign in disabled until both fields are filled", async () => {
    stubFetch(signedOut);
    const user = userEvent.setup();
    await renderLoginPage();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome back to RAG Assistant");
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
