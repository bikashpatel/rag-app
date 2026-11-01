import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { PasswordReason } from "../../api/types";
import PasswordInput from "../ui/PasswordInput";
import PasswordRules from "./PasswordRules";

const BREACHED = "This password has appeared in a data breach. Choose a different one.";

function rows() {
  return within(screen.getByRole("list")).getAllByRole("listitem");
}

/** A new-password field as the pages build it, refused by the server for `reason` when submitted. */
function Field({ reason, message }: { reason: PasswordReason; message: string }) {
  const [password, setPassword] = useState("");
  const [rejected, setRejected] = useState<{ reason: PasswordReason; message: string } | null>(null);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        setRejected({ reason, message });
      }}
    >
      <PasswordInput
        label="Password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        aria-describedby="rules"
      />
      <PasswordRules id="rules" password={password} minimum={8} rejected={rejected} />
      <button type="submit">Create account</button>
    </form>
  );
}

describe("PasswordRules", () => {
  it("checks the length as the user types and leaves the rest to the server", () => {
    const { rerender } = render(<PasswordRules id="rules" password="" minimum={8} />);
    expect(screen.getByRole("list")).toHaveAttribute("id", "rules");
    expect(rows().map((row) => row.textContent)).toEqual([
      "At least 8 characters",
      "Not a common or leaked password, and not your email",
    ]);
    expect(rows()[0]).not.toHaveClass("auth-rule-met");

    rerender(<PasswordRules id="rules" password="seven77" minimum={8} />);
    expect(rows()[0]).not.toHaveClass("auth-rule-met");

    rerender(<PasswordRules id="rules" password="eight888" minimum={8} />);
    expect(rows()[0]).toHaveClass("auth-rule-met");
    expect(rows()[0]).toHaveTextContent("At least 8 characters: done");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("counts characters as the server does (code points of the NFC form)", () => {
    const { rerender } = render(<PasswordRules id="rules" password={"🔒🔒🔒🔒"} minimum={5} />);
    expect(rows()[0]).not.toHaveClass("auth-rule-met");
    rerender(<PasswordRules id="rules" password={"ééééé"} minimum={5} />);
    expect(rows()[0]).toHaveClass("auth-rule-met");
    expect(rows()[0]).toHaveTextContent("At least 5 characters");
  });

  it.each([
    ["too_short", "Use at least 8 characters.", 0],
    ["too_long", "Use at most 256 characters.", 0],
    ["common", "This password is too common. Choose one that's harder to guess.", 1],
    ["context", "Don't use the app's name, your username or your email in the password.", 1],
    ["breached", BREACHED, 1],
  ] as const)("puts a %s refusal in place of the rule it broke", (reason, message, index) => {
    render(<PasswordRules id="rules" password="hunter22" minimum={8} rejected={{ reason, message }} />);
    expect(rows()).toHaveLength(2);
    const refused = rows()[index];
    expect(refused).toHaveClass("auth-rule-error");
    expect(within(refused).getByRole("alert")).toHaveTextContent(message);
    expect(rows()[1 - index]).not.toHaveClass("auth-rule-error");
  });

  it("brings the rule back once the password is edited", async () => {
    const user = userEvent.setup();
    render(<Field reason="breached" message={BREACHED} />);
    const input = screen.getByLabelText("Password");
    await user.type(input, "Password1!");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert")).toHaveTextContent(BREACHED);
    expect(input).toHaveAccessibleDescription(`At least 8 characters: done ${BREACHED}`);

    await user.type(input, "x");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(rows()[1]).toHaveTextContent("Not a common or leaked password, and not your email");

    await user.click(screen.getByRole("button", { name: "Create account" })); // refused again
    expect(screen.getByRole("alert")).toHaveTextContent(BREACHED);
  });
});
