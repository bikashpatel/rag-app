import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me, PlanCatalog, PlanOffer } from "../api/types";
import { makeCatalog, makeCheckoutOption, makeOrder, makePaymentsInfo, makeSeededOffers } from "../test/billing";
import { makeAdminMe, makeLapsedPlanAccess, makeMe, makePaidPlanAccess, makePlanAccess, NOW } from "../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import PlansPage from "./PlansPage";

const STARTER_PLAN = { id: 1, code: "starter", name: "Starter" };

async function setup(
  me: Me,
  catalog: Route = makeCatalog(),
  { route = "/plans", routes = {} as Record<string, Route> } = {},
) {
  const calls = stubFetch({ "GET /auth/me": me, "GET /billing/plans": catalog, ...routes });
  const user = userEvent.setup();
  const view = await renderWithAuth(<PlansPage />, { route });
  return { calls, user, view };
}

const card = (name: string) => screen.getByRole("listitem", { name });

/**
 * Starter's current period (8 Oct to 7 Nov), with the options the server gives then: the labels exactly as
 * `_period_option` in backend/app/services/billing/checkout.py writes them, a higher plan's with its own "or".
 */
function activeStarterCatalog(): PlanCatalog {
  const [starter, standard, professional] = makeSeededOffers();
  const later = { starts_at: "2026-11-07T18:30:00Z", ends_at: "2026-12-07T18:30:00Z", valid_through: "2026-12-07" };
  const plans: PlanOffer[] = [
    { ...starter, current: true, options: [makeCheckoutOption({ label: "Renew", amount_paise: 50_000, ...later })] },
    {
      ...standard,
      options: [
        makeCheckoutOption({
          kind: "now",
          label: "Upgrade now · ₹1,700",
          amount_paise: 170_000,
          credit: { description: "Credit for unused Starter, 20 Oct to 7 Nov", amount_paise: 30_000 },
        }),
        makeCheckoutOption({ label: "or switch on 8 Nov 2026", ...later }),
      ],
    },
    {
      ...professional,
      options: [
        makeCheckoutOption({ kind: "now", label: "Upgrade now · ₹4,700", amount_paise: 470_000 }),
        makeCheckoutOption({
          label: "or switch on 8 Nov 2026",
          ...later,
          unavailable_reason: "You already have a period paid ahead.",
        }),
      ],
    },
  ];
  return makeCatalog({ plans });
}

const starterPeriod = (overrides = {}) =>
  makePaidPlanAccess({
    plan: STARTER_PLAN,
    features: { files: false, images: false, plugins: [] },
    ...overrides,
  });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

describe("PlansPage", () => {
  it("asks a new user to choose, emphasising the recommended plan with the page's only primary button", async () => {
    const { user, view } = await setup(makeMe({ plan: makeLapsedPlanAccess("none") }));
    expect(screen.getByRole("heading", { level: 1, name: "Choose a plan" })).toBeInTheDocument();
    expect(
      screen.getByText("Your account is ready. Pick a plan to start chatting with your documents."),
    ).toBeInTheDocument();
    await screen.findByRole("listitem", { name: "Standard" });
    const list = screen.getByRole("list", { name: "Plans" });
    expect(within(list).getAllByRole("listitem", { name: /Starter|Standard|Professional/ })).toHaveLength(3);
    expect(card("Standard")).toHaveTextContent("Recommended");
    const choose = within(card("Standard")).getByRole("link", { name: "Choose Standard" });
    expect(choose).toHaveClass("btn-primary");
    expect(document.querySelectorAll(".btn-primary")).toHaveLength(1);
    expect(within(card("Starter")).getByRole("link", { name: "Choose Starter" })).not.toHaveClass("btn-primary");
    expect(screen.queryByRole("link", { name: "Back to chat" })).not.toBeInTheDocument();
    await user.click(choose);
    expect(view.location()).toBe("/plans/checkout/standard?kind=period");
  });

  it("shows the current plan with renewal, upgrades for a credit and later switches, and no loud button", async () => {
    const { user, view } = await setup(makeMe({ plan: starterPeriod() }), activeStarterCatalog());
    expect(screen.getByRole("heading", { level: 1, name: "Your plan: Starter" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Valid through 7 Nov 2026. Upgrade now with credit for what's left, or switch plans when this one ends.",
      ),
    ).toBeInTheDocument();
    await screen.findByRole("listitem", { name: "Standard" });
    expect(card("Starter")).toHaveTextContent("Current plan");
    expect(within(card("Starter")).getByRole("link", { name: "Renew Starter" })).not.toHaveClass("btn-primary");
    expect(card("Starter")).toHaveTextContent("Adds 30 days from 8 Nov");
    const upgrade = within(card("Standard")).getByRole("link", { name: /^Upgrade now ?to Standard ?· ₹1,700$/ });
    expect(upgrade.querySelector(".sr-only")).toHaveTextContent("to Standard");
    const alternative = within(card("Standard")).getByRole("link", { name: /^or switch on 8 Nov ?to Standard$/ });
    expect(alternative).toHaveAttribute("href", "/plans/checkout/standard?kind=period");
    expect(alternative.parentElement).toHaveTextContent(/^or switch on 8 Nov to Standard$/);
    expect(within(card("Professional")).queryByText(/or switch/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".plans-grid .btn-primary")).toHaveLength(0);
    expect(screen.getByRole("link", { name: "Back to chat" })).toHaveAttribute("href", "/");
    await user.click(upgrade);
    expect(view.location()).toBe("/plans/checkout/standard?kind=now");
  });

  it("makes renewing the one primary action while the plan ends soon, with the days left", async () => {
    const plan = starterPeriod({
      expiring_soon: true,
      period_end: "2026-10-24T18:30:00Z",
      paid_through: "2026-10-24T18:30:00Z",
      valid_through: "2026-10-24",
    });
    await setup(makeMe({ plan }), activeStarterCatalog());
    expect(screen.getByText("Ends in 4 days")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Renew Starter" })).toHaveClass("btn-primary");
  });

  it.each([
    ["the recommended plan", true],
    ["the cheapest plan that can be bought, without a recommended one", false],
  ])("gives %s the primary button while a retired plan ends soon", async (_, recommended) => {
    const plan = starterPeriod({
      expiring_soon: true,
      period_end: "2026-10-24T18:30:00Z",
      paid_through: "2026-10-24T18:30:00Z",
      valid_through: "2026-10-24",
    });
    const catalog = activeStarterCatalog();
    const [starter, standard, professional] = catalog.plans;
    catalog.plans = [
      {
        ...starter,
        status: "retired",
        options: [makeCheckoutOption({ label: "Renew", unavailable_reason: "This plan is no longer offered." })],
      },
      { ...standard, recommended },
      professional,
    ];
    await setup(makeMe({ plan }), catalog);
    expect(await screen.findByRole("button", { name: "Renew Starter" })).toHaveAttribute("aria-disabled", "true");
    expect(within(card("Standard")).getByRole("link", { name: /^Upgrade now/ })).toHaveClass("btn-primary");
    expect(document.querySelectorAll(".plans-grid .btn-primary")).toHaveLength(1);
  });

  it("keeps the upgrades quiet while a retired plan has time left", async () => {
    const catalog = activeStarterCatalog();
    catalog.plans[0] = { ...catalog.plans[0], status: "retired", options: [] };
    await setup(makeMe({ plan: starterPeriod() }), catalog);
    await screen.findByRole("listitem", { name: "Standard" });
    expect(document.querySelectorAll(".plans-grid .btn-primary")).toHaveLength(0);
  });

  it("says a stacked plan is paid ahead and names the plan that follows", async () => {
    const plan = starterPeriod({
      paid_through: "2026-12-07T18:30:00Z",
      valid_through: "2026-12-07",
      next_plan: { id: 2, code: "standard", name: "Standard" },
    });
    await setup(makeMe({ plan }), activeStarterCatalog());
    expect(
      screen.getByText(
        "Valid through 7 Dec 2026, paid ahead. Upgrade now with credit for what's left, or switch plans when this one ends. From 8 Nov: Standard.",
      ),
    ).toBeInTheDocument();
  });

  it("offers to renew the plan that ended, as the emphasised card", async () => {
    const [starter, standard, professional] = makeSeededOffers();
    const catalog = makeCatalog({
      plans: [starter, { ...standard, options: [makeCheckoutOption({ label: "Renew Standard" })] }, professional],
    });
    await setup(makeMe({ plan: makeLapsedPlanAccess() }), catalog);
    expect(screen.getByRole("heading", { level: 1, name: "Your Standard plan has ended" })).toBeInTheDocument();
    expect(screen.getByText(/^It ended on 7 Oct 2026\. Your chats and documents are kept/)).toBeInTheDocument();
    await screen.findByRole("listitem", { name: "Standard" });
    expect(card("Standard")).toHaveTextContent("Your last plan");
    expect(within(card("Standard")).getByRole("link", { name: "Renew Standard" })).toHaveClass("btn-primary");
  });

  it("shows an option that can't be bought disabled, with its reason", async () => {
    const catalog = activeStarterCatalog();
    catalog.plans[0] = {
      ...catalog.plans[0],
      options: [makeCheckoutOption({ label: "Renew", unavailable_reason: "You already have a period paid ahead." })],
    };
    const { user, view } = await setup(makeMe({ plan: starterPeriod() }), catalog);
    const renew = await screen.findByRole("button", { name: "Renew Starter" });
    expect(renew).toHaveAttribute("aria-disabled", "true");
    expect(renew).toHaveAccessibleDescription("You already have a period paid ahead.");
    await user.click(renew);
    expect(view.location()).toBe("/plans");
  });

  it("shows the admin the page users see, with every button disabled", async () => {
    await setup(makeAdminMe(), makeCatalog({ plans: makeSeededOffers().map((offer) => ({ ...offer, options: [] })) }));
    expect(screen.getByRole("heading", { level: 1, name: "Plans" })).toBeInTheDocument();
    expect(
      screen.getByText("You're the admin, so plans don't apply to you. This is what users see."),
    ).toBeInTheDocument();
    const choose = await screen.findByRole("button", { name: "Choose Standard" });
    expect(choose).toHaveAttribute("aria-disabled", "true");
    expect(choose).toHaveAttribute("title", "The admin doesn't need a plan.");
  });

  it("says when online payment isn't set up, with the contact or, for the admin, where to set it up", async () => {
    const off = makeCatalog({ payments: makePaymentsInfo({ available: false, mode: null }) });
    const member = await setup(makeMe({ plan: makeLapsedPlanAccess("none") }), off);
    expect(await screen.findByText(/Online payment isn't set up yet\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "billing@example.com" })).toHaveAttribute(
      "href",
      "mailto:billing@example.com",
    );
    member.view.unmount();

    await setup(makeAdminMe(), off);
    expect(await screen.findByRole("link", { name: "Set up payments in Admin → Billing" })).toHaveAttribute(
      "href",
      "/admin#billing",
    );
  });

  it("shows testers the test mode notice", async () => {
    await setup(
      makeMe({ plan: makeLapsedPlanAccess("none") }),
      makeCatalog({ payments: makePaymentsInfo({ mode: "test" }) }),
    );
    expect(await screen.findByText(/no real money moves/)).toHaveTextContent(
      "Test mode: no real money moves. Pay with card 4100 2800 0000 1007, any future expiry and any CVV.",
    );
  });

  it("shows a payment in progress, and cancels it", async () => {
    let me = makeMe({ plan: makeLapsedPlanAccess("none", { pending_order: makeOrder() }) });
    const { calls, user } = await setup(me, makeCatalog(), {
      routes: {
        "GET /auth/me": () => me,
        "POST /billing/orders/q3Zx8LmP0aB2cD4eF6gH/cancel": (call: FetchCall) => {
          me = makeMe({ plan: makeLapsedPlanAccess("none") });
          return { ...makeOrder({ public_id: call.path.split("/")[3] }), status: "cancelled" };
        },
      },
    });
    expect(screen.getByText(/is in progress/)).toHaveTextContent("A payment for Standard is in progress.");
    expect(screen.getByRole("link", { name: "Check status" })).toHaveAttribute(
      "href",
      "/billing/return?order=q3Zx8LmP0aB2cD4eF6gH",
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Choose a plan" })).toBeInTheDocument();
    await vi.waitFor(() => expect(screen.queryByText(/is in progress/)).not.toBeInTheDocument());
    expect(calls.filter((call) => call.path === "/billing/plans")).toHaveLength(2);
  });

  it("explains a return from Razorpay that couldn't be matched", async () => {
    await setup(makeMe({ plan: makeLapsedPlanAccess("none") }), makeCatalog(), { route: "/plans?payment=unknown" });
    expect(screen.getByText(/We couldn't match that return from Razorpay\./)).toBeInTheDocument();
  });

  it("describes an open-ended complimentary plan", async () => {
    await setup(makeMe({ plan: makePlanAccess() }));
    expect(screen.getByRole("heading", { level: 1, name: "Your plan: Professional" })).toBeInTheDocument();
    expect(screen.getByText("Complimentary · no end date")).toBeInTheDocument();
    expect(screen.getByText(/^Complimentary, with no end date\./)).toBeInTheDocument();
  });

  it("compares the plans and says how paying works, with the policies in the footer", async () => {
    await setup(makeMe({ plan: makeLapsedPlanAccess("none") }));
    expect(await screen.findByRole("region", { name: "Compare plans" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "How payment works" })).toHaveTextContent("Pay on Razorpay's secure page");
    expect(screen.getByRole("navigation", { name: "Policies" })).toBeInTheDocument();
    expect(screen.getByText("Payments are processed by Razorpay.")).toBeInTheDocument();
  });

  it("shows skeleton cards while loading, then a failed load with Try again", async () => {
    let fail = true;
    const { user } = await setup(makeMe({ plan: makeLapsedPlanAccess("none") }), () =>
      fail ? errorResponse(500, null) : makeCatalog(),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the plans.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("listitem", { name: "Standard" })).toBeInTheDocument();
  });

  it("says when no plans are available", async () => {
    const member = await setup(makeMe({ plan: makeLapsedPlanAccess("none") }), makeCatalog({ plans: [] }));
    expect(await screen.findByRole("heading", { name: "No plans are available yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "billing@example.com" })).toBeInTheDocument();
    member.view.unmount();

    await setup(makeAdminMe(), makeCatalog({ plans: [] }));
    expect(await screen.findByRole("link", { name: "Create plans in Admin → Plans" })).toHaveAttribute(
      "href",
      "/admin#plans",
    );
  });
});
