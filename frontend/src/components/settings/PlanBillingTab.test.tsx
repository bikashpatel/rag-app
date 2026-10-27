import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BillingStatus, Me, PaymentView, PlanOffer } from "../../api/types";
import {
  makeBillingStatus,
  makeCatalog,
  makeCheckoutOption,
  makeOrder,
  makePaymentsInfo,
  makePaymentView,
  makePeriodView,
  makeSeededOffers,
  STANDARD,
  STARTER,
} from "../../test/billing";
import {
  makeAdminMe,
  makeLapsedPlanAccess,
  makeMe,
  makePaidPlanAccess,
  makePlanAccess,
  NOW,
} from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";
import PlanBillingTab from "./PlanBillingTab";

async function setup(status: Route = makeBillingStatus(), routes: Record<string, Route> = {}, me: Me = makeMe()) {
  const calls = stubFetch({
    "GET /auth/me": me,
    "GET /billing/status": status,
    "GET /billing/payments": [makePaymentView()],
    "GET /billing/plans": makeCatalog(),
    "PUT /billing/profile": (call: FetchCall) => call.body as object,
    ...routes,
  });
  const user = userEvent.setup();
  const onClose = vi.fn();
  const view = await renderWithAuth(<PlanBillingTab user={me} onClose={onClose} />);
  return { calls, user, view, onClose };
}

/** The seeded catalog with Standard, the plan of the paid period, changed. */
function catalogWithStandard(overrides: Partial<PlanOffer>) {
  const [starter, standard, professional] = makeSeededOffers();
  return makeCatalog({ plans: [starter, { ...standard, ...overrides }, professional] });
}

const endingSoon = () =>
  makePaidPlanAccess({
    expiring_soon: true,
    period_end: "2026-10-24T18:30:00Z",
    paid_through: "2026-10-24T18:30:00Z",
    valid_through: "2026-10-24",
  });

function fact(term: string): HTMLElement {
  return screen.getByText(term, { selector: "dt" }).nextElementSibling as HTMLElement;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

describe("PlanBillingTab", () => {
  it("shows a paid period: the plan, its price, the days left, the period bar and what it includes", async () => {
    await setup();
    const plan = await screen.findByRole("region", { name: "Standard" });
    expect(plan).toHaveTextContent("Active");
    expect(plan).toHaveTextContent("₹2,000 a month");
    expect(within(plan).getByText("18")).toBeInTheDocument();
    expect(plan).toHaveTextContent("days left in this period");
    expect(within(plan).getByRole("meter", { name: "This period" })).toHaveAttribute("aria-valuetext", "Day 13 of 31");
    expect(plan).toHaveTextContent("8 Oct");
    expect(plan).toHaveTextContent("7 Nov 2026");
    expect(fact("Includes")).toHaveTextContent(
      "Files from chat · Claude Haiku 5.5, GPT-5.4 mini and Claude Sonnet 5.5" +
        "About 260 answers with Claude Sonnet 5.5, 100 documents and 500 MB per period",
    );
    expect(plan).toHaveTextContent("Renewing adds 30 days after 7 Nov.");
    expect(within(plan).getByRole("link", { name: "Change plan" })).toHaveAttribute("href", "/plans");
    expect(within(plan).getByRole("link", { name: "Renew" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );
    expect(screen.queryByText("Coming up")).not.toBeInTheDocument();
  });

  it("shows a period paid ahead, the plan that follows and what is coming up", async () => {
    const plan = makePaidPlanAccess({
      paid_through: "2026-12-07T18:30:00Z",
      valid_through: "2026-12-07",
      next_plan: STARTER,
    });
    const next = makePeriodView({
      id: 13,
      plan: STARTER,
      plan_name: "Starter",
      starts_at: "2026-11-07T18:30:00Z",
      ends_at: "2026-12-07T18:30:00Z",
      valid_through: "2026-12-07",
      current: false,
    });
    await setup(makeBillingStatus({ plan, periods: [makePeriodView(), next] }));
    expect(fact("Paid through")).toHaveTextContent("7 Dec 2026One more period is already paid");
    expect(fact("Then")).toHaveTextContent("Starter from 8 Nov");
    const coming = screen.getByRole("list", { name: "Coming up" });
    expect(within(coming).getByText("Starter")).toBeInTheDocument();
    expect(coming).toHaveTextContent("8 Nov to 7 Dec 2026");
    expect(coming).toHaveTextContent("Paid");
  });

  it("warns while the plan ends soon", async () => {
    const plan = makePaidPlanAccess({
      expiring_soon: true,
      period_end: "2026-10-24T18:30:00Z",
      paid_through: "2026-10-24T18:30:00Z",
      valid_through: "2026-10-24",
    });
    await setup(makeBillingStatus({ plan }));
    expect(await screen.findByText(/Renew to keep using/)).toHaveTextContent(
      "Your plan ends in 4 days. Renew to keep using the assistant without a break.",
    );
    expect(screen.getByRole("meter", { name: "This period" })).toHaveClass("ui-meter-warning");
  });

  it("asks for another plan instead of a renewal when the plan is retired", async () => {
    const retired = catalogWithStandard({
      status: "retired",
      options: [makeCheckoutOption({ label: "Renew", unavailable_reason: "This plan is no longer offered." })],
    });
    await setup(makeBillingStatus({ plan: endingSoon() }), { "GET /billing/plans": retired });
    const plan = await screen.findByRole("region", { name: "Standard" });
    expect(within(plan).getByText(/^Your plan ends in 4 days\./)).toHaveTextContent(
      "Your plan ends in 4 days. Choose another plan to keep using the assistant without a break.",
    );
    expect(plan).toHaveTextContent("Standard is no longer offered, so it can't be renewed.");
    expect(plan).not.toHaveTextContent(/Renew/);
    const choose = within(plan).getByRole("link", { name: "Choose a plan" });
    expect(choose).toHaveAttribute("href", "/plans");
    expect(choose).toHaveClass("btn-primary");
    expect(within(plan).queryByRole("link", { name: "Change plan" })).not.toBeInTheDocument();
  });

  it("says when renewing is possible again while a period is paid ahead", async () => {
    const plan = makePaidPlanAccess({ paid_through: "2026-12-07T18:30:00Z", valid_through: "2026-12-07" });
    const paidAhead = catalogWithStandard({
      options: [makeCheckoutOption({ label: "Renew", unavailable_reason: "You already have a period paid ahead." })],
    });
    await setup(makeBillingStatus({ plan }), { "GET /billing/plans": paidAhead });
    const card = await screen.findByRole("region", { name: "Standard" });
    expect(card).toHaveTextContent("One more period is already paid; you can renew from 8 Nov.");
    expect(card).not.toHaveTextContent("Renewing adds");
    expect(within(card).queryByRole("link", { name: "Renew" })).not.toBeInTheDocument();
  });

  it("gives the period's length in test mode, and the upgrade only when a higher plan offers one", async () => {
    const [starter, standard, professional] = makeSeededOffers();
    const upgrade = makeCheckoutOption({ kind: "now", label: "Upgrade now · ₹4,700", amount_paise: 470_000 });
    const catalog = makeCatalog({ plans: [starter, standard, { ...professional, options: [upgrade] }] });
    const first = await setup(makeBillingStatus({ payments: makePaymentsInfo({ mode: "test" }) }), {
      "GET /billing/plans": catalog,
    });
    const card = await screen.findByRole("region", { name: "Standard" });
    expect(card).toHaveTextContent(
      "Renewing adds 1 day after 7 Nov. Upgrading starts a new period now, with credit for what's left.",
    );
    first.view.unmount();

    await setup();
    expect(await screen.findByRole("region", { name: "Standard" })).not.toHaveTextContent("Upgrading");
  });

  it("gives the reason a renewal can't be bought, without a Renew button", async () => {
    const off = catalogWithStandard({
      options: [makeCheckoutOption({ label: "Renew", unavailable_reason: "Online payment isn't set up yet." })],
    });
    await setup(makeBillingStatus({ plan: endingSoon() }), { "GET /billing/plans": off });
    const card = await screen.findByRole("region", { name: "Standard" });
    expect(within(card).getByText(/^Your plan ends in 4 days\./)).toHaveTextContent(/^Your plan ends in 4 days\.$/);
    expect(card).toHaveTextContent("Online payment isn't set up yet.");
    expect(within(card).queryByRole("link", { name: "Renew" })).not.toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Change plan" })).toBeInTheDocument();
  });

  it("shows an open-ended complimentary plan with its reason and no end", async () => {
    const status = makeBillingStatus({
      plan: makePlanAccess(),
      periods: [],
      complimentary: {
        plan: { id: 3, code: "professional", name: "Professional" },
        reason: "Existing account when plans were introduced",
        granted_by: "system",
        granted_at: "2026-10-26T00:00:00Z",
      },
    });
    await setup(status);
    const plan = await screen.findByRole("region", { name: "Professional" });
    expect(plan).toHaveTextContent("Complimentary · no end date");
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(plan).not.toHaveTextContent("days left");
    expect(fact("Complimentary")).toHaveTextContent("Existing account when plans were introduced");
    expect(within(plan).queryByRole("link", { name: "Renew" })).not.toBeInTheDocument();
    expect(plan).toHaveTextContent("your complimentary plan continues after it");
  });

  it("offers a plan to a user without one, and the renewal of the last one", async () => {
    const none = await setup(makeBillingStatus({ plan: makeLapsedPlanAccess("none"), periods: [] }));
    const empty = await screen.findByRole("region", { name: "No active plan" });
    expect(empty).toHaveTextContent("Choose a plan to start chatting with your documents.");
    expect(within(empty).getByRole("link", { name: "Choose a plan" })).toHaveAttribute("href", "/plans");
    none.view.unmount();

    await setup(makeBillingStatus({ plan: makeLapsedPlanAccess(), periods: [] }));
    const expired = await screen.findByRole("region", { name: "Standard" });
    expect(expired).toHaveTextContent("Ended 7 Oct");
    expect(within(expired).getByRole("link", { name: "Renew Standard" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );
  });

  it("tells the admin plans don't apply, without loading anything", async () => {
    const { calls } = await setup(makeBillingStatus(), {}, makeAdminMe());
    expect(screen.getByText("Plans don't apply to the admin.")).toBeInTheDocument();
    expect(calls.some((call) => call.path.startsWith("/billing"))).toBe(false);
  });

  it("lists the payments with their period, method, status and receipt", async () => {
    const payments: PaymentView[] = [
      makePaymentView(),
      makePaymentView({
        id: 58,
        status: "partially_refunded",
        refunded_paise: 50_000,
        method: "netbanking",
        receipt: null,
      }),
      makePaymentView({ id: 59, status: "disputed", test_mode: true }),
    ];
    await setup(makeBillingStatus(), { "GET /billing/payments": payments });
    const table = await screen.findByRole("table", { name: "Your payments" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("8 Oct 2026");
    expect(rows[0]).toHaveTextContent("Standard plan, 30 days8 Oct to 7 Nov 2026 · UPI");
    expect(rows[0]).toHaveTextContent("₹2,000");
    expect(rows[0]).toHaveTextContent("Paid");
    expect(within(rows[0]).getByRole("link", { name: "Download receipt RA/26-27/000033" })).toHaveAttribute(
      "href",
      "/api/billing/receipts/33",
    );
    expect(rows[1]).toHaveTextContent("Refunded ₹500");
    expect(rows[1]).toHaveTextContent("Netbanking");
    expect(rows[1]).toHaveTextContent("—");
    expect(rows[2]).toHaveTextContent("Disputed");
    expect(rows[2]).toHaveTextContent("Test");
  });

  it("says when there are no payments yet, with the contact for questions", async () => {
    await setup(makeBillingStatus(), { "GET /billing/payments": [] });
    expect(await screen.findByText("No payments yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "billing@example.com" })).toHaveAttribute(
      "href",
      "mailto:billing@example.com",
    );
    expect(screen.getByRole("navigation", { name: "Policies" })).toBeInTheDocument();
  });

  it("saves the name on receipts once changed, and says the reminders come in the app", async () => {
    const { calls, user } = await setup();
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByLabelText("Email for reminders")).not.toBeInTheDocument();
    expect(screen.getByText("Plan reminders: in the app.")).toBeInTheDocument();
    await user.click(save);
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    await user.type(screen.getByLabelText("Name on receipts"), "  Maya Chen ");
    expect(save).not.toHaveAttribute("aria-disabled");
    await user.click(save);
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ name: "Maya Chen", email: null });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
    // Nothing left to save, and the focus stays on the button instead of falling to the page.
    expect(save).toHaveAttribute("aria-disabled", "true");
    expect(save).toHaveFocus();
  });

  it("asks for the reminder email only when reminder emails can be sent, and reports a refusal", async () => {
    const status: BillingStatus = makeBillingStatus({ payments: makePaymentsInfo({ email_reminders: true }) });
    const { user } = await setup(status, {
      "PUT /billing/profile": () => errorResponse(422, "email: not a valid address"),
    });
    expect(
      await screen.findByText("Plan reminders: in the app. Add an email to get them by email too."),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email for reminders"), "maya@");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("email: not a valid address");
  });

  it("says reminders also come by email when an email was given", async () => {
    await setup(
      makeBillingStatus({
        payments: makePaymentsInfo({ email_reminders: true }),
        profile: { name: "Maya Chen", email: "maya@example.com" },
      }),
    );
    expect(await screen.findByText("Plan reminders: in the app and by email.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email for reminders")).toHaveValue("maya@example.com");
  });

  it("shows an open checkout with Check status and Cancel, which reloads the tab", async () => {
    let open = true;
    const order = makeOrder();
    const { calls, user } = await setup(() => makeBillingStatus({ open_order: open ? order : null }), {
      "POST /billing/orders/q3Zx8LmP0aB2cD4eF6gH/cancel": () => {
        open = false;
        return { ...order, status: "cancelled" };
      },
    });
    const notice = await screen.findByText(/is in progress/);
    expect(notice).toHaveTextContent("A payment for Standard is in progress.");
    expect(screen.getByRole("link", { name: "Check status" })).toHaveAttribute(
      "href",
      "/billing/return?order=q3Zx8LmP0aB2cD4eF6gH",
    );
    expect(screen.getByRole("link", { name: "Resume payment" })).toHaveAttribute("href", "https://rzp.io/rzp/q3Zx8Lm");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls.filter((call) => call.path === "/billing/status")).toHaveLength(2);
    expect(await screen.findByRole("region", { name: STANDARD.name })).toBeInTheDocument();
    expect(screen.queryByText(/is in progress/)).not.toBeInTheDocument();
  });

  it("reports a failed load with Try again", async () => {
    let fail = true;
    const { user } = await setup(() => (fail ? errorResponse(500, null) : makeBillingStatus()));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your plan.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Standard" })).toBeInTheDocument();
  });

  it("closes from its close button", async () => {
    const { user, onClose } = await setup();
    await user.click(screen.getByRole("button", { name: "Close settings" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
