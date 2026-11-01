import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AtSignIcon, ClockIcon, LinkBrokenIcon, MailCheckIcon } from "../ui/icons";
import AuthCard from "./AuthCard";

describe("AuthCard", () => {
  it("lays out a form: the brand, the title and lede, the content, its links and the policy links", () => {
    render(
      <AuthCard
        title="Reset your password"
        lede="Enter the email address on your account."
        footer={<a href="/login">Back to sign in</a>}
      >
        <form className="auth-form" aria-label="Reset">
          <button type="submit" className="btn btn-primary auth-submit">
            Send reset link
          </button>
        </form>
      </AuthCard>,
    );
    const card = screen.getByRole("main");
    expect(card).toHaveClass("auth-card");
    expect(card).not.toHaveClass("auth-result");
    const title = within(card).getByRole("heading", { level: 1, name: "Reset your password" });
    expect(title).toHaveAttribute("tabindex", "-1");
    expect(screen.getByText("Enter the email address on your account.")).toHaveClass("auth-lede");
    expect(within(card).getByRole("form", { name: "Reset" })).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Back to sign in" }).parentElement).toHaveClass("auth-links");
    expect(card.querySelector(".auth-mark")).toBeNull();

    // The brand is a full page load to sign-in; the foot links the policies.
    expect(screen.getByRole("link", { name: "AskNuvexusDoc, sign in" })).toHaveAttribute("href", "/login");
    const policies = screen.getByRole("navigation", { name: "Policies" });
    expect(within(policies).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Terms", "/terms"],
      ["Privacy", "/privacy"],
      ["Contact", "/contact"],
    ]);
  });

  it.each([
    ["accent", "auth-mark"],
    ["success", "auth-mark auth-mark-success"],
    ["warning", "auth-mark auth-mark-warning"],
    ["neutral", "auth-mark auth-mark-neutral"],
  ] as const)("lays out a %s result: the mark, the title and the text, centred", (tone, className) => {
    render(
      <AuthCard
        title="Check your email"
        lede={
          <>
            If an account uses <b>maya@example.com</b>, we've emailed it a link.
          </>
        }
        mark={{ icon: <MailCheckIcon />, tone }}
      >
        <div className="auth-actions">
          <button type="button">Back to sign in</button>
        </div>
      </AuthCard>,
    );
    const card = screen.getByRole("main");
    expect(card).toHaveClass("auth-card auth-result");
    const mark = card.querySelector(".auth-mark");
    expect(mark?.className).toBe(className);
    expect(mark).toHaveAttribute("aria-hidden", "true");
    expect(mark?.querySelector("svg")).not.toBeNull();
    expect(screen.getByText(/we've emailed it a link/)).toHaveClass("auth-text");
    expect(card).not.toHaveAttribute("aria-busy");
    expect(card.querySelector(".auth-links")).toBeNull();
  });

  it("shows a spinner in a neutral mark while it checks a link", () => {
    render(<AuthCard title="Checking your link…" lede="This takes a moment." mark="busy" />);
    const card = screen.getByRole("main");
    expect(card).toHaveAttribute("aria-busy", "true");
    expect(card.querySelector(".auth-mark")).toHaveClass("auth-mark-neutral");
    expect(card.querySelector(".auth-mark-spinner")).not.toBeNull();
  });

  it("moves focus to the title when the page opens and whenever the title changes", () => {
    const { rerender } = render(<AuthCard title="Checking your link…" mark="busy" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();

    rerender(
      <AuthCard title="This link is incomplete" mark={{ icon: <LinkBrokenIcon />, tone: "warning" }}>
        <button type="button">Go to sign in</button>
      </AuthCard>,
    );
    screen.getByRole("button", { name: "Go to sign in" }).focus();
    rerender(
      <AuthCard title="This link is incomplete" mark={{ icon: <LinkBrokenIcon />, tone: "warning" }}>
        <button type="button">Go to sign in</button>
      </AuthCard>,
    );
    expect(screen.getByRole("button", { name: "Go to sign in" })).toHaveFocus(); // the same state: focus stays

    rerender(<AuthCard title="This link has expired" mark={{ icon: <ClockIcon />, tone: "warning" }} />);
    expect(screen.getByRole("heading", { level: 1, name: "This link has expired" })).toHaveFocus();
  });

  it("leaves focus in a field that took it as the page opened", () => {
    const { rerender } = render(
      <AuthCard title="Confirm your email" mark={{ icon: <AtSignIcon />, tone: "accent" }}>
        <input aria-label="Email" autoFocus />
      </AuthCard>,
    );
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveFocus();
    rerender(<AuthCard title="Your email is confirmed" mark={{ icon: <AtSignIcon />, tone: "success" }} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
  });
});
