import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { makeCatalog, makeCheckoutOption, makeSeededOffers } from "../../test/billing";
import { makeLapsedPlanAccess } from "../../test/fixtures";
import { errorResponse, stubFetch, type Route } from "../../test/http";
import { renderWithRouter } from "../../test/render";
import PlanEndedPanel from "./PlanEndedPanel";

function setup(catalog: Route = makeCatalog(), plan = makeLapsedPlanAccess(), draftKept = false) {
  stubFetch({ "GET /billing/plans": catalog });
  return renderWithRouter(<PlanEndedPanel plan={plan} draftKept={draftKept} />);
}

describe("PlanEndedPanel", () => {
  it("says the plan ended, takes focus, and offers to renew it at its price", async () => {
    const [starter, standard, professional] = makeSeededOffers();
    setup(
      makeCatalog({
        plans: [starter, { ...standard, options: [makeCheckoutOption({ label: "Renew Standard" })] }, professional],
      }),
    );
    const heading = screen.getByRole("heading", { name: "Your Standard plan ended on 7 Oct" });
    expect(heading).toHaveFocus();
    expect(screen.getByRole("region", { name: "Your Standard plan ended on 7 Oct" })).toHaveTextContent(
      "Renew to continue. Your chats and documents are kept.",
    );
    expect(await screen.findByRole("link", { name: "Renew ₹2,000" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );
    expect(screen.getByRole("link", { name: "See plans" })).toHaveAttribute("href", "/plans");
  });

  it("offers only the plans when the last plan can't be renewed or the catalog fails", async () => {
    const [starter, standard, professional] = makeSeededOffers();
    const retired = makeCatalog({
      plans: [
        starter,
        { ...standard, options: [makeCheckoutOption({ unavailable_reason: "This plan is no longer offered." })] },
        professional,
      ],
    });
    const first = setup(retired);
    const region = screen.getByRole("region", { name: "Your Standard plan ended on 7 Oct" });
    expect(await within(region).findByText(/^Choose a plan to continue\./)).toBeInTheDocument();
    expect(region).not.toHaveTextContent("Renew to continue.");
    expect(screen.queryByRole("link", { name: /^Renew/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See plans" })).toBeInTheDocument();
    first.unmount();

    const calls = stubFetch({ "GET /billing/plans": () => errorResponse(500, null) });
    renderWithRouter(<PlanEndedPanel plan={makeLapsedPlanAccess()} draftKept={false} />);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(screen.getByText(/^Renew to continue\./)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Renew/ })).not.toBeInTheDocument();
  });

  it("says the unsent message is kept", () => {
    setup(makeCatalog(), makeLapsedPlanAccess(), true);
    expect(screen.getByText("Your unsent message is saved and comes back when you renew.")).toBeInTheDocument();
  });

  it("asks a user who never had a plan to choose one", () => {
    setup(makeCatalog(), makeLapsedPlanAccess("none"));
    expect(screen.getByRole("heading", { name: "You don't have a plan" })).toBeInTheDocument();
    expect(screen.getByText(/Choose a plan to continue\./)).toBeInTheDocument();
  });
});
