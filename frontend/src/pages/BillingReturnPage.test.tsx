import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrderView } from "../api/types";
import { makeBillingStatus, makeOrder, makePaidOrder, makePaymentsInfo } from "../test/billing";
import { makeLapsedPlanAccess, makeMe, makePaidPlanAccess, NOW } from "../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../test/http";
import { renderWithAuth } from "../test/render";
import BillingReturnPage from "./BillingReturnPage";

const ORDER = "/billing/orders/q3Zx8LmP0aB2cD4eF6gH";

async function setup(
  order: Route,
  { route = `/billing/return?order=q3Zx8LmP0aB2cD4eF6gH`, routes = {} as Record<string, Route> } = {},
) {
  const calls = stubFetch({
    "GET /auth/me": makeMe({ plan: makeLapsedPlanAccess("none") }),
    [`GET ${ORDER}`]: order,
    "GET /billing/status": makeBillingStatus(),
    ...routes,
  });
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  const view = await renderWithAuth(<BillingReturnPage />, { route });
  await act(async () => {});
  return { calls, user, view };
}

const polls = (calls: FetchCall[]) => calls.filter((call) => call.path === `${ORDER}?refresh=true`).length;
const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
const title = () => screen.getByRole("heading", { level: 1 });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"], shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BillingReturnPage", () => {
  it("confirms the payment, then shows the receipt slip and the way into the app", async () => {
    let order: OrderView = makeOrder();
    const { calls, user, view } = await setup(() => order);
    expect(title()).toHaveTextContent("Confirming your payment…");
    expect(screen.getByText("This usually takes a few seconds. You can keep this page open.")).toBeInTheDocument();
    expect(screen.getByText("Standard · ₹2,000")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("aria-live", "polite");

    order = makePaidOrder({ credit_paise: 30_000, test_mode: true });
    await advance(3_000);
    expect(title()).toHaveTextContent("Payment received");
    expect(title()).toHaveFocus();
    expect(screen.getByText(/^You're on/)).toHaveTextContent("You're on Standard until 19 Nov 2026.");
    const slip = document.querySelector(".plans-receipt") as HTMLElement;
    expect(slip).toHaveTextContent("PlanTest modeStandard, 1 day");
    expect(slip).toHaveTextContent("Period20 Oct to 19 Nov 2026");
    expect(slip).toHaveTextContent("Paid withUPI");
    expect(slip).toHaveTextContent("ReceiptRA/26-27/000041");
    expect(slip).toHaveTextContent("Total paid₹2,000after ₹300 credit");
    expect(screen.getByRole("link", { name: "Download receipt" })).toHaveAttribute("href", "/api/billing/receipts/41");
    expect(screen.getByText("Razorpay also emails a confirmation to the address you gave it.")).toBeInTheDocument();
    expect(polls(calls)).toBe(2);
    await advance(30_000);
    expect(polls(calls)).toBe(2);

    const mes = calls.filter((call) => call.path === "/auth/me").length;
    await user.click(screen.getByRole("button", { name: "Start chatting" }));
    expect(calls.filter((call) => call.path === "/auth/me").length).toBe(mes);
    await vi.waitFor(() => expect(view.location()).toBe("/"));
  });

  it("says a stacked period continues the plan", async () => {
    await setup(
      makePaidOrder({
        period_start: "2026-11-19T18:30:00Z",
        period_end: "2026-12-19T18:30:00Z",
        valid_through: "2026-12-19",
      }),
    );
    expect(screen.getByText(/continues from/)).toHaveTextContent(
      "Your Standard plan continues from 20 Nov to 19 Dec 2026.",
    );
  });

  it("polls every 3 s for a minute, then every 15 s, then stops after 10 minutes with Check again", async () => {
    const { calls, user } = await setup(makeOrder());
    expect(polls(calls)).toBe(1);
    await advance(57_000);
    expect(polls(calls)).toBe(20);
    expect(title()).toHaveTextContent("Confirming your payment…");
    await advance(3_000);
    expect(title()).toHaveTextContent("Still waiting for Razorpay");
    await advance(15_000);
    expect(polls(calls)).toBe(22);
    await advance(540_000);
    expect(title()).toHaveTextContent("No answer from Razorpay yet");
    const stoppedAt = polls(calls);
    await advance(60_000);
    expect(polls(calls)).toBe(stoppedAt);

    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(polls(calls)).toBe(stoppedAt + 1);
    expect(title()).toHaveTextContent("Confirming your payment…");
  });

  it("offers to resume or cancel a payment still waiting after a minute", async () => {
    let order = makeOrder();
    const { calls, user } = await setup(() => order, {
      routes: {
        [`POST ${ORDER}/cancel`]: () => {
          order = makeOrder({ status: "cancelled", pay_url: null });
          return order;
        },
      },
    });
    await advance(60_000);
    expect(screen.getByText(/you can go back to it/)).toHaveTextContent(
      "If you closed Razorpay's page before paying, you can go back to it. We keep checking either way.",
    );
    expect(screen.getByRole("link", { name: "Resume payment" })).toHaveAttribute("href", "https://rzp.io/rzp/q3Zx8Lm");
    expect(screen.getByRole("link", { name: "Choose another plan" })).toHaveAttribute("href", "/plans");
    await user.click(screen.getByRole("button", { name: "Cancel this payment" }));
    expect(calls.some((call) => call.method === "POST" && call.path === `${ORDER}/cancel`)).toBe(true);
    await vi.waitFor(() => expect(title()).toHaveTextContent("Payment not completed"));
  });

  it("says when the return couldn't be verified", async () => {
    await setup(makeOrder(), { route: "/billing/return?order=q3Zx8LmP0aB2cD4eF6gH&check=failed" });
    expect(screen.getByText(/couldn't verify the return/)).toHaveTextContent(
      "We couldn't verify the return from Razorpay. If you paid, it will be confirmed automatically within a few minutes.",
    );
  });

  it("explains a checkout that ended without a payment, with the last failure and Try again", async () => {
    await setup(makeOrder({ status: "expired", pay_url: null, last_payment_error: "Your bank declined the payment." }));
    expect(title()).toHaveTextContent("Payment not completed");
    expect(title()).toHaveFocus();
    expect(screen.getByText(/Last attempt:/).parentElement).toHaveTextContent("Last attempt: Your bank declined the payment.");
    expect(screen.getByText("Started").nextElementSibling).toHaveTextContent("Today, 14:58");
    expect(screen.getByRole("link", { name: "Try again" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );
    expect(screen.getByRole("link", { name: "Back to plans" })).toHaveAttribute("href", "/plans");
    expect(screen.getByText(/If money left your account/)).toBeInTheDocument();
  });

  it("asks not to pay again while a payment is being checked", async () => {
    await setup(makeOrder({ status: "review" }));
    expect(title()).toHaveTextContent("We're checking your payment");
    expect(screen.getByText(/Please don't pay again\./)).toBeInTheDocument();
  });

  it("says a payment isn't the user's, and doesn't ask without an order", async () => {
    const first = await setup(() => errorResponse(404, "Not found"));
    expect(title()).toHaveTextContent("This payment isn't in your account");
    first.view.unmount();

    const { calls } = await setup(makeOrder(), { route: "/billing/return" });
    expect(title()).toHaveTextContent("This payment isn't in your account");
    expect(polls(calls)).toBe(0);
  });

  it("keeps checking through a failed request", async () => {
    let fail = true;
    const { calls } = await setup(() => (fail ? errorResponse(502, "Bad gateway") : makePaidOrder()));
    expect(title()).toHaveTextContent("Confirming your payment…");
    fail = false;
    await advance(3_000);
    expect(polls(calls)).toBe(2);
    expect(title()).toHaveTextContent("Payment received");
  });

  it("asks once for an email for reminders when they can be sent", async () => {
    const { calls, user } = await setup(makePaidOrder(), {
      routes: {
        "GET /billing/status": makeBillingStatus({
          plan: makePaidPlanAccess(),
          payments: makePaymentsInfo({ email_reminders: true }),
          profile: { name: "Maya Chen", email: null },
        }),
        "PUT /billing/profile": (call: FetchCall) => call.body as object,
      },
    });
    const ask = await screen.findByLabelText("Remind me before my plan ends");
    await user.type(ask, "maya@example.com");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ name: "Maya Chen", email: "maya@example.com" });
    expect(await screen.findByText("We'll email you before 19 Nov.")).toBeInTheDocument();
  });

  it("lets the reminder ask be dismissed, and doesn't ask without reminder emails", async () => {
    const first = await setup(makePaidOrder(), {
      routes: { "GET /billing/status": makeBillingStatus({ payments: makePaymentsInfo({ email_reminders: true }) }) },
    });
    const form = (await screen.findByLabelText("Remind me before my plan ends")).closest("form") as HTMLElement;
    await first.user.click(within(form).getByRole("button", { name: "Not now" }));
    expect(screen.queryByLabelText("Remind me before my plan ends")).not.toBeInTheDocument();
    first.view.unmount();

    await setup(makePaidOrder());
    await act(async () => {});
    expect(screen.queryByLabelText("Remind me before my plan ends")).not.toBeInTheDocument();
  });

  it("shows a refused reminder email", async () => {
    const { user } = await setup(makePaidOrder(), {
      routes: {
        "GET /billing/status": makeBillingStatus({ payments: makePaymentsInfo({ email_reminders: true }) }),
        "PUT /billing/profile": () => errorResponse(422, "email: not a valid address"),
      },
    });
    await user.type(await screen.findByLabelText("Remind me before my plan ends"), "maya@");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("email: not a valid address");
  });
});
