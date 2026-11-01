import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthConfig } from "../api/types";
import { forgetAuthConfig } from "../auth/AuthContext";
import type { TurnstileApi } from "../components/auth/Turnstile";
import { turnstileAllowed } from "../lib/authDocument";
import { makeAuthConfig, makeClosedAuthConfig } from "../test/auth";
import { errorResponse, jsonResponse, stubFetch, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import ForgotPasswordPage from "./ForgotPasswordPage";

vi.mock("../lib/authDocument", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/authDocument")>()),
  turnstileAllowed: vi.fn(),
}));

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
    async solve(token = "XXXX.DUMMY.TOKEN.XXXX") {
      await waitFor(() => expect(widgets.length).toBeGreaterThan(0));
      act(() => widgets.at(-1)?.callback(token));
    },
  };
}

function routes(extra: Record<string, Route> = {}, config: AuthConfig = makeAuthConfig()): Record<string, Route> {
  return { "GET /auth/me": () => errorResponse(401, "Not authenticated"), "GET /auth/config": config, ...extra };
}

function codeError(status: number, detail: string, code: string): Response {
  return jsonResponse({ detail, code }, { status });
}

const render = () => renderWithAuth(<ForgotPasswordPage />, { route: "/forgot-password" });

beforeEach(() => {
  forgetAuthConfig();
  vi.mocked(turnstileAllowed).mockReturnValue(true);
});

describe("ForgotPasswordPage", () => {
  it("sends the reset link for the address, after the robot check, and says what happens next", async () => {
    const turnstile = installTurnstile();
    const calls = stubFetch(routes({ "POST /auth/password/forgot": () => jsonResponse({ status: "accepted" }, { status: 202 }) }));
    const user = userEvent.setup();
    await render();

    expect(await screen.findByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveAccessibleDescription(
      "No email on your account? Ask the administrator.",
    );
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/login");

    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(screen.getByText("Enter your email.")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "Email" }), "maya@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(screen.getByText("Checking you're not a robot…")).toBeInTheDocument();
    await turnstile.solve("TOKEN-1");

    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
    expect(
      screen.getByText(/we've emailed it a link to reset the password. The link works for 30 minutes./),
    ).toHaveTextContent("If an account uses maya@example.com");
    expect(screen.getByText(/Nothing arrived\? Check your spam folder/)).toBeInTheDocument();
    expect(calls.find((call) => call.path === "/auth/password/forgot")?.body).toEqual({
      email: "maya@example.com",
      turnstile_token: "TOKEN-1",
    });
    expect(turnstile.api.render.mock.calls[0][1]).toMatchObject({ action: "reset" });
  });

  it("says when password reset isn't available", async () => {
    stubFetch(routes({}, makeClosedAuthConfig()));
    await render();
    expect(await screen.findByRole("heading", { name: "Password reset isn't available" })).toBeInTheDocument();
    expect(screen.getByText("Password reset isn't available right now. Ask the administrator.")).toBeInTheDocument();
  });

  it("puts an invalid address under the field and other refusals above the button", async () => {
    const turnstile = installTurnstile();
    const replies = [
      errorResponse(422, "Enter a valid email address."),
      codeError(503, "We can't send emails right now.", "mail_unavailable"),
    ];
    stubFetch(routes({ "POST /auth/password/forgot": () => replies.shift() }));
    const user = userEvent.setup();
    await render();
    await turnstile.solve("TOKEN-1");
    await user.type(await screen.findByRole("textbox", { name: "Email" }), "maya@example");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveFocus();

    await turnstile.solve("TOKEN-2");
    await user.type(screen.getByRole("textbox", { name: "Email" }), ".com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByText("We can't send emails right now.")).toBeInTheDocument();
  });

  it("starts the check again after challenge_failed", async () => {
    const turnstile = installTurnstile();
    const replies = [
      codeError(400, "The check expired or failed. Try again.", "challenge_failed"),
      jsonResponse({ status: "accepted" }, { status: 202 }),
    ];
    stubFetch(routes({ "POST /auth/password/forgot": () => replies.shift() }));
    const user = userEvent.setup();
    await render();
    await turnstile.solve("TOKEN-1");
    await user.type(await screen.findByRole("textbox", { name: "Email" }), "maya@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    await waitFor(() => expect(turnstile.api.reset).toHaveBeenCalled());
    await turnstile.solve("TOKEN-2");
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument();
  });

  it("offers a retry when the configuration can't be loaded", async () => {
    stubFetch(routes({ "GET /auth/config": () => errorResponse(502, "Bad gateway") }));
    await render();
    expect(await screen.findByRole("heading", { name: "We couldn't check your link" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
