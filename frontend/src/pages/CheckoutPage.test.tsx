import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CheckoutOption, PlanCatalog } from "../api/types";
import { leaveFor } from "../components/billing/leave";
import { makeCatalog, makeCheckoutOption, makeOrder, makePaymentsInfo, makeSeededOffers } from "../test/billing";
import { makeAdminMe, makeLapsedPlanAccess, makeMe, makePaidPlanAccess, NOW } from "../test/fixtures";
import { errorResponse, jsonResponse, stubFetch, type FetchCall, type Route as Reply } from "../test/http";
import { renderWithAuth } from "../test/render";
import CheckoutPage from "./CheckoutPage";

vi.mock("../components/billing/leave", () => ({ leaveFor: vi.fn() }));

const STARTED = { order: makeOrder(), pay_url: "https://rzp.io/rzp/q3Zx8Lm" };

/** The seeded catalog with Standard's options replaced. */
function catalogWith(options: CheckoutOption[], overrides: Partial<PlanCatalog> = {}): PlanCatalog {
  const plans = makeSeededOffers().map((offer) => (offer.code === "standard" ? { ...offer, options } : offer));
  return makeCatalog({ plans, ...overrides });
}

async function setup(
  {
    me = makeMe({ plan: makeLapsedPlanAccess("none") }),
    catalog = makeCatalog() as Reply,
    route = "/plans/checkout/standard?kind=period",
  } = {},
  routes: Record<string, Reply> = {},
) {
  const calls = stubFetch({
    "GET /auth/me": me,
    "GET /billing/plans": catalog,
    "GET /billing/profile": { name: null, email: null },
    "PUT /billing/profile": (call: FetchCall) => call.body as object,
    "POST /billing/checkout": () => jsonResponse(STARTED, { status: 201 }),
    ...routes,
  });
  const user = userEvent.setup();
  const view = await renderWithAuth(
    <Routes>
      <Route path="/plans/checkout/:code" element={<CheckoutPage />} />
    </Routes>,
    { route },
  );
  await screen.findByRole("heading", { level: 1, name: "Review and pay" });
  return { calls, user, view };
}

const summary = () => screen.getByRole("complementary", { name: "Summary" });
const payIn = (scope: HTMLElement, amount: string) => within(scope).getByRole("button", { name: `Pay ${amount}` });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.mocked(leaveFor).mockReset();
});

describe("CheckoutPage", () => {
  it("reviews the plan, its 30 days and the total, with the consent line under Pay", async () => {
    await setup();
    expect(await screen.findByRole("heading", { name: "Standard" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Checkout steps" });
    expect(within(steps).getAllByRole("listitem")[1]).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("30 days: valid 20 Oct to 19 Nov 2026")).toBeInTheDocument();
    expect(
      screen.getByText("Starts as soon as your payment is confirmed. It doesn't renew by itself."),
    ).toBeInTheDocument();
    expect(screen.getByText("Chat with documents")).toBeInTheDocument();
    expect(screen.getByText("About 260 answers a month")).toBeInTheDocument();
    expect(summary()).toHaveTextContent("Standard plan30 days, 20 Oct to 19 Nov₹2,000");
    expect(summary()).toHaveTextContent("Total₹2,000");
    const consent = screen.getByText(/By paying you agree to the/);
    expect(consent).toHaveTextContent(
      "By paying you agree to the Terms of service and the Cancellation and refunds policy.",
    );
    expect(within(consent).getByRole("link", { name: "Terms of service" })).toHaveAttribute("target", "_blank");
    expect(within(consent).getByRole("link", { name: "Cancellation and refunds policy" })).toHaveAttribute(
      "href",
      "/refunds",
    );
    expect(payIn(summary(), "₹2,000")).toHaveAccessibleDescription(consent.textContent ?? "");
    expect(within(summary()).getByRole("list", { name: "Ways to pay" })).toHaveTextContent("UPICardsNetbankingWallets");
    expect(screen.queryByText(/What changes on/)).not.toBeInTheDocument();
  });

  it("pays: records consent with the checkout, shows the interstitial and leaves for Razorpay", async () => {
    const { calls, user } = await setup();
    await user.click(payIn(summary(), "₹2,000"));
    expect(calls.find((call) => call.path === "/billing/checkout")?.body).toEqual({
      plan_id: 2,
      kind: "period",
      accept_terms: true,
    });
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    const redirect = screen.getByRole("status", { name: "" });
    expect(redirect).toHaveTextContent("Opening Razorpay…");
    expect(redirect).toHaveTextContent(
      "You'll pay ₹2,000 on Razorpay's secure page, then come back here automatically.",
    );
    expect(leaveFor).toHaveBeenCalledExactlyOnceWith("https://rzp.io/rzp/q3Zx8Lm");
    expect(await screen.findByRole("link", { name: "Continue to Razorpay" }, { timeout: 4_000 })).toHaveAttribute(
      "href",
      "https://rzp.io/rzp/q3Zx8Lm",
    );
    act(() => {
      window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }));
    });
    expect(screen.queryByText("Opening Razorpay…")).not.toBeInTheDocument();
  });

  it("saves changed receipt details before paying, from a collapsed block", async () => {
    const { calls, user } = await setup({
      catalog: makeCatalog({ payments: makePaymentsInfo({ email_reminders: true }) }),
    });
    const toggle = screen.getByRole("button", { name: "Receipt details (optional)" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByText("Saved to your account for next time.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Name on the receipt"), "Maya Chen");
    await user.type(screen.getByLabelText("Email for reminders"), "maya@example.com");
    expect(screen.getByLabelText("Email for reminders")).toHaveAccessibleDescription(
      "Used only to remind you before your plan ends.",
    );
    await user.click(payIn(summary(), "₹2,000"));
    const sent = calls.filter((call) => call.method !== "GET").map((call) => [call.method, call.path, call.body]);
    expect(sent).toEqual([
      ["PUT", "/billing/profile", { name: "Maya Chen", email: "maya@example.com" }],
      ["POST", "/billing/checkout", { plan_id: 2, kind: "period", accept_terms: true }],
    ]);
  });

  it("asks for no email when reminder emails can't be sent, and stops on a refused profile", async () => {
    const { calls, user } = await setup({}, { "PUT /billing/profile": () => errorResponse(422, "name: too long") });
    await user.click(screen.getByRole("button", { name: "Receipt details (optional)" }));
    expect(screen.queryByLabelText("Email for reminders")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Name on the receipt"), "Maya");
    await user.click(payIn(summary(), "₹2,000"));
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("Couldn't save your receipt details. name: too long");
    expect(error).toHaveFocus();
    expect(calls.some((call) => call.path === "/billing/checkout")).toBe(false);
  });

  it("shows an upgrade's credit, the new period starting now and what changes", async () => {
    const option = makeCheckoutOption({
      kind: "now",
      label: "Upgrade now · ₹1,700",
      amount_paise: 170_000,
      credit: { description: "Credit for unused Starter, 20 Oct to 7 Nov", amount_paise: 30_000 },
      changes: ["Claude Opus 5.5 and GPT-5.5 won't be available."],
      notes: ["Your scheduled Starter period moves to after this one."],
    });
    const me = makeMe({ plan: makePaidPlanAccess({ plan: { id: 1, code: "starter", name: "Starter" } }) });
    await setup({ me, catalog: catalogWith([option]), route: "/plans/checkout/standard?kind=now" });
    expect(
      await screen.findByText("Starts now. Your Starter period ends today, and its unused part is credited below."),
    ).toBeInTheDocument();
    expect(screen.getByText("Your scheduled Starter period moves to after this one.")).toBeInTheDocument();
    expect(summary()).toHaveTextContent("Credit for unused Starter, 20 Oct to 7 Nov−minus ₹300");
    expect(summary()).toHaveTextContent("Total₹1,700");
    const changes = screen.getByRole("region", { name: "What changes on 20 Oct" });
    expect(changes).toHaveTextContent("Claude Opus 5.5 and GPT-5.5 won't be available.");
  });

  it("dates a period that starts when the current one ends", async () => {
    const option = makeCheckoutOption({
      label: "Switch on 8 Nov",
      starts_at: "2026-11-07T18:30:00Z",
      ends_at: "2026-12-07T18:30:00Z",
      valid_through: "2026-12-07",
    });
    await setup({ catalog: catalogWith([option]) });
    expect(await screen.findByText("30 days: valid 8 Nov to 7 Dec 2026")).toBeInTheDocument();
    expect(screen.getByText("Starts 8 Nov 2026, when your current plan ends.")).toBeInTheDocument();
  });

  it("notes the UPI limit above it, and shows testers the test notice", async () => {
    const professional = await setup({
      catalog: makeCatalog({ payments: makePaymentsInfo({ mode: "test" }) }),
      route: "/plans/checkout/professional?kind=period",
    });
    expect(await screen.findByText(/UPI is limited to/)).toHaveTextContent(
      "UPI is limited to ₹2,000 per payment for now. Pay ₹5,000 by netbanking, debit card or credit card on the next page.",
    );
    expect(screen.getByText(/no real money moves/)).toBeInTheDocument();
    expect(screen.getByText("1 day: valid 20 Oct to 19 Nov 2026")).toBeInTheDocument();
    professional.view.unmount();

    await setup();
    expect(screen.queryByText(/UPI is limited to/)).not.toBeInTheDocument();
  });

  it.each([
    [409, "checkout_unavailable", "You already have a period paid ahead.", "Back to plans"],
    [502, "payment_provider_error", "The payment service didn't answer. Try again in a minute.", "Try again"],
  ])("explains a %i with what to do next", async (status, code, detail, action) => {
    const { user } = await setup({}, { "POST /billing/checkout": () => jsonResponse({ detail, code }, { status }) });
    await user.click(payIn(summary(), "₹2,000"));
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent(detail);
    expect(within(error).getByRole(action === "Try again" ? "button" : "link", { name: action })).toBeInTheDocument();
    expect(leaveFor).not.toHaveBeenCalled();
    expect(payIn(summary(), "₹2,000")).not.toHaveAttribute("aria-busy");
  });

  it("tries again after a provider error", async () => {
    let fail = true;
    const { calls, user } = await setup(
      {},
      {
        "POST /billing/checkout": () =>
          fail
            ? jsonResponse(
                { detail: "The payment service didn't answer.", code: "payment_provider_error" },
                { status: 502 },
              )
            : jsonResponse(STARTED, { status: 201 }),
      },
    );
    await user.click(payIn(summary(), "₹2,000"));
    fail = false;
    await user.click(within(await screen.findByRole("alert")).getByRole("button", { name: "Try again" }));
    expect(calls.filter((call) => call.path === "/billing/checkout")).toHaveLength(2);
    expect(leaveFor).toHaveBeenCalledTimes(1);
  });

  it("refuses to leave for a payment address that isn't Razorpay's", async () => {
    const { user } = await setup(
      {},
      {
        "POST /billing/checkout": () =>
          jsonResponse({ ...STARTED, pay_url: "https://rzp.io.evil.example/x" }, { status: 201 }),
      },
    );
    await user.click(payIn(summary(), "₹2,000"));
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("The payment page's address didn't look right, so it wasn't opened.");
    expect(within(error).getByRole("link", { name: "Check status" })).toHaveAttribute(
      "href",
      "/billing/return?order=q3Zx8LmP0aB2cD4eF6gH",
    );
    expect(leaveFor).not.toHaveBeenCalled();
  });

  it("accepts the app's own origin as the payment page in test mode (the end-to-end mocks)", async () => {
    const url = `${window.location.origin}/__fake-razorpay/q3Zx8Lm`;
    const { user } = await setup(
      { catalog: makeCatalog({ payments: makePaymentsInfo({ mode: "test" }) }) },
      { "POST /billing/checkout": () => jsonResponse({ ...STARTED, pay_url: url }, { status: 201 }) },
    );
    await user.click(payIn(summary(), "₹2,000"));
    expect(leaveFor).toHaveBeenCalledExactlyOnceWith(url);
  });

  it("puts the total and Pay in a bar for phones", async () => {
    await setup();
    const bar = document.querySelector(".plans-paybar") as HTMLElement;
    expect(bar).toHaveTextContent("₹2,00030 days, paid in advance");
    expect(payIn(bar, "₹2,000")).toBeInTheDocument();
  });

  it.each([
    ["an unknown plan", "/plans/checkout/gold?kind=period", "This plan isn't offered."],
    ["an option not offered", "/plans/checkout/standard?kind=now", "This option isn't available right now."],
  ])("explains %s, with the way back", async (_, route, reason) => {
    await setup({ route });
    const panel = await screen.findByRole("region", { name: "This checkout isn't available" });
    expect(panel).toHaveTextContent(reason);
    expect(within(panel).getByRole("link", { name: "Back to plans" })).toHaveAttribute("href", "/plans");
  });

  it("shows an option's reason when it can't be bought, and that payment isn't set up", async () => {
    const option = makeCheckoutOption({ unavailable_reason: "Online payment isn't set up yet." });
    await setup({ catalog: catalogWith([option], { payments: makePaymentsInfo({ available: false, mode: null }) }) });
    const panel = await screen.findByRole("region", { name: "This checkout isn't available" });
    expect(panel).toHaveTextContent("Online payment isn't set up yet.");
    expect(screen.getByRole("link", { name: "billing@example.com" })).toBeInTheDocument();
  });

  it("tells the admin no plan is needed", async () => {
    await setup({ me: makeAdminMe() });
    expect(await screen.findByText("The admin doesn't need a plan.")).toBeInTheDocument();
  });

  it("reports a failed load with Try again", async () => {
    let fail = true;
    const { user } = await setup({ catalog: () => (fail ? errorResponse(500, null) : makeCatalog()) });
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load this checkout.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Standard" })).toBeInTheDocument();
  });
});
