import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithRouter } from "../../test/render";
import PolicyLinks from "./PolicyLinks";

function links(): [string, string | null][] {
  const nav = screen.getByRole("navigation", { name: "Policies" });
  return within(nav)
    .getAllByRole("link")
    .map((link) => [link.textContent ?? "", link.getAttribute("href")]);
}

describe("PolicyLinks", () => {
  it("links every public page", () => {
    renderWithRouter(<PolicyLinks />);
    expect(links()).toEqual([
      ["Pricing", "/pricing"],
      ["Terms", "/terms"],
      ["Privacy", "/privacy"],
      ["Cancellation and refunds", "/refunds"],
      ["Contact", "/contact"],
      ["About", "/about"],
      ["Shipping", "/shipping"],
    ]);
  });

  it("shows the login page's five when compact, with the caller's class", () => {
    renderWithRouter(<PolicyLinks compact className="login-policies" />);
    expect(links().map(([label]) => label)).toEqual(["Pricing", "Terms", "Privacy", "Cancellation and refunds", "Contact"]);
    expect(screen.getByRole("navigation", { name: "Policies" })).toHaveClass("login-policies");
  });

  it("navigates inside the app", async () => {
    const view = renderWithRouter(<PolicyLinks />, { route: "/plans" });
    await userEvent.setup().click(screen.getByRole("link", { name: "Terms" }));
    expect(view.location()).toBe("/terms");
  });
});
