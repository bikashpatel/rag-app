import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { Me } from "../api/types";
import { stash, take } from "../lib/authDocument";
import { makeEmailTokenInfo } from "../test/auth";
import { makeMe } from "../test/fixtures";
import { errorResponse, jsonResponse, stubFetch, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import ConfirmEmailPage from "./ConfirmEmailPage";

const TOKEN = "q3Zx8LmP0aB2cD4eF6gHq3Zx8LmP0aB2cD4eF6gH123";

function codeError(status: number, detail: string, code: string): Response {
  return jsonResponse({ detail, code }, { status });
}

async function openLink(hash = `#t=${TOKEN}`) {
  window.history.replaceState(null, "", `/confirm-email${hash}`);
  return renderWithAuth(<ConfirmEmailPage />, { route: "/confirm-email" });
}

function signedIn(me: Me | (() => Me | Response), extra: Record<string, Route>): Record<string, Route> {
  return { "GET /auth/me": me, ...extra };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("ConfirmEmailPage", () => {
  it("confirms the address for the signed-in account that asked, with a click", async () => {
    let me = makeMe();
    const calls = stubFetch(
      signedIn(() => me, {
        "POST /auth/email/check": makeEmailTokenInfo(),
        "POST /auth/email/verify": () => {
          me = makeMe({ email: "maya.chen@example.org" });
          return jsonResponse({ email: "maya.chen@example.org" });
        },
      }),
    );
    const user = userEvent.setup();
    const view = await openLink();

    expect(await screen.findByRole("heading", { name: "Confirm your email" })).toBeInTheDocument();
    expect(screen.getByText(/as the email of your account\?/)).toHaveTextContent("Use maya.chen@example.org as");
    expect(screen.getByRole("link", { name: "Not now" })).toHaveAttribute("href", "/");
    await user.click(screen.getByRole("button", { name: "Confirm this email" }));

    expect(await screen.findByRole("heading", { name: "Your email is confirmed" })).toBeInTheDocument();
    expect(document.querySelector(".auth-art-result svg.mark-all-set")).not.toBeNull();
    expect(screen.getByText(/is now the email of your account/)).toHaveTextContent("maya.chen@example.org");
    expect(calls.find((call) => call.path === "/auth/email/verify")?.body).toEqual({ token: TOKEN });
    await waitFor(() => expect(calls.filter((call) => call.path === "/auth/me")).toHaveLength(2));
    expect(take("confirm_token")).toBeNull();
    await user.click(screen.getByRole("link", { name: "Continue" }));
    expect(view.location()).toBe("/?settings=account");
  });

  it("signed out, sends the user to sign in and keeps the token in this tab", async () => {
    stubFetch({
      "GET /auth/me": () => errorResponse(401, "Not authenticated"),
      "POST /auth/email/check": makeEmailTokenInfo({ signed_in: false }),
    });
    await openLink();
    expect(await screen.findByRole("heading", { name: "Sign in to confirm" })).toBeInTheDocument();
    expect(screen.getByText(/Sign in to the account/)).toHaveTextContent(
      "Sign in to the account m***@example.com to confirm maya.chen@example.org.",
    );
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login?next=/confirm-email");
    expect(take("confirm_token")).toBe(TOKEN);
  });

  it("picks the kept token up again after signing in", async () => {
    stash("confirm_token", TOKEN, 60_000);
    const calls = stubFetch(signedIn(makeMe(), { "POST /auth/email/check": makeEmailTokenInfo() }));
    await openLink("");
    expect(await screen.findByRole("heading", { name: "Confirm your email" })).toBeInTheDocument();
    expect(calls.find((call) => call.path === "/auth/email/check")?.body).toEqual({ token: TOKEN });
  });

  it("signed in to another account, offers to sign out, then asks to sign in", async () => {
    let session = true;
    stubFetch({
      "GET /auth/me": () => (session ? makeMe({ username: "sam.ortiz" }) : errorResponse(401, "Not authenticated")),
      "POST /auth/email/check": makeEmailTokenInfo({ signed_in: false }),
      "POST /auth/logout": () => {
        session = false;
        return new Response(null, { status: 204 });
      },
    });
    const user = userEvent.setup();
    await openLink();
    expect(await screen.findByRole("heading", { name: "You're signed in to another account" })).toBeInTheDocument();
    expect(screen.getByText("Sign out, then sign in to m***@example.com to confirm this email.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to confirm" })).toBeInTheDocument();
  });

  it("says when signing out failed", async () => {
    stubFetch(
      signedIn(makeMe(), {
        "POST /auth/email/check": makeEmailTokenInfo({ signed_in: false }),
        "POST /auth/logout": () => errorResponse(503, "Unavailable"),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't sign out.");
  });

  it.each([
    ["link_invalid", 400, "This link has expired", "Ask for a new link in Settings → Account."],
    ["email_taken", 409, "That address is taken", "Another account already uses this address."],
  ])("turns %s on confirming into its result", async (code, status, title, text) => {
    stubFetch(
      signedIn(makeMe(), {
        "POST /auth/email/check": makeEmailTokenInfo(),
        "POST /auth/email/verify": () => codeError(status, "Refused.", code),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.click(await screen.findByRole("button", { name: "Confirm this email" }));
    expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("checks again after wrong_account, and shows other refusals", async () => {
    const replies = [
      codeError(403, "Sign in to the account that asked for this change.", "wrong_account"),
      codeError(429, "Too many attempts.", "too_many_attempts"),
    ];
    const checks = [makeEmailTokenInfo(), makeEmailTokenInfo()];
    const calls = stubFetch(
      signedIn(makeMe(), {
        "POST /auth/email/check": () => jsonResponse(checks.shift() ?? makeEmailTokenInfo()),
        "POST /auth/email/verify": () => replies.shift(),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.click(await screen.findByRole("button", { name: "Confirm this email" }));
    await waitFor(() => expect(calls.filter((call) => call.path === "/auth/email/check")).toHaveLength(2));
    await user.click(await screen.findByRole("button", { name: "Confirm this email" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
  });

  it("says an expired link has expired on opening, and a missing token makes it incomplete", async () => {
    stubFetch(signedIn(makeMe(), { "POST /auth/email/check": () => codeError(400, "Expired.", "link_invalid") }));
    const view = await openLink();
    expect(await screen.findByRole("heading", { name: "This link has expired" })).toBeInTheDocument();
    view.unmount();
    await openLink("");
    expect(screen.getByRole("heading", { name: "This link is incomplete" })).toBeInTheDocument();
  });

  it("offers Try again when the link couldn't be checked", async () => {
    stubFetch(signedIn(makeMe(), { "POST /auth/email/check": () => errorResponse(502, "Bad gateway") }));
    await openLink();
    expect(await screen.findByRole("heading", { name: "We couldn't check your link" })).toBeInTheDocument();
  });
});
