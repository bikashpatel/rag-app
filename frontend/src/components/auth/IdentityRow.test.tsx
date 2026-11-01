import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import IdentityRow from "./IdentityRow";

describe("IdentityRow", () => {
  it("shows an address with the mail tile and its sub-line", () => {
    const { container } = render(<IdentityRow kind="email" primary="maya@example.com" secondary="Confirmed by this link" />);
    expect(screen.getByText("maya@example.com")).toHaveClass("auth-identity-primary");
    expect(screen.getByText("Confirmed by this link")).toHaveClass("auth-identity-secondary");
    const tile = container.querySelector(".auth-identity-icon");
    expect(tile).toHaveAttribute("aria-hidden", "true");
    expect(tile?.querySelector("svg")).not.toBeNull();
    expect(container.querySelector(".auth-google-g")).toBeNull();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows a username with the avatar", () => {
    const { container } = render(<IdentityRow kind="user" primary="maya.chen" secondary="maya@example.com" />);
    expect(container.querySelector(".ui-avatar")).toHaveTextContent("MC");
    expect(container.querySelector(".auth-identity-icon")).toBeNull();
  });

  it("shows a Google account with the G, and no sub-line when there is none", () => {
    const { container } = render(<IdentityRow kind="google" primary="maya.chen@gmail.com" />);
    expect(container.querySelector(".auth-identity-icon .auth-google-g svg")).not.toBeNull();
    expect(container.querySelector(".auth-identity-secondary")).toBeNull();
    expect(container.querySelector(".auth-identity")).toHaveTextContent("maya.chen@gmail.com");
  });
});
