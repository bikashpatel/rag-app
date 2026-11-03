import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { leaveForApp, take } from "../lib/authDocument";
import { makeSignupCheck } from "../test/auth";
import { makeMe } from "../test/fixtures";
import { deferred, errorResponse, jsonResponse, stubFetch, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import VerifyEmailPage from "./VerifyEmailPage";

vi.mock("../lib/authDocument", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/authDocument")>()),
  leaveForApp: vi.fn(),
}));

const TOKEN = "q3Zx8LmP0aB2cD4eF6gHq3Zx8LmP0aB2cD4eF6gH123";

function codeError(status: number, detail: string, code: string): Response {
  return jsonResponse({ detail, code }, { status });
}

function routes(extra: Record<string, Route>): Record<string, Route> {
  return { "GET /auth/me": () => errorResponse(401, "Not authenticated"), ...extra };
}

async function openLink(hash = `#t=${TOKEN}`) {
  window.history.replaceState(null, "", `/verify-email${hash}`);
  return renderWithAuth(<VerifyEmailPage />, { route: "/verify-email" });
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("VerifyEmailPage", () => {
  it("checks the link, then finishes in one click in the browser that signed up", async () => {
    const check = deferred<Response>();
    const calls = stubFetch(
      routes({
        "POST /auth/signup/check": () => check.promise,
        "POST /auth/signup/complete": makeMe({ email: "maya@example.com" }),
      }),
    );
    const user = userEvent.setup();
    await openLink();

    expect(screen.getByRole("heading", { name: "Checking your link…" })).toBeInTheDocument();
    // The token leaves the address bar at once.
    expect(window.location.hash).toBe("");
    check.resolve(jsonResponse(makeSignupCheck({ next: "/plans/checkout/standard" })));

    expect(await screen.findByRole("heading", { name: "Finish creating your account" })).toBeInTheDocument();
    expect(screen.getByText("Your email is confirmed. Create your account to continue.")).toBeInTheDocument();
    expect(screen.getByText("maya@example.com")).toBeInTheDocument();
    expect(screen.getByText("Confirmed by this link")).toBeInTheDocument();
    expect(screen.queryByLabelText("The password you chose")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/plans/checkout/standard"));
    expect(calls.filter((call) => call.method === "POST").map((call) => [call.path, call.body])).toEqual([
      ["/auth/signup/check", { token: TOKEN }],
      ["/auth/signup/complete", { token: TOKEN, password: null }],
    ]);
  });

  it("asks for the chosen password on another device, and goes to the plans", async () => {
    const replies: Response[] = [
      codeError(400, "That isn't the password you chose when you signed up.", "wrong_password"),
      jsonResponse(makeMe()),
    ];
    const calls = stubFetch(
      routes({
        "POST /auth/signup/check": makeSignupCheck({ needs_password: true }),
        "POST /auth/signup/complete": () => replies.shift(),
      }),
    );
    const user = userEvent.setup();
    await openLink();

    expect(await screen.findByText("One last step: enter the password you chose when you signed up.")).toBeInTheDocument();
    const password = screen.getByLabelText("The password you chose");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(document.querySelector('input[autocomplete="username"]')).toHaveValue("maya@example.com");
    expect(screen.getByRole("link", { name: "Sign up again" })).toHaveAttribute("href", "/signup");

    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByText("Enter the password you chose when you signed up.")).toBeInTheDocument();

    await user.type(password, "not it");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("That isn't the password you chose when you signed up.")).toBeInTheDocument();
    expect(password).toHaveAttribute("aria-invalid", "true");

    await user.clear(password);
    await user.type(password, "correct horse");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/plans"));
    expect(calls.filter((call) => call.path === "/auth/signup/complete").map((call) => call.body)).toEqual([
      { token: TOKEN, password: "not it" },
      { token: TOKEN, password: "correct horse" },
    ]);
  });

  it("carries the address to the sign-up page with Sign up again", async () => {
    stubFetch(routes({ "POST /auth/signup/check": makeSignupCheck({ needs_password: true }) }));
    const user = userEvent.setup();
    await openLink();
    const again = await screen.findByRole("link", { name: "Sign up again" });
    again.addEventListener("click", (event) => event.preventDefault());
    await user.click(again);
    expect(take("email")).toBe("maya@example.com");
  });

  it("tells a signed-in visitor that finishing signs them out", async () => {
    const me = makeMe({ display_name: "Sam Lee", email: "sam@example.com" });
    stubFetch({ "GET /auth/me": me, "POST /auth/signup/check": makeSignupCheck() });
    await openLink();
    expect(await screen.findByText("Finishing signs you out of Sam Lee.")).toBeInTheDocument();
  });

  it.each([
    ["signup_reset", "Too many wrong passwords. Sign up again to choose a new one.", "Too many wrong passwords"],
    ["link_invalid", "This link has expired or was already used.", "This link has expired"],
    ["email_taken", "This address already has an account. Sign in instead.", "You already have an account"],
    ["signup_closed", "New sign-ups are closed right now.", "Sign-ups are closed"],
  ])("turns %s on completion into its result", async (code, detail, title) => {
    stubFetch(
      routes({
        "POST /auth/signup/check": makeSignupCheck(),
        "POST /auth/signup/complete": () => codeError(code === "signup_closed" ? 403 : 400, detail, code),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.click(await screen.findByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: title })).toHaveFocus();
    expect(screen.getAllByRole("link", { name: /Sign in|Sign up again/ }).length).toBeGreaterThan(0);
  });

  it("shows another refusal above the button", async () => {
    stubFetch(
      routes({
        "POST /auth/signup/check": makeSignupCheck(),
        "POST /auth/signup/complete": () => codeError(429, "Too many attempts. Try again in 40 minutes.", "too_many_attempts"),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.click(await screen.findByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Try again in 40 minutes.");
  });

  it("says an expired link has expired, with Sign up again and Sign in", async () => {
    stubFetch(routes({ "POST /auth/signup/check": () => codeError(400, "This link has expired or was already used.", "link_invalid") }));
    await openLink();
    expect(await screen.findByRole("heading", { name: "This link has expired" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign up again" })).toHaveAttribute("href", "/signup");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("says a link without its token is incomplete", async () => {
    stubFetch(routes({}));
    await openLink("");
    expect(screen.getByRole("heading", { name: "This link is incomplete" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/login");
  });

  it("offers Try again when the link couldn't be checked", async () => {
    let up = false;
    stubFetch(
      routes({
        "POST /auth/signup/check": () => (up ? jsonResponse(makeSignupCheck()) : errorResponse(429, "Too many attempts.")),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    expect(await screen.findByRole("heading", { name: "We couldn't check your link" })).toBeInTheDocument();
    expect(screen.getByText("Too many attempts.")).toBeInTheDocument();
    up = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Finish creating your account" })).toBeInTheDocument();
  });
});
