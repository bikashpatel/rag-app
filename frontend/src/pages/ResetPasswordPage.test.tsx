import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { forgetAuthConfig } from "../auth/AuthContext";
import { makeAuthConfig, makePasswordTokenInfo } from "../test/auth";
import { errorResponse, jsonResponse, stubFetch, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import ResetPasswordPage from "./ResetPasswordPage";

const TOKEN = "q3Zx8LmP0aB2cD4eF6gHq3Zx8LmP0aB2cD4eF6gH123";

function routes(extra: Record<string, Route>): Record<string, Route> {
  return {
    "GET /auth/me": () => errorResponse(401, "Not authenticated"),
    "GET /auth/config": makeAuthConfig({ password_min_length: 10 }),
    ...extra,
  };
}

function codeError(status: number, detail: string, code: string, reason?: string): Response {
  return jsonResponse({ detail, code, reason }, { status });
}

async function openLink(hash = `#t=${TOKEN}`) {
  window.history.replaceState(null, "", `/reset-password${hash}`);
  return renderWithAuth(<ResetPasswordPage />, { route: "/reset-password" });
}

const field = (name: string) => screen.getByLabelText(name, { selector: "input" });

beforeEach(() => {
  forgetAuthConfig();
  window.history.replaceState(null, "", "/");
});

describe("ResetPasswordPage", () => {
  it("sets the new password, twice, and says every session ended", async () => {
    const calls = stubFetch(
      routes({
        "POST /auth/password/check": makePasswordTokenInfo(),
        "POST /auth/password/reset": () => new Response(null, { status: 204 }),
      }),
    );
    const user = userEvent.setup();
    await openLink();

    const heading = await screen.findByRole("heading", { name: "Choose a new password" });
    expect(heading.previousElementSibling).toHaveClass("auth-art");
    expect(heading.previousElementSibling?.querySelector("svg.mark-security")).not.toBeNull();
    expect(window.location.hash).toBe("");
    expect(screen.getByText("Saving it signs you out on every device.")).toBeInTheDocument();
    expect(screen.getByText("user-k3m7q2xa")).toBeInTheDocument();
    expect(await screen.findByText("At least 10 characters")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();

    await user.type(field("New password"), "short");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(screen.getByText("Use at least 10 characters.")).toBeInTheDocument();
    expect(field("New password")).toHaveFocus();

    await user.type(field("New password"), " and long enough");
    await user.type(field("Repeat it"), "different");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(screen.getByText("The passwords don't match.")).toBeInTheDocument();
    expect(field("Repeat it")).toHaveFocus();

    await user.clear(field("Repeat it"));
    await user.type(field("Repeat it"), "short and long enough");
    await user.click(screen.getByRole("button", { name: "Save password" }));

    expect(await screen.findByRole("heading", { name: "Password changed" })).toBeInTheDocument();
    expect(
      screen.getByText("Your password was changed and you were signed out everywhere. Sign in with the new one."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(calls.find((call) => call.path === "/auth/password/reset")?.body).toEqual({
      token: TOKEN,
      new_password: "short and long enough",
      disconnect_google: false,
    });
  });

  it("offers to disconnect Google, ticked when its address isn't the account's, and sets a first password", async () => {
    const calls = stubFetch(
      routes({
        "POST /auth/password/check": makePasswordTokenInfo({
          has_password: false,
          google: { email: "m***@gmail.com", suggest_disconnect: true },
        }),
        "POST /auth/password/reset": () => new Response(null, { status: 204 }),
      }),
    );
    const user = userEvent.setup();
    await openLink();

    expect(await screen.findByRole("heading", { name: "Set your password" })).toBeInTheDocument();
    const box = screen.getByRole("checkbox", { name: "Also disconnect Google sign-in (m***@gmail.com)" });
    expect(box).toBeChecked();
    expect(box).toHaveAccessibleDescription("Do this if someone else may have connected it.");
    await user.click(box);
    await user.type(field("New password"), "a long passphrase");
    await user.type(field("Repeat it"), "a long passphrase");
    await user.click(screen.getByRole("button", { name: "Save password" }));

    expect(await screen.findByRole("heading", { name: "Your password is set" })).toBeInTheDocument();
    expect(screen.getByText(/and your new password, or keep using Google./)).toHaveTextContent("maya@example.com");
    expect(calls.find((call) => call.path === "/auth/password/reset")?.body).toMatchObject({ disconnect_google: false });
  });

  it("shows the server's refusal in place of its rule, and an expired link on saving", async () => {
    const replies = [
      codeError(422, "This password is too common. Choose one that's harder to guess.", "password_rejected", "common"),
      codeError(400, "This link has expired or was already used.", "link_invalid"),
    ];
    stubFetch(routes({ "POST /auth/password/check": makePasswordTokenInfo(), "POST /auth/password/reset": () => replies.shift() }));
    const user = userEvent.setup();
    await openLink();
    await user.type(await screen.findByLabelText("New password", { selector: "input" }), "password1234");
    await user.type(field("Repeat it"), "password1234");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByText("This password is too common. Choose one that's harder to guess.")).toBeInTheDocument();
    expect(field("New password")).toHaveAttribute("aria-invalid", "true");

    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("heading", { name: "This link has expired" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ask for a new link" })).toHaveAttribute("href", "/forgot-password");
  });

  it("shows another refusal above the button", async () => {
    stubFetch(
      routes({
        "POST /auth/password/check": makePasswordTokenInfo(),
        "POST /auth/password/reset": () => codeError(429, "Too many attempts.", "too_many_attempts"),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    await user.type(await screen.findByLabelText("New password", { selector: "input" }), "a long passphrase");
    await user.type(field("Repeat it"), "a long passphrase");
    await user.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
  });

  it("says an expired link has expired, and a missing token makes the link incomplete", async () => {
    stubFetch(routes({ "POST /auth/password/check": () => codeError(400, "This link has expired or was already used.", "link_invalid") }));
    const view = await openLink();
    expect(await screen.findByRole("heading", { name: "This link has expired" })).toBeInTheDocument();
    expect(screen.getByText("Reset links work once, for 30 minutes. Ask for a new one.")).toBeInTheDocument();
    view.unmount();

    await openLink("");
    expect(screen.getByRole("heading", { name: "This link is incomplete" })).toBeInTheDocument();
  });

  it("offers Try again when the link couldn't be checked", async () => {
    let up = false;
    stubFetch(
      routes({
        "POST /auth/password/check": () => (up ? jsonResponse(makePasswordTokenInfo()) : errorResponse(502, "Bad gateway")),
      }),
    );
    const user = userEvent.setup();
    await openLink();
    expect(await screen.findByRole("heading", { name: "We couldn't check your link" })).toBeInTheDocument();
    up = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
  });
});
