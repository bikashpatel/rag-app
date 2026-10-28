import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Me } from "../../api/types";
import { makeLapsedPlanAccess, makeMe } from "../../test/fixtures";
import { errorResponse, stubFetch } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import PlansFrame from "./PlansFrame";

async function setup(me: Me) {
  stubFetch({ "GET /auth/me": me, "POST /auth/logout": () => errorResponse(503, "Down") });
  const view = await renderWithAuth(
    <PlansFrame title="Plans" mainClassName="plans-main">
      <h1>Page</h1>
    </PlansFrame>,
    { route: "/plans" },
  );
  return { view, user: userEvent.setup() };
}

describe("PlansFrame", () => {
  it("leads back to the chat for a user with access, and names the tab", async () => {
    await setup(makeMe());
    expect(screen.getByRole("link", { name: "Back to chat" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "AskNuvexusDoc home" })).toHaveAttribute("href", "/");
    expect(document.title).toBe("Plans · AskNuvexusDoc");
  });

  it("keeps a user without access on the plans", async () => {
    await setup(makeMe({ plan: makeLapsedPlanAccess() }));
    expect(screen.queryByRole("link", { name: "Back to chat" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AskNuvexusDoc home" })).toHaveAttribute("href", "/plans");
  });

  it("opens Settings from the avatar menu and says when signing out fails", async () => {
    const { user, view } = await setup(makeMe());
    await user.click(screen.getByRole("button", { name: /^Account: / }));
    await user.click(screen.getByRole("menuitem", { name: /^Plan & billing/ }));
    expect(view.location()).toBe("/plans?settings=billing");
    await user.click(screen.getByRole("button", { name: /^Account: / }));
    await user.click(screen.getByRole("menuitem", { name: "Log out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't sign out.");
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
