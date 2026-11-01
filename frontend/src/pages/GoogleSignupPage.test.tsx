import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { leaveForApp } from "../lib/authDocument";
import { makeGooglePending } from "../test/auth";
import { makeMe } from "../test/fixtures";
import { errorResponse, jsonResponse, stubFetch, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import { rememberGoogleNext } from "./authShared";
import GoogleSignupPage from "./GoogleSignupPage";

vi.mock("../lib/authDocument", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/authDocument")>()),
  leaveForApp: vi.fn(),
}));

const GOOGLE_URL = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y";

function codeError(status: number, detail: string, code: string): Response {
  return jsonResponse({ detail, code }, { status });
}

function routes(extra: Record<string, Route>): Record<string, Route> {
  return { "GET /auth/me": () => errorResponse(401, "Not authenticated"), ...extra };
}

function stubAssign() {
  const assign = vi.fn();
  vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign, hostname: "localhost" } as Location);
  return assign;
}

const render = () => renderWithAuth(<GoogleSignupPage />, { route: "/signup/google" });

describe("GoogleSignupPage", () => {
  it("creates the account once the terms are accepted, and leaves for the plans", async () => {
    const calls = stubFetch(
      routes({ "GET /auth/google/pending": makeGooglePending(), "POST /auth/google/finish": makeMe() }),
    );
    const user = userEvent.setup();
    await render();

    expect(await screen.findByRole("heading", { name: "Create your AskNuvexusDoc account" })).toBeInTheDocument();
    expect(screen.getByText("You're signing up with Google as:")).toBeInTheDocument();
    expect(screen.getByText("maya.chen@gmail.com")).toBeInTheDocument();
    expect(screen.getByText("Google account")).toBeInTheDocument();
    expect(screen.queryByText(/We'll email that address/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByText("Tick the box to accept the terms.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toHaveFocus();
    expect(calls.some((call) => call.path === "/auth/google/finish")).toBe(false);

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/plans"));
    expect(calls.find((call) => call.path === "/auth/google/finish")?.body).toEqual({ accept_terms: true });
  });

  it("lands on the page the sign-up started for", async () => {
    rememberGoogleNext("/plans/checkout/standard");
    stubFetch(routes({ "GET /auth/google/pending": makeGooglePending(), "POST /auth/google/finish": makeMe() }));
    const user = userEvent.setup();
    await render();
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(leaveForApp).toHaveBeenCalledWith("/plans/checkout/standard"));
  });

  it("says an unconfirmed address will be emailed", async () => {
    stubFetch(routes({ "GET /auth/google/pending": makeGooglePending({ email: "maya@studio-nine.in", email_verified: false }) }));
    await render();
    expect(
      await screen.findByText(
        "We'll email that address so you can confirm it. Until then, receipts and password resets can't use it.",
      ),
    ).toBeInTheDocument();
  });

  it("from the sign-in page, offers I already have an account, which drops the ticket", async () => {
    const assign = stubAssign();
    const calls = stubFetch(
      routes({
        "GET /auth/google/pending": makeGooglePending({ from: "login" }),
        "POST /auth/google/cancel": () => new Response(null, { status: 204 }),
      }),
    );
    const user = userEvent.setup();
    await render();

    expect(await screen.findByRole("heading", { name: "No AskNuvexusDoc account uses this address yet" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "I already have an account" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?google=use_password"));
    expect(calls.some((call) => call.path === "/auth/google/cancel")).toBe(true);
  });

  it("Cancel drops the ticket and goes to the sign-in page", async () => {
    const assign = stubAssign();
    stubFetch(
      routes({
        "GET /auth/google/pending": makeGooglePending(),
        "POST /auth/google/cancel": () => errorResponse(500, "down"),
      }),
    );
    const user = userEvent.setup();
    await render();
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  });

  it("Use a different Google account starts again, only towards Google", async () => {
    const assign = stubAssign();
    const replies: Response[] = [
      jsonResponse({ authorize_url: "https://evil.example/" }),
      jsonResponse({ authorize_url: GOOGLE_URL }),
    ];
    const calls = stubFetch(
      routes({ "GET /auth/google/pending": makeGooglePending(), "POST /auth/google/start": () => replies.shift() }),
    );
    const user = userEvent.setup();
    await render();

    await user.click(await screen.findByRole("button", { name: "Use a different Google account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("We couldn't sign you in with Google. Try again.");
    expect(assign).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Use a different Google account" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(GOOGLE_URL));
    expect(calls.find((call) => call.path === "/auth/google/start")?.body).toEqual({
      intent: "signin",
      from: "signup",
      next: null,
    });
  });

  it("says when the Google sign-up waited too long, with Continue with Google", async () => {
    stubFetch(routes({ "GET /auth/google/pending": () => codeError(400, "This link has expired or was already used.", "link_invalid") }));
    await render();
    expect(await screen.findByRole("heading", { name: "That took too long" })).toBeInTheDocument();
    expect(screen.getByText("For your safety, the Google sign-up waits 15 minutes at most.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/login");
  });

  it.each([
    ["email_taken", 409, "You already have an account"],
    ["signup_closed", 403, "Sign-ups are closed"],
    ["link_invalid", 400, "That took too long"],
  ])("turns %s on finishing into its result", async (code, status, title) => {
    stubFetch(
      routes({
        "GET /auth/google/pending": makeGooglePending(),
        "POST /auth/google/finish": () => codeError(status, "Refused.", code),
      }),
    );
    const user = userEvent.setup();
    await render();
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
  });

  it("shows another refusal above the button, and Try again when the ticket couldn't be checked", async () => {
    let pending: Route = () => errorResponse(502, "Bad gateway");
    stubFetch(
      routes({
        "GET /auth/google/pending": (call) => (typeof pending === "function" ? pending(call) : pending),
        "POST /auth/google/finish": () => codeError(429, "Too many attempts.", "too_many_attempts"),
      }),
    );
    const user = userEvent.setup();
    await render();
    expect(await screen.findByRole("heading", { name: "We couldn't check your link" })).toBeInTheDocument();
    pending = makeGooglePending();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
  });
});
