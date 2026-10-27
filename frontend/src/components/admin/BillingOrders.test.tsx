import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdminOrderDetail } from "../../api/types";
import { makeAdminOrder } from "../../test/billing";
import { NOW } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import BillingOrders from "./BillingOrders";

const pending = makeAdminOrder({ next_check_at: "2026-10-20T09:30:30Z" });
const review = makeAdminOrder({
  id: 72,
  public_id: "rev123",
  status: "review",
  user: { id: 9, username: "sam.ortiz" },
  review_reason: "Amount differs: paid ₹1,999, expected ₹2,000",
  next_check_at: null,
  pay_url: null,
});
const paused = makeAdminOrder({
  id: 73,
  public_id: "old456",
  status: "expired",
  paused: true,
  test_mode: true,
  mode: "test",
  last_error: "Razorpay didn't answer in time",
  user: { id: null, username: "gone.user" },
});
const offline = makeAdminOrder({
  id: 74,
  public_id: "off789",
  status: "paid",
  mode: "offline",
  razorpay_link_id: null,
});
const detail: AdminOrderDetail = {
  ...review,
  events: [
    {
      at: "2026-10-20T09:28:00Z",
      source: "app",
      type: "order.created",
      detail: { amount_paise: 200_000 },
      error: null,
    },
    { at: "2026-10-20T09:31:00Z", source: "callback", type: "order.review", detail: {}, error: "Amount differs" },
  ],
};

function setup(routes: Record<string, Route> = {}, orderParam: string | null = null) {
  const calls = stubFetch({
    "GET /admin/billing/orders": { items: [pending, review, paused, offline], total: 4 },
    "GET /admin/billing/orders?status=review&page=1": { items: [review], total: 1 },
    "GET /admin/billing/orders/rev123": detail,
    ...routes,
  });
  const onParamsRead = vi.fn();
  const onGrant = vi.fn();
  render(<BillingOrders orderParam={orderParam} onParamsRead={onParamsRead} onGrant={onGrant} />);
  return { calls, onParamsRead, onGrant };
}

const requests = (calls: FetchCall[], method = "GET") =>
  calls.filter((call) => call.method === method).map((call) => call.path);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BillingOrders", () => {
  it("lists the orders with their checks, pauses and errors, and filters by status with the review count", async () => {
    const { calls } = setup();
    const user = userEvent.setup();
    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(within(rows[0]).getByText("Pending")).toBeInTheDocument();
    expect(within(rows[0]).getByText("3 checks · next in 30 seconds")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Paused")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Test")).toBeInTheDocument();
    expect(within(rows[2]).getByText("deleted")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Razorpay didn't answer in time")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Bank transfer")).toBeInTheDocument();

    const filter = screen.getByRole("radiogroup", { name: "Order status" });
    expect(await within(filter).findByRole("radio", { name: "Review 1" })).toBeInTheDocument();
    await user.click(within(filter).getByRole("radio", { name: "Paid" }));
    await waitFor(() => expect(requests(calls)).toContain("/admin/billing/orders?status=paid&page=1"));
  });

  it("marks an order under review that was resolved, and counts only those still to review", async () => {
    const resolved = {
      ...review,
      id: 99,
      public_id: "done99",
      user: { id: 10, username: "lee.park" },
      review_note: "Refunded",
    };
    let open = true;
    const user = userEvent.setup();
    setup({
      "GET /admin/billing/orders": { items: [review, resolved], total: 2 },
      // The server lists only the orders still to review under status=review (SPEC, admin billing).
      "GET /admin/billing/orders?status=review&page=1": () =>
        open ? { items: [review], total: 1 } : { items: [], total: 0 },
      "POST /admin/billing/orders/rev123/resolve": () => {
        open = false;
        return { ...review, review_note: "Refunded the difference" };
      },
    });
    const filter = screen.getByRole("radiogroup", { name: "Order status" });
    expect(await within(filter).findByRole("radio", { name: "Review 1" })).toBeInTheDocument();
    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(within(rows[0]).queryByText("Resolved")).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("Resolved")).toBeInTheDocument();

    // Resolving keeps the status `review`, and the count follows.
    await user.click(screen.getByRole("button", { name: "Open the Standard order of sam.ortiz" }));
    const dialog = await screen.findByRole("dialog", { name: "Standard order of sam.ortiz" });
    await user.click(await within(dialog).findByRole("button", { name: "Mark resolved…" }));
    await user.type(within(dialog).getByRole("textbox", { name: "Note" }), "Refunded the difference");
    await user.click(within(dialog).getByRole("button", { name: "Mark resolved" }));
    expect(await screen.findByText("Marked the order as resolved.")).toBeInTheDocument();
    expect(await within(filter).findByRole("radio", { name: "Review" })).toBeInTheDocument();
  });

  it("says when there are no orders, and pages through many", async () => {
    const { calls } = setup({ "GET /admin/billing/orders?page=1": { items: [pending], total: 75 } });
    const user = userEvent.setup();
    expect(await screen.findByText("1–50 of 75")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(requests(calls)).toContain("/admin/billing/orders?page=2"));
  });

  it("shows an order in review with its events, checks it now and marks it resolved with a note", async () => {
    const resolved = { ...review, review_note: "Refunded the difference" };
    const { calls } = setup({
      "POST /admin/billing/orders/rev123/check": review,
      "POST /admin/billing/orders/rev123/resolve": resolved,
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Open the Standard order of sam.ortiz" }));
    const dialog = await screen.findByRole("dialog", { name: "Standard order of sam.ortiz" });
    expect(await within(dialog).findByText("Amount differs: paid ₹1,999, expected ₹2,000")).toBeInTheDocument();
    const events = within(dialog).getByRole("region", { name: "Events" });
    expect(within(events).getByText("amount_paise=200000")).toBeInTheDocument();
    expect(within(events).getByText("Amount differs")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Check now" }));
    expect(await screen.findByText("Checked with Razorpay.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Mark resolved…" }));
    const form = within(dialog).getByRole("form", { name: "Mark resolved" });
    expect(within(form).getByRole("textbox", { name: "Note" })).toHaveFocus();
    await user.click(within(form).getByRole("button", { name: "Mark resolved" }));
    expect(within(form).getByRole("textbox", { name: "Note" })).toHaveAccessibleDescription(
      expect.stringContaining("This field is required."),
    );
    await user.type(within(form).getByRole("textbox", { name: "Note" }), "Refunded the difference");
    await user.click(within(form).getByRole("button", { name: "Mark resolved" }));
    expect(await screen.findByText("Marked the order as resolved.")).toBeInTheDocument();
    expect(within(dialog).getByText("Refunded the difference")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Mark resolved…" })).not.toBeInTheDocument();
    expect(requests(calls, "POST")).toEqual([
      "/admin/billing/orders/rev123/check",
      "/admin/billing/orders/rev123/resolve",
    ]);
  });

  it("opens the order from the hash and grants instead", async () => {
    const { onParamsRead, onGrant } = setup({}, "rev123");
    const user = userEvent.setup();
    const dialog = await screen.findByRole("dialog", { name: "Standard order of sam.ortiz" });
    expect(onParamsRead).toHaveBeenCalled();
    await user.click(await within(dialog).findByRole("button", { name: "Grant instead" }));
    expect(onGrant).toHaveBeenCalledWith(9);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a failed load and a failed check in the dialog", async () => {
    let failing = true;
    setup(
      {
        "GET /admin/billing/orders/rev123": () => {
          if (!failing) return detail;
          failing = false;
          return errorResponse(500, "Couldn't read the order.");
        },
        "POST /admin/billing/orders/rev123/check": () => errorResponse(502, "The payment service didn't answer."),
      },
      "rev123",
    );
    const user = userEvent.setup();
    const dialog = await screen.findByRole("dialog", { name: "Order" });
    expect(await within(dialog).findByText("Couldn't read the order.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    await user.click(await within(dialog).findByRole("button", { name: "Check now" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("The payment service didn't answer.");
  });
});
