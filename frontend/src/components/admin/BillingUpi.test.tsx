import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdminOrder, AdminOrderDetail, BillingAdminConfig } from "../../api/types";
import { setMediaQueries } from "../../test/browser";
import {
  makeAdminOrder,
  makeAttentionItem,
  makeBillingAdminConfig,
  makeBillingSummary,
  makeUpiInfo,
} from "../../test/billing";
import { NOW } from "../../test/fixtures";
import { errorResponse, jsonResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import BillingTab from "./BillingTab";

const TO_CHECK = "/admin/billing/orders?status=submitted&page=1";
const RECENT = "/admin/billing/orders?channel=upi_direct&page=1";

/** A direct-UPI order whose UTR waits, sent `hoursAgo` before NOW. */
function waiting(overrides: Partial<AdminOrder> = {}, hoursAgo = 2): AdminOrder {
  return makeAdminOrder({
    public_id: "u7Kp2QwX9mN4bV6cZ8dA",
    status: "submitted",
    channel: "upi_direct",
    mode: "offline",
    pay_url: null,
    razorpay_link_id: null,
    checks: 0,
    last_checked_at: null,
    next_check_at: null,
    user: { id: 11, username: "maya.chen", email: "maya.chen@example.com" },
    upi: makeUpiInfo({
      uri: null,
      utr: "630512345678",
      submitted_at: new Date(Date.parse(NOW) - hoursAgo * 3_600_000).toISOString(),
    }),
    ...overrides,
  });
}

const second = waiting(
  {
    public_id: "secondOrder",
    kind: "now",
    plan: { id: 3, code: "professional", name: "Professional" },
    amount_paise: 342_000,
    user: { id: 12, username: "ravi.k", email: null },
  },
  30,
);
second.upi = { ...second.upi!, reference: "AN-3HQX-8MWD", utr: "512298761234", upi_id: "old.id@ybl" };

const upiConfig = (overrides: Partial<BillingAdminConfig["settings"]> = {}) =>
  makeBillingAdminConfig({
    settings: {
      ...makeBillingAdminConfig().settings,
      upi_enabled: true,
      upi_id: "asha.rao@okhdfcbank",
      upi_payee_name: "Asha Rao",
      ...overrides,
    },
  });

function setup(routes: Record<string, Route> = {}, section = "upi") {
  const calls = stubFetch({
    "GET /admin/billing/config": upiConfig(),
    [`GET ${TO_CHECK}`]: { items: [waiting(), second], total: 2 },
    [`GET ${RECENT}`]: { items: [waiting(), second], total: 2 },
    ...routes,
  });
  const onNavigate = vi.fn();
  const view = render(<BillingTab section={section} params={new URLSearchParams()} onNavigate={onNavigate} />);
  return { calls, onNavigate, view, user: userEvent.setup() };
}

const toCheck = () => screen.getByRole("region", { name: /UPI payments to check/ });
const recent = () => screen.getByRole("region", { name: "Recent UPI payments" });
const posts = (calls: FetchCall[]) => calls.filter((call) => call.method === "POST");
const count = (calls: FetchCall[], path: string) => calls.filter((call) => call.path === path).length;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Billing → UPI", () => {
  it("lists the UTRs to check, the oldest first, with the reminder and the count in the section's label", async () => {
    const { user } = setup();
    expect(await screen.findByRole("radio", { name: "UPI · 2" })).toBeChecked();
    expect(within(toCheck()).getByRole("heading", { name: /^UPI payments to check\s*2\s*waiting$/ })).toBeInTheDocument();
    expect(toCheck()).toHaveTextContent(
      "Before you confirm, open your bank or UPI app and find a credit of exactly the amount with this UTR.",
    );
    const rows = within(toCheck()).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("20 Oct, 13:00");
    expect(rows[0]).toHaveTextContent("2 hours ago");
    expect(rows[0]).toHaveTextContent("maya.chen");
    expect(rows[0]).toHaveTextContent("Standard30 days");
    expect(rows[0]).toHaveTextContent("₹2,000");
    expect(rows[0]).toHaveTextContent("AN-7K4M-Q9TX");
    expect(rows[0]).toHaveTextContent("6305 1234 5678");
    expect(within(rows[0]).queryByText(/^to /)).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("Waiting over a day");
    expect(rows[1]).toHaveTextContent("ProfessionalStarts when confirmed");
    expect(rows[1]).toHaveTextContent("to old.id@ybl");
    await user.click(within(rows[1]).getByRole("button", { name: "Copy the UTR of ravi.k" }));
    expect(await navigator.clipboard.readText()).toBe("512298761234");
  });

  it("shows each UTR as a card on a phone", async () => {
    setMediaQueries((query) => query.includes("max-width: 799.98px"));
    setup();
    const cards = await within(await screen.findByRole("region", { name: /UPI payments to check/ })).findAllByRole(
      "listitem",
    );
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("₹2,000Standard · 30 days");
    expect(cards[0]).toHaveTextContent("ReferenceAN-7K4M-Q9TX");
    expect(within(cards[0]).getByRole("button", { name: "Confirm ₹2,000 from maya.chen" })).toBeInTheDocument();
    expect(within(cards[1]).getByRole("button", { name: "Reject ₹3,420 from ravi.k" })).toBeInTheDocument();
    expect(within(toCheck()).queryByRole("table")).not.toBeInTheDocument();
  });

  it("says when there's nothing to check, or that direct UPI is off", async () => {
    const empty = { items: [], total: 0 };
    const first = setup({ [`GET ${TO_CHECK}`]: empty, [`GET ${RECENT}`]: empty });
    expect(await screen.findByText(/Nothing to check/)).toHaveTextContent(
      "Nothing to check. New UPI payments show up here and in Needs attention.",
    );
    expect(screen.getByRole("radio", { name: "UPI" })).toBeInTheDocument();
    expect(await within(recent()).findByText("No UPI payments yet")).toBeInTheDocument();
    first.view.unmount();

    const { onNavigate, user } = setup({
      "GET /admin/billing/config": makeBillingAdminConfig(),
      [`GET ${TO_CHECK}`]: empty,
      [`GET ${RECENT}`]: empty,
    });
    await user.click(await screen.findByRole("button", { name: "Turn it on in Business & payments." }));
    expect(onNavigate).toHaveBeenCalledWith("billing/business");
  });

  it("confirms only once the credit is ticked, sending the order's UTR", async () => {
    let paid = false;
    const { calls, user } = setup({
      [`GET ${TO_CHECK}`]: () => (paid ? { items: [second], total: 1 } : { items: [waiting(), second], total: 2 }),
      "POST /admin/billing/orders/u7Kp2QwX9mN4bV6cZ8dA/upi-confirm": () => {
        paid = true;
        return waiting({ status: "paid" });
      },
    });
    await user.click(await screen.findByRole("button", { name: "Confirm ₹2,000 from maya.chen" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm UPI payment" });
    expect(dialog).toHaveTextContent("Confirm only after you've found this payment in your bank or UPI app.");
    expect(dialog).toHaveTextContent("Amount₹2,000");
    expect(dialog).toHaveTextContent("UTR6305 1234 5678");
    expect(dialog).toHaveTextContent("ReferenceAN-7K4M-Q9TX");
    expect(dialog).toHaveTextContent("Paid toasha.rao@okhdfcbank");
    expect(dialog).toHaveTextContent("Frommaya.chenmaya.chen@example.com");
    expect(dialog).toHaveTextContent("PlanStandard, 30 days");
    expect(dialog).toHaveTextContent("Sent20 Oct 2026, 13:00 IST");
    const tick = within(dialog).getByRole("checkbox", {
      name: "I found a credit of exactly ₹2,000 with UTR 6305 1234 5678.",
    });
    await waitFor(() => expect(tick).toHaveFocus());
    const confirm = within(dialog).getByRole("button", { name: "Confirm and start the plan" });
    expect(confirm).toHaveAttribute("aria-disabled", "true");
    await user.click(confirm);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Tick the box once you've found this payment.");
    expect(tick).toHaveFocus();
    expect(posts(calls)).toHaveLength(0);

    await user.click(tick);
    await user.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts(calls).map((call) => call.body)).toEqual([{ utr: "630512345678" }]);
    expect(screen.getByText("Confirmed ₹2,000 from maya.chen. Their plan has started.")).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "UPI · 1" })).toBeInTheDocument();
    expect(count(calls, RECENT)).toBe(2);
  });

  it("reports a confirmation that went to review, and a refused one in the dialog", async () => {
    let refuse = true;
    const { user } = setup({
      "POST /admin/billing/orders/secondOrder/upi-confirm": () =>
        refuse
          ? errorResponse(409, "The UTR on this payment is 5122 9876 1234. Check it again.")
          : { ...second, status: "review", review_reason: "The current period changed before the check." },
    });
    await user.click(await screen.findByRole("button", { name: "Confirm ₹3,420 from ravi.k" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm UPI payment" });
    expect(dialog).toHaveTextContent("PlanProfessional, 30 days from the confirmation: replaces the current period");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Confirm and start the plan" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("The UTR on this payment is 5122 9876 1234.");
    refuse = false;
    await user.click(within(dialog).getByRole("button", { name: "Confirm and start the plan" }));
    expect(
      await screen.findByText(
        "Recorded ₹3,420 from ravi.k. It needs your review: The current period changed before the check.",
      ),
    ).toBeInTheDocument();
  });

  it("rejects with a reason the customer reads, and a note required for Something else", async () => {
    const { calls, user } = setup({
      "POST /admin/billing/orders/u7Kp2QwX9mN4bV6cZ8dA/upi-reject": () => waiting({ status: "rejected" }),
    });
    await user.click(await screen.findByRole("button", { name: "Reject ₹2,000 from maya.chen" }));
    const dialog = screen.getByRole("dialog", { name: "Reject UPI payment" });
    expect(dialog).toHaveTextContent("The customer sees why and can start a new payment.");
    const group = within(dialog).getByRole("group", { name: "Why" });
    expect(within(group).getByRole("radio", { name: /No payment with this UTR arrived/ })).toBeChecked();
    expect(group).toHaveTextContent(
      "We couldn't find a payment of ₹2,000 with UTR 6305 1234 5678. If money left your account, write to billing@example.com with a screenshot of the payment.",
    );
    expect(within(dialog).queryByText(/Send what they paid back/)).not.toBeInTheDocument();

    await user.click(within(group).getByRole("radio", { name: /Something else/ }));
    await user.click(within(dialog).getByRole("button", { name: "Reject payment" }));
    const note = within(dialog).getByRole("textbox", { name: "Note to the customer" });
    expect(note).toHaveFocus();
    expect(note).toHaveAccessibleDescription(
      "Tell the customer what went wrong. The customer reads this. Don't include anyone's bank details.",
    );
    expect(posts(calls)).toHaveLength(0);

    await user.click(within(group).getByRole("radio", { name: /The amount is different/ }));
    expect(dialog).toHaveTextContent("Send what they paid back to the payer by UPI from your bank app.");
    await user.type(within(dialog).getByRole("textbox", { name: "Note to the customer (optional)" }), "  We got ₹200.  ");
    await user.click(within(dialog).getByRole("button", { name: "Reject payment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts(calls).map((call) => call.body)).toEqual([{ reason: "amount_differs", note: "We got ₹200." }]);
    expect(screen.getByText("Rejected ₹2,000 from maya.chen. They can start a new payment.")).toBeInTheDocument();
  });

  it("previews the customer's sentences without a contact email, and shows a refused rejection", async () => {
    const { calls, user } = setup({
      "GET /admin/billing/config": upiConfig({ contact_email: null }),
      "POST /admin/billing/orders/u7Kp2QwX9mN4bV6cZ8dA/upi-reject": errorResponse(409, "This payment isn't waiting."),
    });
    await user.click(await screen.findByRole("button", { name: "Reject ₹2,000 from maya.chen" }));
    const dialog = screen.getByRole("dialog", { name: "Reject UPI payment" });
    expect(dialog).toHaveTextContent("If money left your account, write to us from Settings.");
    expect(dialog).toHaveTextContent("UTR 6305 1234 5678 belongs to another payment.");
    await user.click(within(dialog).getByRole("button", { name: "Reject payment" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("This payment isn't waiting.");
    expect(posts(calls).map((call) => call.body)).toEqual([{ reason: "not_received", note: null }]);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lists the latest UPI orders with their status, and opens one to decide from its dialog", async () => {
    const rejected = waiting({
      public_id: "rejectedOrder",
      status: "rejected",
      upi_decided_by: "admin",
      upi: makeUpiInfo({
        uri: null,
        utr: "111122223333",
        submitted_at: "2026-10-19T09:00:00Z",
        decided_at: "2026-10-19T10:00:00Z",
        rejection: { reason: "amount_differs", message: "Not the amount.", note: "We got ₹200." },
      }),
    });
    const open = waiting({ public_id: "openOrder", status: "pending", upi: makeUpiInfo() });
    const detail: AdminOrderDetail = { ...waiting(), events: [] };
    const { user } = setup({
      [`GET ${RECENT}`]: { items: [waiting(), rejected, open], total: 3 },
      "GET /admin/billing/orders/u7Kp2QwX9mN4bV6cZ8dA": detail,
      "GET /admin/billing/orders/rejectedOrder": { ...rejected, events: [] },
    });
    const rows = (await within(await screen.findByRole("region", { name: "Recent UPI payments" })).findAllByRole("row")).slice(1);
    expect(rows[0]).toHaveTextContent("To check");
    expect(rows[1]).toHaveTextContent("RejectedAmount differs");
    expect(rows[2]).toHaveTextContent("Waiting for UTR");

    await user.click(within(rows[1]).getByRole("button", { name: "Open the Standard order of maya.chen" }));
    let dialog = await screen.findByRole("dialog", { name: "Standard order of maya.chen" });
    expect(await within(dialog).findByText("UPI (direct)")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Decided");
    expect(dialog).toHaveTextContent("Rejected by admin on 19 Oct 2026, 15:30 IST");
    expect(dialog).toHaveTextContent("The amount is different: We got ₹200.");
    expect(within(dialog).queryByRole("button", { name: "Confirm…" })).not.toBeInTheDocument();
    await user.click(within(dialog).getAllByRole("button", { name: "Close" })[0]);

    await user.click(within(rows[0]).getByRole("button", { name: "Open the Standard order of maya.chen" }));
    dialog = await screen.findByRole("dialog", { name: "Standard order of maya.chen" });
    expect(await within(dialog).findByText("AN-7K4M-Q9TX")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Paid toAsha Rao · asha.rao@okhdfcbank");
    expect(dialog).toHaveTextContent("UTR6305 1234 5678");
    expect(within(dialog).queryByRole("button", { name: "Check now" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Confirm…" }));
    expect(screen.getByRole("dialog", { name: "Confirm UPI payment" })).toBeInTheDocument();
  });

  it("shows the latest UPI orders as cards on a phone, each status beside its Open", async () => {
    setMediaQueries((query) => query.includes("max-width: 799.98px"));
    const rejected = waiting({
      public_id: "rejectedOrder",
      status: "rejected",
      user: { id: 13, username: "priyanka.venkataraman.docs", email: "priyanka.venkataraman@example.com" },
      upi: makeUpiInfo({
        uri: null,
        utr: "111122223333",
        rejection: { reason: "amount_differs", message: "Not the amount.", note: null },
      }),
    });
    const { user } = setup({
      [`GET ${RECENT}`]: { items: [waiting(), rejected], total: 2 },
      "GET /admin/billing/orders/rejectedOrder": { ...rejected, events: [] },
    });
    const cards = await within(await screen.findByRole("region", { name: "Recent UPI payments" })).findAllByRole(
      "listitem",
    );
    expect(within(recent()).queryByRole("table")).not.toBeInTheDocument();
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent(/^₹2,000Standard · 20 Oct/);
    expect(cards[0]).toHaveTextContent("maya.chen");
    expect(cards[0]).toHaveTextContent("To check");
    expect(cards[1]).toHaveTextContent("priyanka.venkataraman.docs");
    expect(cards[1]).toHaveTextContent("RejectedAmount differs");

    await user.click(
      within(cards[1]).getByRole("button", { name: "Open the Standard order of priyanka.venkataraman.docs" }),
    );
    expect(await screen.findByRole("dialog", { name: "Standard order of priyanka.venkataraman.docs" })).toBeInTheDocument();
  });

  it("leads from Needs attention to the UTRs to check", async () => {
    const { onNavigate, user } = setup(
      {
        "GET /admin/billing/summary?month=2026-10": makeBillingSummary({
          attention: [
            makeAttentionItem({
              kind: "upi_to_check",
              message: "2 UPI payments to check",
              detail: "The oldest: ₹3,420 from ravi.k.",
              order_public_id: "secondOrder",
              payment_id: null,
              count: 2,
            }),
          ],
        }),
        "GET /admin/billing/payments?month=2026-10&page=1": { items: [], total: 0 },
      },
      "overview",
    );
    const row = (await screen.findByText("2 UPI payments to check")).closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Check payments" }));
    expect(onNavigate).toHaveBeenCalledWith("billing/upi");
  });

  it("says Razorpay isn't needed while direct UPI is on", async () => {
    setup(
      {
        "GET /admin/billing/config": makeBillingAdminConfig({
          ...upiConfig(),
          payments: { ...upiConfig().payments, configured: false, mode: null },
        }),
        "GET /admin/billing/summary?month=2026-10": makeBillingSummary(),
        "GET /admin/billing/payments?month=2026-10&page=1": { items: [], total: 0 },
      },
      "overview",
    );
    expect(
      await screen.findByText(
        "Online payment isn't set up yet: users pay by direct UPI until the Razorpay keys are in .env.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the section's label plain when the count can't load", async () => {
    setup({ [`GET ${TO_CHECK}`]: jsonResponse({ detail: "Down" }, { status: 500 }) });
    expect(await screen.findByText("Down")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "UPI" })).toBeChecked();
  });
});
