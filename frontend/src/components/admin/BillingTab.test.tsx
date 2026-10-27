import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeSeededAdminPlans } from "../../test/adminTabs";
import {
  makeAdminOrder,
  makeAdminPayment,
  makeAttentionItem,
  makeBillingAdminConfig,
  makeBillingSummary,
} from "../../test/billing";
import { makeAdminUser, NOW } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import BillingTab from "./BillingTab";

const testConfig = makeBillingAdminConfig({
  payments: {
    ...makeBillingAdminConfig().payments,
    mode: "test",
    key_id_hint: "rzp_test_…AbCd",
    test_links_created: 7,
  },
});
const summary = makeBillingSummary({
  attention: [
    makeAttentionItem(),
    makeAttentionItem({
      kind: "refund_failed",
      message: "A refund of ₹500 to maya.chen failed",
      detail: "Razorpay: no balance",
      order_public_id: null,
    }),
    makeAttentionItem({
      kind: "test_mode",
      message: "Test mode",
      detail: null,
      order_public_id: null,
      payment_id: null,
    }),
    makeAttentionItem({
      kind: "paused_orders",
      message: "3 live orders can't be checked",
      detail: null,
      order_public_id: null,
      payment_id: null,
      count: 3,
    }),
    makeAttentionItem({
      kind: "keys_rejected",
      message: "Razorpay rejected the keys",
      detail: null,
      order_public_id: null,
      payment_id: null,
    }),
    makeAttentionItem({
      kind: "duplicate",
      message: "A second payment arrived",
      detail: null,
      order_public_id: "zz9",
      payment_id: null,
    }),
    makeAttentionItem({
      kind: "refund_pending",
      message: "A refund is pending",
      detail: null,
      order_public_id: "yy8",
      payment_id: null,
    }),
  ],
});
const payments = {
  items: [makeAdminPayment(), makeAdminPayment({ id: 58, test_mode: true, user: { id: null, username: "old.user" } })],
  total: 2,
};

const navigations: string[] = [];

function Harness({ initial }: { initial: string }) {
  const [hash, setHash] = useState(initial);
  const [path, search = ""] = hash.split("?");
  const [, section = null] = path.split("/");
  return (
    <BillingTab
      section={section}
      params={new URLSearchParams(search)}
      onNavigate={(target) => {
        navigations.push(target);
        if (target.startsWith("billing")) setHash(target);
      }}
    />
  );
}

function setup(routes: Record<string, Route> = {}, initial = "billing/overview") {
  navigations.length = 0;
  const calls = stubFetch({
    "GET /admin/billing/config": testConfig,
    "GET /admin/billing/summary": summary,
    "GET /admin/billing/payments": payments,
    "GET /admin/users": [makeAdminUser({ id: 1, username: "admin", is_admin: true }), makeAdminUser()],
    "GET /admin/plans": makeSeededAdminPlans(),
    ...routes,
  });
  render(<Harness initial={initial} />);
  return calls;
}

const requested = (calls: FetchCall[], prefix: string) =>
  calls.filter((call) => call.path.startsWith(prefix)).map((call) => call.path);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BillingTab: overview", () => {
  it("shows the test-mode notice, what needs attention, the month's tiles and lists", async () => {
    const calls = setup();
    const user = userEvent.setup();

    expect(await screen.findByText(/7 of about 30 test payment links used\./)).toBeInTheDocument();
    const attention = await screen.findByRole("region", { name: /^Needs attention/ });
    const rows = within(attention).getAllByRole("listitem");
    expect(rows.map((row) => within(row).getByRole("button").textContent)).toEqual([
      "Review",
      "Open payment",
      "Open orders",
      "Set up payments",
      "Review",
      "Open order",
    ]);
    expect(within(attention).queryByText("Test mode")).not.toBeInTheDocument();
    expect(within(attention).getByText("Amount differs: paid ₹1,500, expected ₹2,000")).toBeInTheDocument();
    expect(within(attention).getByRole("heading", { level: 2 })).toHaveTextContent("Needs attention6 items");

    const tiles = screen.getByLabelText("Totals for October 2026");
    expect(within(tiles).getByText("₹9,500")).toBeInTheDocument();
    expect(within(tiles).getByText("5 payments")).toBeInTheDocument();
    expect(within(tiles).getByText("2.36% with GST on the fee")).toBeInTheDocument();
    expect(within(tiles).getByText("1 refund")).toBeInTheDocument();
    expect(within(tiles).getByText("₹8,775.80")).toBeInTheDocument();

    const byPlan = screen.getByRole("region", { name: "By plan" });
    expect(within(byPlan).getByRole("row", { name: /Professional 1 ₹5,000 1 paid · 1 free/ })).toBeInTheDocument();
    expect(within(byPlan).getByText("No plan 2")).toBeInTheDocument();
    const months = screen.getByRole("region", { name: "Last 12 months" });
    expect(
      within(months)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["Sep 2026September 2026:₹0", "OctOctober 2026:₹9,500"]);

    const expiring = screen.getByRole("region", { name: "Expiring in 7 days" });
    expect(within(expiring).getByText(/Standard · 24 Oct · last paid 25 Sep/)).toBeInTheDocument();
    await user.click(within(expiring).getByRole("button", { name: "Open maya.chen" }));
    expect(navigations).toContain("users?user=7");

    const recent = screen.getByRole("region", { name: "Recent payments" });
    expect(within(recent).getByRole("link", { name: "Export CSV" })).toHaveAttribute(
      "href",
      "/api/admin/billing/export.csv?month=2026-10",
    );
    expect(within(recent).getByText("deleted")).toBeInTheDocument();
    expect(within(recent).getByText("Test")).toBeInTheDocument();
    expect(requested(calls, "/admin/billing/summary")).toEqual(["/admin/billing/summary?month=2026-10"]);
    expect(requested(calls, "/admin/billing/payments")).toEqual(["/admin/billing/payments?month=2026-10&page=1"]);

    await user.click(within(attention).getAllByRole("button", { name: "Review" })[0]);
    expect(navigations).toContain("billing/orders?order=k8Yw2NnQ5rS7tU9vW1xY");
  });

  it("offers to set up payments and shows the test-mode item when the setup can't load", async () => {
    setup({
      "GET /admin/billing/config": makeBillingAdminConfig({
        payments: { ...makeBillingAdminConfig().payments, configured: false, mode: null },
      }),
    });
    const user = userEvent.setup();
    expect(await screen.findByText(/Online payment isn't set up yet/)).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Set up payments" })[0]);
    expect(navigations).toContain("billing/business");
  });

  it("keeps every attention item without the setup, and shows an empty month", async () => {
    setup({
      "GET /admin/billing/config": () => errorResponse(500, "Setup unavailable."),
      "GET /admin/billing/summary": makeBillingSummary({
        attention: [
          makeAttentionItem({
            kind: "test_mode",
            message: "Test keys are running",
            detail: null,
            order_public_id: null,
            payment_id: null,
          }),
        ],
        by_plan: [],
        current_users: [],
        users_without_plan: 0,
        expiring: [],
        lapsed: [],
        refunds: 0,
        disputed_paise: 10_000,
      }),
      "GET /admin/billing/payments": { items: [], total: 0 },
    });
    expect(await screen.findByText("Test keys are running")).toBeInTheDocument();
    expect(screen.getByText("No plans sold or held yet")).toBeInTheDocument();
    expect(screen.getAllByText("Nobody")).toHaveLength(2);
    expect(screen.getByText("No payments this month")).toBeInTheDocument();
    expect(screen.getByText("Lost this month")).toBeInTheDocument();
    expect(screen.getByText("None")).toBeInTheDocument();
  });

  it("loads another IST month from the picker and switches sections", async () => {
    const calls = setup();
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "By plan" });
    await user.click(screen.getByRole("button", { name: "Previous month" }));
    await waitFor(() =>
      expect(requested(calls, "/admin/billing/summary")).toContain("/admin/billing/summary?month=2026-09"),
    );
    expect(screen.getByRole("button", { name: "Next month" })).toBeEnabled();

    await user.click(screen.getByRole("radio", { name: "Orders" }));
    expect(navigations).toContain("billing/orders");
    expect(screen.queryByRole("button", { name: "Previous month" })).not.toBeInTheDocument();
  });

  it("records a bank transfer, asking for a note below the plan's price, and reloads the month", async () => {
    const calls = setup({
      "POST /admin/billing/offline-payments": makeAdminPayment({ amount_paise: 150_000, method: "bank_transfer" }),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Record bank transfer" }));
    const dialog = await screen.findByRole("dialog", { name: "Record bank transfer" });
    await within(dialog).findByRole("combobox", { name: "User" });
    expect(within(dialog).getByLabelText("Received on")).toHaveValue("2026-10-20");

    await user.click(within(dialog).getByRole("button", { name: /^Record/ }));
    expect(within(dialog).getByText("Choose the user who paid.")).toBeInTheDocument();
    expect(within(dialog).getByText("Choose the plan it pays for.")).toBeInTheDocument();

    await user.selectOptions(within(dialog).getByRole("combobox", { name: "User" }), "maya.chen");
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "Plan" }), "Standard · ₹2,000");
    expect(within(dialog).getByRole("textbox", { name: "Amount received" })).toHaveValue("2000");
    await user.clear(within(dialog).getByRole("textbox", { name: "Amount received" }));
    await user.type(within(dialog).getByRole("textbox", { name: "Amount received" }), "1500");
    await user.type(within(dialog).getByRole("textbox", { name: "Bank reference" }), "UTR 4471");
    await user.click(within(dialog).getByRole("button", { name: "Record ₹1,500" }));
    expect(within(dialog).getByRole("textbox", { name: "Note" })).toHaveAccessibleDescription(
      expect.stringContaining("Say why the amount is below Standard's price of ₹2,000."),
    );

    await user.type(within(dialog).getByRole("textbox", { name: "Note" }), "Paid the rest in cash");
    await user.click(within(dialog).getByRole("switch", { name: "Start a period" }));
    await user.click(within(dialog).getByRole("button", { name: "Record ₹1,500" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      screen.getAllByRole("status").some((region) => region.textContent === "Recorded ₹1,500 from maya.chen."),
    ).toBe(true);
    expect(calls.filter((call) => call.method === "POST").map((call) => call.body)).toEqual([
      {
        user_id: 7,
        plan_id: 2,
        amount_paise: 150_000,
        received_on: "2026-10-20",
        reference: "UTR 4471",
        note: "Paid the rest in cash",
        apply: false,
      },
    ]);
    await waitFor(() => expect(requested(calls, "/admin/billing/summary")).toHaveLength(2));
  });

  it("shows a refused bank transfer in the dialog and a failed load with a retry", async () => {
    let failing = true;
    setup({
      "GET /admin/plans": () => {
        if (!failing) return makeSeededAdminPlans();
        failing = false;
        return errorResponse(500, "Plans are unavailable.");
      },
      "POST /admin/billing/offline-payments": () =>
        errorResponse(422, "The date can't be before the user was created."),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Record bank transfer" }));
    const dialog = await screen.findByRole("dialog", { name: "Record bank transfer" });
    expect(await within(dialog).findByText("Plans are unavailable.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    await user.selectOptions(await within(dialog).findByRole("combobox", { name: "User" }), "maya.chen");
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "Plan" }), "Starter · ₹500");
    await user.type(within(dialog).getByRole("textbox", { name: "Bank reference" }), "UTR 1");
    await user.click(within(dialog).getByRole("button", { name: "Record ₹500" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The date can't be before the user was created.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("BillingTab: sections", () => {
  it("opens the payment named in the hash, then keeps the plain section hash", async () => {
    setup({ "GET /admin/billing/payments/57": makeAdminPayment() }, "billing/payments?payment=57");
    expect(await screen.findByRole("dialog", { name: "Payment of ₹2,000" })).toBeInTheDocument();
    expect(navigations).toContain("billing/payments");
  });

  it("refunds a recent payment from the overview and reloads the month", async () => {
    const calls = setup({
      "POST /admin/billing/payments/57/refund": makeAdminPayment({
        status: "refunded",
        refunded_paise: 200_000,
        refundable_paise: 0,
      }),
    });
    const user = userEvent.setup();
    const recent = await screen.findByRole("region", { name: "Recent payments" });
    await user.click(await within(recent).findByRole("button", { name: /Open the payment of ₹2,000 by maya\.chen/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Refund…" }));
    await user.type(within(dialog).getByRole("textbox", { name: "Reason" }), "Cancelled");
    await user.click(within(dialog).getByRole("button", { name: "Refund ₹2,000" }));
    expect(await screen.findByText("Refund of ₹2,000 started.")).toBeInTheDocument();
    expect(within(recent).getByText("Refunded")).toBeInTheDocument();
    await waitFor(() => expect(requested(calls, "/admin/billing/summary")).toHaveLength(2));
    await user.click(within(dialog).getAllByRole("button", { name: "Close" })[1]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("records a transfer from the payments section", async () => {
    setup({}, "billing/payments");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Record bank transfer" }));
    expect(await screen.findByRole("dialog", { name: "Record bank transfer" })).toBeInTheDocument();
  });

  it("goes to the user's grant form from an order in review", async () => {
    setup(
      {
        "GET /admin/billing/orders": { items: [], total: 0 },
        "GET /admin/billing/orders/rev1": {
          ...makeAdminOrder({ public_id: "rev1", status: "review", user: { id: 9, username: "sam.ortiz" } }),
          events: [],
        },
      },
      "billing/orders?order=rev1",
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Grant instead" }));
    expect(navigations).toContain("users?user=9&grant=1");
  });
});
