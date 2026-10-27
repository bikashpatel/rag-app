import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdminPayment } from "../../api/types";
import { makeAdminPayment, makeRefund } from "../../test/billing";
import { makeAdminUser, NOW } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import BillingPayments from "./BillingPayments";

const upgrade = makeAdminPayment({
  id: 60,
  kind: "now",
  amount_paise: 170_000,
  refundable_paise: 140_000,
  credit_used_paise: 30_000,
  status: "partially_refunded",
  refunded_paise: 30_000,
  refunds: [
    makeRefund({ amount_paise: 30_000 }),
    makeRefund({
      id: 6,
      status: "failed",
      last_error: "The refund amount is more than the amount left.",
      initiated_by: "razorpay",
      admin_username: null,
    }),
  ],
  disputes: [
    {
      id: 1,
      razorpay_dispute_id: "disp_1",
      amount_paise: 170_000,
      status: "action_required",
      reason_code: "fraud",
      respond_by: "2026-12-04",
      created_at: "2026-10-19T00:00:00Z",
      updated_at: "2026-10-19T00:00:00Z",
    },
    {
      id: 2,
      razorpay_dispute_id: "disp_2",
      amount_paise: 1_000,
      status: "won",
      reason_code: null,
      respond_by: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-05T00:00:00Z",
    },
  ],
});
const offline = makeAdminPayment({
  id: 61,
  method: "bank_transfer",
  mode: "offline",
  razorpay_payment_id: null,
  offline_reference: "UTR 4471",
  fee_paise: null,
  fee_tax_paise: null,
  receipt: null,
  period_start: null,
  period_end: null,
});
const page = (items: AdminPayment[], total = items.length) => ({ items, total });

type Props = { userParam?: number | null; paymentParam?: number | null };

function setup(routes: Record<string, Route> = {}, { userParam = null, paymentParam = null }: Props = {}) {
  const calls = stubFetch({
    "GET /admin/users": [
      makeAdminUser({ id: 1, username: "admin", is_admin: true }),
      makeAdminUser(),
      makeAdminUser({ id: 9, username: "sam.ortiz" }),
    ],
    "GET /admin/billing/payments": page([makeAdminPayment(), upgrade, offline]),
    ...routes,
  });
  const onParamsRead = vi.fn();
  const onRecord = vi.fn();
  render(
    <BillingPayments
      month="2026-10"
      userParam={userParam}
      paymentParam={paymentParam}
      onParamsRead={onParamsRead}
      onRecord={onRecord}
      version={0}
    />,
  );
  return { calls, onParamsRead, onRecord };
}

const listRequests = (calls: FetchCall[]) =>
  calls.filter((call) => call.path.startsWith("/admin/billing/payments")).map((call) => call.path);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BillingPayments", () => {
  it("lists the month's payments and filters by user, status and every month, with pages", async () => {
    const { calls, onRecord } = setup({
      "GET /admin/billing/payments?month=2026-10&page=1": page([makeAdminPayment()], 120),
    });
    const user = userEvent.setup();
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1]).getByText("Paid")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("link", { name: "RA/26-27/000033" })).toHaveAttribute(
      "href",
      "/api/admin/billing/receipts/33",
    );
    expect(screen.getByRole("navigation", { name: "Payments pages" })).toHaveTextContent("1–50 of 120");

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "User" }), "sam.ortiz");
    await user.click(screen.getByRole("radio", { name: "Refunded" }));
    await user.click(screen.getByRole("checkbox", { name: "All months" }));
    await waitFor(() =>
      expect(listRequests(calls)).toContain("/admin/billing/payments?user_id=9&status=refunded&page=1"),
    );
    expect(listRequests(calls)).toEqual([
      "/admin/billing/payments?month=2026-10&page=1",
      "/admin/billing/payments?month=2026-10&page=2",
      "/admin/billing/payments?month=2026-10&user_id=9&page=1",
      "/admin/billing/payments?month=2026-10&user_id=9&status=refunded&page=1",
      "/admin/billing/payments?user_id=9&status=refunded&page=1",
    ]);
    expect(screen.queryByRole("combobox", { name: "User" })?.querySelectorAll("option")).toHaveLength(3);

    await user.click(screen.getByRole("button", { name: "Record bank transfer" }));
    expect(onRecord).toHaveBeenCalled();
  });

  it("says when nothing matches", async () => {
    setup({ "GET /admin/billing/payments": page([]) });
    expect(await screen.findByText("Nothing matches these filters in this month.")).toBeInTheDocument();
  });

  it("opens a payment with its fee, disputes and refunds", async () => {
    setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Open the payment of ₹1,700/ }));
    const dialog = screen.getByRole("dialog", { name: "Payment of ₹1,700" });
    expect(within(dialog).getByText("8 Oct 2026, 10:42 IST")).toBeInTheDocument();
    expect(within(dialog).getByText("₹300 of this payment went as credit to a later upgrade.")).toBeInTheDocument();
    expect(within(dialog).getByText("pay_Q3Zx8LmP0aB2cD")).toBeInTheDocument();
    expect(within(dialog).getByText("₹40 incl. ₹7.20 GST")).toBeInTheDocument();
    const disputes = within(dialog).getByRole("region", { name: "Disputes" });
    expect(within(disputes).getByText("Action required")).toBeInTheDocument();
    expect(within(disputes).getByText("Respond in Razorpay's Dashboard by 4 Dec 2026")).toBeInTheDocument();
    expect(within(disputes).getByText("Won")).toBeInTheDocument();
    const refunds = within(dialog).getByRole("region", { name: "Refunds" });
    expect(within(refunds).getByText(/By admin on 12 Oct 2026 · Asked for a partial refund/)).toBeInTheDocument();
    expect(within(refunds).getByText(/From Razorpay's Dashboard/)).toBeInTheDocument();
    expect(within(refunds).getByText("Razorpay: The refund amount is more than the amount left.")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Download receipt" })).toHaveAttribute(
      "href",
      "/api/admin/billing/receipts/33",
    );
    await user.click(within(dialog).getAllByRole("button", { name: "Close" })[1]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("refunds all that's refundable as null, ending access, and shows the result", async () => {
    const refunded = { ...upgrade, refundable_paise: 0, status: "refunded" as const, refunded_paise: 170_000 };
    const { calls } = setup({ "POST /admin/billing/payments/60/refund": refunded });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Open the payment of ₹1,700/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Refund…" }));
    const form = within(dialog).getByRole("form", { name: "Refund" });
    expect(within(form).getByRole("textbox", { name: "Amount" })).toHaveValue("1400");
    expect(within(form).getByRole("textbox", { name: "Amount" })).toHaveFocus();
    expect(within(form).getByText(/₹300 of this payment went as credit to an upgrade\./)).toBeInTheDocument();
    expect(within(form).getByRole("checkbox", { name: /End access now/ })).toBeChecked();
    expect(within(form).getByRole("checkbox", { name: /End access now/ })).toBeDisabled();
    expect(
      within(form).getByText("The refund goes back to the payer's account in 5 to 7 working days."),
    ).toBeInTheDocument();

    await user.click(within(form).getByRole("button", { name: "Refund ₹1,400" }));
    expect(within(form).getByRole("textbox", { name: "Reason" })).toHaveAccessibleDescription(
      expect.stringContaining("This field is required."),
    );
    await user.type(within(form).getByRole("textbox", { name: "Reason" }), "Cancelled in the first week");
    await user.click(within(form).getByRole("button", { name: "Refund ₹1,400" }));
    await waitFor(() => expect(within(dialog).queryByRole("form", { name: "Refund" })).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("Refund of ₹1,400 started.");
    expect(within(dialog).queryByRole("button", { name: "Refund…" })).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST").map((call) => call.body)).toEqual([
      { amount_paise: null, reason: "Cancelled in the first week", end_access: true },
    ]);
  });

  it("checks a partial amount and sends it with the End access choice", async () => {
    const { calls } = setup({
      "POST /admin/billing/payments/57/refund": () => errorResponse(502, "The payment service didn't answer."),
    });
    const user = userEvent.setup();
    await user.click(within((await screen.findAllByRole("row"))[1]).getByRole("button", { name: /^Open/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Refund…" }));
    const form = within(dialog).getByRole("form", { name: "Refund" });
    const amount = within(form).getByRole("textbox", { name: "Amount" });
    await user.type(within(form).getByRole("textbox", { name: "Reason" }), "Goodwill");

    for (const [typed, message] of [
      ["abc", "Enter an amount in rupees, like 500 or 499.50."],
      ["0.5", "Refund at least ₹1."],
      ["2500", "At most ₹2,000 can be refunded."],
    ]) {
      await user.clear(amount);
      await user.type(amount, typed);
      await user.click(within(form).getByRole("button", { name: /^Refund/ }));
      expect(amount).toHaveAccessibleDescription(expect.stringContaining(message));
    }
    await user.clear(amount);
    await user.type(amount, "499.50");
    expect(within(form).getByRole("checkbox", { name: /End access now/ })).toBeEnabled();
    await user.click(within(form).getByRole("checkbox", { name: /End access now/ }));
    await user.click(within(form).getByRole("button", { name: "Refund ₹499.50" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("The payment service didn't answer.");
    expect(calls.filter((call) => call.method === "POST").map((call) => call.body)).toEqual([
      { amount_paise: 49_950, reason: "Goodwill", end_access: true },
    ]);
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(within(dialog).getByRole("button", { name: "Refund…" })).toBeInTheDocument();
  });

  it("records a refund of a bank transfer without Razorpay", async () => {
    setup({
      "POST /admin/billing/payments/61/refund": {
        ...offline,
        refundable_paise: 0,
        status: "refunded",
        refunded_paise: 200_000,
      },
    });
    const user = userEvent.setup();
    const rows = await screen.findAllByRole("row");
    await user.click(within(rows[3]).getByRole("button", { name: /^Open/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("UTR 4471")).toBeInTheDocument();
    expect(within(dialog).getByText(/receipt only, no period/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: "Download receipt" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Refund…" }));
    expect(within(dialog).getByText(/no money moves through Razorpay/)).toBeInTheDocument();
    await user.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Returned by bank");
    await user.click(within(dialog).getByRole("button", { name: "Refund ₹2,000" }));
    expect(await screen.findByText("Recorded a refund of ₹2,000.")).toBeInTheDocument();
  });

  it("opens the payment named in the hash", async () => {
    const { onParamsRead } = setup({ "GET /admin/billing/payments/60": upgrade }, { paymentParam: 60 });
    expect(await screen.findByRole("dialog", { name: "Payment of ₹1,700" })).toBeInTheDocument();
    expect(onParamsRead).toHaveBeenCalled();
  });

  it("explains a payment it can't open, and filters by the user from the hash over every month", async () => {
    const { calls, onParamsRead } = setup(
      { "GET /admin/billing/payments/999": () => errorResponse(404, "Payment not found") },
      { paymentParam: 999, userParam: 9 },
    );
    expect(await screen.findByText("Payment not found")).toBeInTheDocument();
    expect(onParamsRead).toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "All months" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "User" })).toHaveDisplayValue("sam.ortiz");
    expect(listRequests(calls)).toContain("/admin/billing/payments?user_id=9&page=1");
  });
});
