import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { forgetAuthConfig } from "../auth/AuthContext";
import { makeAuthConfig, makeUndoInfo } from "../test/auth";
import { errorResponse, jsonResponse, stubFetch, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import UndoEmailPage from "./UndoEmailPage";

const TOKEN = "q3Zx8LmP0aB2cD4eF6gHq3Zx8LmP0aB2cD4eF6gH123";

function routes(extra: Record<string, Route>): Record<string, Route> {
  return { "GET /auth/me": () => errorResponse(401, "Not authenticated"), "GET /auth/config": makeAuthConfig(), ...extra };
}

function codeError(status: number, detail: string, code: string): Response {
  return jsonResponse({ detail, code }, { status });
}

async function openLink(hash = `#t=${TOKEN}`) {
  window.history.replaceState(null, "", `/undo-email${hash}`);
  return renderWithAuth(<UndoEmailPage />, { route: "/undo-email" });
}

beforeEach(() => {
  forgetAuthConfig();
  window.history.replaceState(null, "", "/");
});

describe("UndoEmailPage", () => {
  it("puts the old address back on a click and says what happened", async () => {
    const calls = stubFetch(
      routes({
        "POST /auth/email/undo-check": makeUndoInfo(),
        "POST /auth/email/undo": () => new Response(null, { status: 204 }),
      }),
    );
    const user = userEvent.setup();
    await openLink();

    expect(await screen.findByRole("heading", { name: "Undo the email change?" })).toBeInTheDocument();
    expect(screen.getByText(/back as the email of your account\?/)).toHaveTextContent("Put maya@example.com back");
    expect(screen.getByText("The new address was m***@example.org.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo the change" }));

    expect(await screen.findByRole("heading", { name: "Your email is back" })).toBeInTheDocument();
    expect(screen.getByText(/We signed out every session and removed the password./)).toHaveTextContent(
      "Check maya@example.com for a link to choose a new one.",
    );
    expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/login");
    expect(calls.find((call) => call.path === "/auth/email/undo")?.body).toEqual({ token: TOKEN });
  });

  it("says an address taken meanwhile is taken, with the contact address", async () => {
    stubFetch(
      routes({
        "POST /auth/email/undo-check": makeUndoInfo(),
        "POST /auth/email/undo": () => codeError(409, "That address is now used by another account.", "email_taken"),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.click(await screen.findByRole("button", { name: "Undo the change" }));
    expect(await screen.findByRole("heading", { name: "That address is taken" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contact us" })).toHaveAttribute("href", "mailto:support@asknuvexusdoc.com");
  });

  it("says an expired link has expired, on opening or on undoing", async () => {
    stubFetch(routes({ "POST /auth/email/undo-check": () => codeError(400, "Expired.", "link_invalid") }));
    const view = await openLink();
    expect(await screen.findByRole("heading", { name: "This link has expired" })).toBeInTheDocument();
    expect(screen.getByText(/Undo links work once, for 7 days./)).toBeInTheDocument();
    view.unmount();

    stubFetch(
      routes({
        "POST /auth/email/undo-check": makeUndoInfo(),
        "POST /auth/email/undo": () => codeError(400, "Expired.", "link_invalid"),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.click(await screen.findByRole("button", { name: "Undo the change" }));
    expect(await screen.findByRole("heading", { name: "This link has expired" })).toBeInTheDocument();
  });

  it("shows other refusals above the button, a missing token, and Try again", async () => {
    let up = false;
    stubFetch(
      routes({
        "POST /auth/email/undo-check": () => (up ? jsonResponse(makeUndoInfo()) : errorResponse(502, "Bad gateway")),
        "POST /auth/email/undo": () => codeError(429, "Too many attempts.", "too_many_attempts"),
      }),
    );
    const user = userEvent.setup();
    const view = await openLink();
    expect(await screen.findByRole("heading", { name: "We couldn't check your link" })).toBeInTheDocument();
    up = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await user.click(await screen.findByRole("button", { name: "Undo the change" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
    view.unmount();

    await openLink("");
    expect(screen.getByRole("heading", { name: "This link is incomplete" })).toBeInTheDocument();
  });
});
