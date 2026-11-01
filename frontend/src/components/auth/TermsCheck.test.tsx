import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import TermsCheck from "./TermsCheck";

function Controlled({ error = null }: { error?: string | null }) {
  const [checked, setChecked] = useState(false);
  return <TermsCheck checked={checked} onChange={setChecked} error={error} />;
}

describe("TermsCheck", () => {
  it("starts unticked and ticks from anywhere on its label", async () => {
    const user = userEvent.setup();
    render(<Controlled />);
    const box = screen.getByRole("checkbox", { name: /^I agree to the Terms.* and the Privacy policy.*\.$/ });
    expect(box).not.toBeChecked();
    expect(box).toHaveAttribute("name", "accept_terms");
    await user.click(screen.getByText(/I agree to the/));
    expect(box).toBeChecked();
    await user.click(box);
    expect(box).not.toBeChecked();
  });

  it("links the terms and the policy in a new tab, saying so", async () => {
    const onChange = vi.fn();
    render(<TermsCheck checked={false} onChange={onChange} />);
    // Browsers keep the space before the hidden words; jsdom's name computation drops it.
    const terms = screen.getByRole("link", { name: /^Terms ?\(opens in a new tab\)$/ });
    const privacy = screen.getByRole("link", { name: /^Privacy policy ?\(opens in a new tab\)$/ });
    expect(terms).toHaveAttribute("href", "/terms");
    expect(privacy).toHaveAttribute("href", "/privacy");
    for (const link of [terms, privacy]) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noreferrer");
    }
  });

  it("shows its error under the box and links it", () => {
    render(<Controlled error="Tick the box to accept the terms." />);
    const box = screen.getByRole("checkbox");
    expect(screen.getByRole("alert")).toHaveTextContent("Tick the box to accept the terms.");
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(box).toHaveAccessibleDescription("Tick the box to accept the terms.");
  });

  it("can be focused through its form by name", () => {
    render(
      <form aria-label="Sign up">
        <TermsCheck checked={false} onChange={() => {}} />
      </form>,
    );
    const form = screen.getByRole("form", { name: "Sign up" }) as HTMLFormElement;
    (form.elements.namedItem("accept_terms") as HTMLInputElement).focus();
    expect(screen.getByRole("checkbox")).toHaveFocus();
  });
});
