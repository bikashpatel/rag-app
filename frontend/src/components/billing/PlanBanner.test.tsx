import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PlanAccess } from "../../api/types";
import { makeCatalog, makeOrder, makeSeededOffers } from "../../test/billing";
import { makePaidPlanAccess, NOW } from "../../test/fixtures";
import { errorResponse, stubFetch } from "../../test/http";
import { renderWithRouter } from "../../test/render";
import PendingCheckoutBanner, { PendingCheckoutNotice } from "./PendingCheckoutBanner";
import PlanBanner from "./PlanBanner";

const ending = (validThrough: string, overrides: Partial<PlanAccess> = {}) =>
  makePaidPlanAccess({
    expiring_soon: true,
    period_end: `${validThrough}T18:30:00Z`,
    paid_through: `${validThrough}T18:30:00Z`,
    valid_through: validThrough,
    ...overrides,
  });

/** The seeded catalog with Standard retired (or left out). */
function retiredCatalog(drop = false) {
  const [starter, standard, professional] = makeSeededOffers();
  const plans = drop ? [starter, professional] : [starter, { ...standard, status: "retired" as const }, professional];
  return makeCatalog({ plans });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

describe("PlanBanner", () => {
  it.each([
    ["2026-10-25", "Your Standard plan ends in 5 days, on 25 Oct.", "ui-notice-info"],
    ["2026-10-21", "Your Standard plan ends tomorrow, on 21 Oct.", "ui-notice-warning"],
    ["2026-10-20", "Your Standard plan ends tonight at midnight.", "ui-notice-warning"],
  ])("says when a plan valid through %s ends, with Renew", async (date, text, tone) => {
    stubFetch({ "GET /billing/plans": makeCatalog() });
    renderWithRouter(<PlanBanner plan={ending(date)} />);
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent(text);
    expect(banner).toHaveClass(tone);
    expect(await screen.findByRole("link", { name: "Renew" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );
  });

  it.each([
    ["retired", retiredCatalog()],
    ["no longer in the catalog", retiredCatalog(true)],
  ])("offers Choose a plan instead of Renew when the plan is %s", async (_, catalog) => {
    stubFetch({ "GET /billing/plans": catalog });
    renderWithRouter(<PlanBanner plan={ending("2026-10-25")} />);
    expect(await screen.findByRole("link", { name: "Choose a plan" })).toHaveAttribute("href", "/plans");
    expect(screen.queryByRole("link", { name: "Renew" })).not.toBeInTheDocument();
  });

  it("waits for the catalog before showing an action, and offers Renew when it can't be read", async () => {
    let fail!: () => void;
    stubFetch({
      "GET /billing/plans": () => new Promise((resolve) => (fail = () => resolve(errorResponse(500, null)))),
    });
    renderWithRouter(<PlanBanner plan={ending("2026-10-25")} />);
    expect(screen.getByRole("status")).toHaveTextContent("Your Standard plan ends in 5 days");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    fail();
    expect(await screen.findByRole("link", { name: "Renew" })).toBeInTheDocument();
  });

  it("names a complimentary plan", () => {
    stubFetch({ "GET /billing/plans": makeCatalog() });
    renderWithRouter(<PlanBanner plan={ending("2026-10-25", { complimentary: true })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Your complimentary Standard plan ends in 5 days");
  });

  it("isn't shown when the plan doesn't end soon, and then loads nothing", () => {
    const calls = stubFetch({});
    renderWithRouter(<PlanBanner plan={makePaidPlanAccess()} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("stays dismissed for the rest of the day, for that period end only", async () => {
    stubFetch({ "GET /billing/plans": makeCatalog() });
    const user = userEvent.setup();
    const first = renderWithRouter(<PlanBanner plan={ending("2026-10-25")} />);
    await user.click(screen.getByRole("button", { name: "Dismiss until tomorrow" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    first.unmount();

    const again = renderWithRouter(<PlanBanner plan={ending("2026-10-25")} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    again.unmount();

    const other = renderWithRouter(<PlanBanner plan={ending("2026-10-24")} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    other.unmount();

    vi.setSystemTime(new Date("2026-10-21T09:30:00Z"));
    renderWithRouter(<PlanBanner plan={ending("2026-10-25")} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows again when the storage can't be read", () => {
    stubFetch({ "GET /billing/plans": makeCatalog() });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderWithRouter(<PlanBanner plan={ending("2026-10-25")} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

describe("PendingCheckoutBanner", () => {
  it("follows a payment in progress, without a dismiss", () => {
    renderWithRouter(<PendingCheckoutBanner order={makeOrder()} />);
    expect(screen.getByRole("status")).toHaveTextContent("A payment for Standard is in progress.");
    expect(screen.getByRole("link", { name: "Check status" })).toHaveAttribute(
      "href",
      "/billing/return?order=q3Zx8LmP0aB2cD4eF6gH",
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("offers Resume only for a Razorpay page, and reports a failed cancel", async () => {
    stubFetch({
      "POST /billing/orders/q3Zx8LmP0aB2cD4eF6gH/cancel": () => errorResponse(409, "The payment was already made."),
    });
    const onCancelled = vi.fn();
    const user = userEvent.setup();
    renderWithRouter(
      <PendingCheckoutNotice order={makeOrder({ pay_url: "https://evil.example/pay" })} onCancelled={onCancelled} />,
    );
    expect(screen.queryByRole("link", { name: "Resume payment" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("The payment was already made.")).toBeInTheDocument();
    expect(onCancelled).not.toHaveBeenCalled();
  });
});
