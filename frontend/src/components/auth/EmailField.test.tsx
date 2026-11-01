import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import EmailField from "./EmailField";

function Controlled({ initial = "", error = null, hint }: { initial?: string; error?: string | null; hint?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <EmailField id="email" label="Email" value={value} onChange={setValue} error={error} hint={hint} />
      <button type="button">Next</button>
    </>
  );
}

describe("EmailField", () => {
  it("is a labelled email input for password managers and phone keyboards", () => {
    render(<EmailField id="email" label="Email" value="" onChange={() => {}} autoFocus />);
    const input = screen.getByRole("textbox", { name: "Email" });
    expect(input).toHaveAttribute("type", "email");
    expect(input).toHaveAttribute("autocomplete", "email");
    expect(input).toHaveAttribute("name", "email");
    expect(input).toHaveAttribute("autocapitalize", "none");
    expect(input).toHaveFocus();
    expect(input).not.toHaveAttribute("aria-describedby");
  });

  it("suggests a common domain when the field is left with a typo, and applies it on a tap", async () => {
    const user = userEvent.setup();
    render(<Controlled />);
    const input = screen.getByRole("textbox", { name: "Email" });
    await user.type(input, "maya@gmial.com");
    expect(screen.queryByText(/Did you mean/)).not.toBeInTheDocument(); // only once the field is left

    await user.click(screen.getByRole("button", { name: "Next" }));
    const suggestion = screen.getByRole("status");
    expect(suggestion).toHaveTextContent("Did you mean maya@gmail.com?");
    expect(input).toHaveAccessibleDescription(/^Did you mean maya@gmail\.com ?\?$/);
    expect(input).not.toHaveAttribute("aria-invalid");

    await user.click(screen.getByRole("button", { name: "maya@gmail.com" }));
    expect(input).toHaveValue("maya@gmail.com");
    expect(input).toHaveFocus();
    expect(screen.queryByText(/Did you mean/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.queryByText(/Did you mean/)).not.toBeInTheDocument();
  });

  it("drops the suggestion as soon as the address is edited", async () => {
    const user = userEvent.setup();
    render(<Controlled initial="maya@hotmial.com" />);
    const input = screen.getByRole("textbox", { name: "Email" });
    await user.click(input);
    await user.tab();
    expect(screen.getByText(/Did you mean/)).toHaveTextContent("maya@hotmail.com");
    await user.type(input, "x");
    expect(screen.queryByText(/Did you mean/)).not.toBeInTheDocument();
  });

  it("says nothing for an address that looks right", async () => {
    const user = userEvent.setup();
    render(<Controlled initial="maya@studio-nine.in" />);
    await user.click(screen.getByRole("textbox"));
    await user.tab();
    expect(screen.queryByText(/Did you mean/)).not.toBeInTheDocument();
  });

  it("shows the hint and the error, linked to the input, the suggestion above the error", async () => {
    const user = userEvent.setup();
    render(
      <Controlled
        initial="maya@yaho.com"
        error="Enter a valid email address."
        hint="No email on your account? Ask the administrator."
      />,
    );
    const input = screen.getByRole("textbox", { name: "Email" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid email address.");
    expect(input).toHaveAccessibleDescription(
      "No email on your account? Ask the administrator. Enter a valid email address.",
    );
    await user.click(input);
    await user.tab();
    const suggestion = screen.getByText(/Did you mean/);
    expect(suggestion.compareDocumentPosition(screen.getByRole("alert")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(input).toHaveAccessibleDescription(
      /^Did you mean maya@yahoo\.com ?\? No email on your account\? Ask the administrator\. Enter a valid email address\.$/,
    );
  });
});
