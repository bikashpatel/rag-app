import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrderView } from "../../api/types";
import BillingReturnPage from "../../pages/BillingReturnPage";
import { setMediaQueries } from "../../test/browser";
import {
  makeBillingStatus,
  makePaymentsInfo,
  makePublicSite,
  makeSubmittedUpiOrder,
  makeUpiInfo,
  makeUpiOrder,
} from "../../test/billing";
import { makeLapsedPlanAccess, makeMe, makePaidPlanAccess, NOW } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { renderWithAuth } from "../../test/render";

const ID = "u7Kp2QwX9mN4bV6cZ8dA";
const ORDER = `/billing/orders/${ID}`;
const PHONE = (query: string) => query.includes("max-width: 799.98px");

type Options = { me?: ReturnType<typeof makeMe>; routes?: Record<string, Route>; online?: boolean };

async function setup(order: Route, { me = makeMe({ plan: makeLapsedPlanAccess("none") }), routes = {}, online = true }: Options = {}) {
  const calls = stubFetch({
    "GET /auth/me": me,
    [`GET ${ORDER}`]: order,
    "GET /billing/status": makeBillingStatus({ payments: makePaymentsInfo({ available: online, upi_direct: true }) }),
    "GET /public/site": makePublicSite({ business_name: "Asha Rao Docs" }),
    ...routes,
  });
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  const view = await renderWithAuth(<BillingReturnPage />, { route: `/billing/return?order=${ID}` });
  await act(async () => {});
  return { calls, user, view };
}

const title = () => screen.getByRole("heading", { level: 1 });
const utrField = () => screen.getByRole("textbox", { name: "UTR (12-digit UPI reference number)" });
const send = () => screen.getByRole("button", { name: "I've paid · Send UTR" });
const gets = (calls: FetchCall[]) => calls.filter((call) => call.method === "GET" && call.path.startsWith(ORDER));
const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

/** Replaces the UTR field's text with a paste, as from the UPI app's payment details. */
async function paste(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.clear(utrField());
  await user.paste(text);
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
    shouldAdvanceTime: true,
  });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("The direct-UPI order page", () => {
  it("shows the QR code, the steps and what to pay on a wide screen, without Razorpay's polling", async () => {
    const { calls } = await setup(makeUpiOrder());
    expect(title()).toHaveTextContent("Pay ₹2,000 by UPI");
    await waitFor(() => expect(title()).toHaveFocus());
    expect(screen.getByText(/this request is open until/)).toHaveTextContent(
      "Standard plan, 30 days · this request is open until 21 Oct, 14:58 IST",
    );
    const qr = screen.getByRole("img", { name: "QR code: pay ₹2,000 to Asha Rao by UPI" });
    expect(qr).toHaveAttribute("src", `/api${ORDER}/upi-qr.svg`);
    const save = screen.getByRole("link", { name: "Save QR image" });
    expect(save).toHaveAttribute("href", `/api${ORDER}/upi-qr.png`);
    expect(save).toHaveAttribute("download", "upi-AN-7K4M-Q9TX.png");
    expect(screen.getByText(/Check that it shows/)).toHaveTextContent(
      "Check that it shows Asha Rao and ₹2,000, then pay with your UPI PIN.",
    );
    expect(screen.getByText("asha.rao@okhdfcbank")).toBeInTheDocument();
    expect(screen.getByText("AN-7K4M-Q9TX")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open UPI app" })).not.toBeInTheDocument();
    expect(screen.getByText(/Pay only once/)).toHaveTextContent(
      "Pay only once. If your app says the payment failed or is pending, don't pay again straight away: check your bank balance. Money taken for a failed payment comes back by itself, usually within a day. If the app later shows it as successful, enter its UTR here.",
    );
    expect(screen.getByRole("main")).not.toHaveAttribute("aria-live");
    expect(await screen.findByRole("link", { name: "Pay online instead" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );

    await advance(60_000);
    expect(gets(calls).map((call) => call.path)).toEqual([`${ORDER}?refresh=true`]);
  });

  it("copies the UPI ID, the amount and the note, and selects the text when the clipboard is refused", async () => {
    const { user } = await setup(makeUpiOrder());
    await user.click(screen.getByRole("button", { name: "Copy UPI ID" }));
    expect(await navigator.clipboard.readText()).toBe("asha.rao@okhdfcbank");
    expect(screen.getByText("Copied UPI ID")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy UPI ID" })).toHaveTextContent("Copied");
    await advance(2_000);
    expect(screen.queryByText("Copied UPI ID")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Copy amount" }));
    expect(await navigator.clipboard.readText()).toBe("2000");

    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("Denied"));
    await user.click(screen.getByRole("button", { name: "Copy note" }));
    expect(window.getSelection()?.toString()).toBe("AN-7K4M-Q9TX");
    expect(screen.queryByText("Copied note")).not.toBeInTheDocument();
  });

  it("counts the UTR's digits without its spaces and dashes, announcing only the twelfth and the field's end", async () => {
    const { user } = await setup(makeUpiOrder());
    expect(utrField()).toHaveAttribute("maxlength", "40");
    expect(screen.getByText("0 of 12 digits")).not.toHaveAttribute("aria-live");
    await user.type(utrField(), "6305 1234");
    expect(utrField()).toHaveValue("6305 1234");
    expect(screen.getByText("8 of 12 digits")).not.toHaveAttribute("aria-live");
    expect(send()).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByText("12 digits entered")).not.toBeInTheDocument();

    await user.type(utrField(), "-5678");
    expect(utrField()).toHaveValue("6305 1234-5678");
    expect(screen.getByText(/all 12 digits/).parentElement).toHaveTextContent("6305 1234 5678 (all 12 digits)");
    expect(screen.getByText("12 digits entered")).toHaveAttribute("aria-live", "polite");
    expect(send()).not.toHaveAttribute("aria-disabled");
    expect(utrField()).toHaveAccessibleDescription(
      "12 digits, numbers only. Your UPI app shows it in the payment's details as UTR, UPI Ref No. or UPI transaction ID. 6305 1234 5678 (all 12 digits)",
    );

    await user.type(utrField(), "{Backspace}");
    expect(screen.queryByText("12 digits entered")).not.toBeInTheDocument();
    expect(screen.getAllByText("11 of 12 digits")).toHaveLength(1);
    await user.tab();
    expect(screen.getAllByText("11 of 12 digits").map((node) => node.getAttribute("aria-live"))).toEqual([
      null,
      "polite",
    ]);
  });

  it("keeps every digit of a longer number and says a UTR has exactly 12", async () => {
    const { calls, user } = await setup(makeUpiOrder());
    await paste(user, "1234567890123456");
    expect(utrField()).toHaveValue("1234567890123456");
    const long = "That's 16 digits. A UTR has exactly 12: look for UPI Ref No. or UTR in the payment's details.";
    expect(screen.getByText(long)).not.toHaveAttribute("aria-live");
    expect(screen.queryByText(/all 12 digits/)).not.toBeInTheDocument();
    expect(send()).toHaveAttribute("aria-disabled", "true");
    expect(utrField()).toHaveAccessibleDescription(expect.stringContaining(long));

    await user.click(send());
    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent(long);
    await waitFor(() => expect(error).toHaveFocus());
    expect(screen.getAllByText(long)).toEqual([error]);
    expect(calls.some((call) => call.method === "POST")).toBe(false);

    await paste(user, "UPI Ref No: 6305 1234 5678 9");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/^That's 13 digits\. A UTR has exactly 12/)).toBeInTheDocument();
    await user.tab();
    expect(screen.getAllByText(/^That's 13 digits/).map((node) => node.getAttribute("aria-live"))).toEqual([
      null,
      "polite",
    ]);
  });

  it("says an app's own transaction ID isn't the UTR, and that a UTR has only the digits 0 to 9", async () => {
    const { calls, user } = await setup(makeUpiOrder());
    const id = "This looks like your app's own transaction ID. Look for the 12-digit UTR or UPI reference number.";
    for (const pasted of ["T2511021234567890123456", "Transaction ID: T2511021234567890123456", "CICAgOD6kZbXQA"]) {
      await paste(user, pasted);
      expect(utrField()).toHaveValue(pasted);
      expect(screen.getByText(id)).toBeInTheDocument();
      expect(send()).toHaveAttribute("aria-disabled", "true");
    }
    await user.keyboard("{Enter}");
    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent(id);
    await waitFor(() => expect(error).toHaveFocus());
    expect(screen.getAllByText(id)).toEqual([error]);

    await paste(user, "6305१२341234");
    expect(screen.getByText("A UTR has only the digits 0 to 9.")).toBeInTheDocument();
    await user.click(send());
    expect(screen.getByRole("alert")).toHaveTextContent("A UTR has only the digits 0 to 9.");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("drops a label copied with the UTR and sends only its 12 digits", async () => {
    let order: OrderView = makeUpiOrder();
    const { calls, user } = await setup(() => order, {
      routes: {
        [`POST ${ORDER}/utr`]: () => {
          order = makeSubmittedUpiOrder();
          return order;
        },
      },
    });
    for (const pasted of [
      "UTR: 6305 1234 5678",
      "UPI Ref No: 6305-1234-5678",
      "Ref No. 630512345678",
      "UPI Ref. No.: 6305 1234 5678",
      "UPI transaction ID 630512345678",
      "RRN 630512345678",
      " 6305 – 1234 – 5678 ",
    ]) {
      await paste(user, pasted);
      expect(screen.getByText(/all 12 digits/).parentElement).toHaveTextContent("6305 1234 5678 (all 12 digits)");
    }
    await user.click(send());
    expect(await screen.findByRole("heading", { level: 1, name: "We're checking your payment" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST").map((call) => call.body)).toEqual([{ utr: "630512345678" }]);
  });

  it("asks for all 12 digits before sending", async () => {
    const { calls, user } = await setup(makeUpiOrder());
    await user.type(utrField(), "630512");
    await user.click(send());
    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent("Enter all 12 digits.");
    await waitFor(() => expect(error).toHaveFocus());
    expect(utrField()).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
    await user.type(utrField(), "3");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends the UTR, then shows that the owner is checking it", async () => {
    let order: OrderView = makeUpiOrder();
    const { calls, user } = await setup(() => order, {
      routes: {
        [`POST ${ORDER}/utr`]: () => {
          order = makeSubmittedUpiOrder({ upi: makeUpiInfo({ uri: null, utr: "630512345678", submitted_at: NOW }) });
          return order;
        },
      },
    });
    await user.type(utrField(), "6305 1234 5678{Enter}");
    expect(calls.find((call) => call.path === `${ORDER}/utr`)?.body).toEqual({ utr: "630512345678" });
    const checking = await screen.findByRole("heading", { level: 1, name: "We're checking your payment" });
    await waitFor(() => expect(checking).toHaveFocus());
    expect(screen.getByRole("main")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByText(/We'll match your UTR/)).toHaveTextContent(
      "We'll match your UTR with the ₹2,000 that reached Asha Rao's account. This usually takes a few hours during the day (Indian time). Your Standard plan starts as soon as it's confirmed.",
    );
    const slip = document.querySelector(".plans-receipt") as HTMLElement;
    expect(slip).toHaveTextContent("PlanStandard, 30 days");
    expect(slip).toHaveTextContent("Amount₹2,000");
    expect(slip).toHaveTextContent("ReferenceAN-7K4M-Q9TX");
    expect(slip).toHaveTextContent("UTR6305 1234 5678");
    expect(slip).toHaveTextContent("SentToday, 15:00");
    expect(calls.filter((call) => call.path === "/auth/me").length).toBe(2);
  });

  it("shows the server's refusal of a UTR under the field", async () => {
    const { user } = await setup(makeUpiOrder(), {
      routes: {
        [`POST ${ORDER}/utr`]: errorResponse(
          409,
          "This UTR is already used for another payment. Check the 12-digit number in your UPI app.",
        ),
      },
    });
    await user.type(utrField(), "630512345678");
    await user.click(send());
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("This UTR is already used for another payment.");
    await waitFor(() => expect(error).toHaveFocus());
    expect(title()).toHaveTextContent("Pay ₹2,000 by UPI");
  });

  it("reloads an order that is no longer open and shows it expired", async () => {
    let order: OrderView = makeUpiOrder();
    const { user } = await setup(() => order, {
      routes: {
        [`POST ${ORDER}/utr`]: () => {
          order = makeUpiOrder({ status: "expired", upi: makeUpiInfo({ uri: null }) });
          return new Response(
            JSON.stringify({ detail: "This payment request has expired.", code: "order_not_open" }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        },
      },
    });
    await user.type(utrField(), "630512345678");
    await user.click(send());
    const expired = await screen.findByRole("heading", { level: 1, name: "This payment request has expired" });
    await waitFor(() => expect(expired).toHaveFocus());
    expect(screen.getByText(/No UTR was sent/)).toHaveTextContent(
      "No UTR was sent, so your plan hasn't changed. If you already paid, start a new payment for the same plan and enter that payment's UTR there.",
    );
    expect(screen.getByRole("link", { name: "Start a new payment" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );
  });

  it("cancels the request", async () => {
    const { calls, user } = await setup(makeUpiOrder(), {
      routes: { [`POST ${ORDER}/cancel`]: makeUpiOrder({ status: "cancelled", upi: makeUpiInfo({ uri: null }) }) },
      online: false,
    });
    expect(screen.queryByRole("link", { name: "Pay online instead" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel this request" }));
    expect(await screen.findByRole("heading", { level: 1, name: "This payment request was cancelled" })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual([`${ORDER}/cancel`]);
  });

  it("says when cancelling fails", async () => {
    const { user } = await setup(makeUpiOrder(), {
      routes: { [`POST ${ORDER}/cancel`]: errorResponse(409, "This payment was already sent for checking.") },
    });
    await user.click(screen.getByRole("button", { name: "Cancel this request" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This payment was already sent for checking.");
  });

  it("reads a pending order past its end as expired", async () => {
    await setup(makeUpiOrder({ expires_at: "2026-10-20T09:00:00Z" }));
    expect(title()).toHaveTextContent("This payment request has expired");
  });

  it("opens the UPI app first on a phone, with the QR code behind a toggle", async () => {
    setMediaQueries(PHONE);
    const { user } = await setup(makeUpiOrder());
    const open = screen.getByRole("link", { name: "Open UPI app" });
    expect(open).toHaveAttribute("href", makeUpiInfo().uri);
    expect(screen.getByText("Choose your UPI app, check the name and ₹2,000, and pay.")).toBeInTheDocument();
    expect(screen.queryByText(/On iPhone/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Or pay to this UPI ID" })).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(document.querySelector(".plans-upi-steps")).toBeNull();

    const toggle = screen.getByRole("button", { name: "Show QR code" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Hide QR code" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("img", { name: "QR code: pay ₹2,000 to Asha Rao by UPI" })).toBeInTheDocument();
    expect(screen.getByText(/Upload from gallery/)).toBeInTheDocument();
  });

  it("tells iPhone users that one UPI app opens, and never links a request that isn't upi://pay", async () => {
    setMediaQueries(PHONE);
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)");
    const first = await setup(makeUpiOrder());
    expect(screen.getByText(/On iPhone this opens one UPI app/)).toBeInTheDocument();
    first.view.unmount();

    await setup(makeUpiOrder({ upi: makeUpiInfo({ uri: "javascript:alert(1)" }) }));
    expect(screen.queryByRole("link", { name: "Open UPI app" })).not.toBeInTheDocument();
    expect(screen.getByText("asha.rao@okhdfcbank")).toBeInTheDocument();
  });
});

describe("A direct-UPI payment waiting for the owner", () => {
  it("checks again every 30 s while the tab is visible, for 30 minutes, then offers Check again", async () => {
    const { calls, user } = await setup(makeSubmittedUpiOrder());
    expect(title()).toHaveTextContent("We're checking your payment");
    expect(gets(calls)).toHaveLength(1);
    await advance(30_000);
    expect(gets(calls).map((call) => call.path)).toEqual([`${ORDER}?refresh=true`, ORDER]);

    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    await advance(30_000);
    expect(gets(calls)).toHaveLength(2);
    hidden.mockReturnValue(false);

    await advance(28 * 60_000);
    expect(gets(calls)).toHaveLength(58);
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
    await advance(60_000);
    expect(gets(calls)).toHaveLength(59);
    await advance(60_000);
    expect(gets(calls)).toHaveLength(59);
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(gets(calls)).toHaveLength(60);
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
  });

  it("moves to the paid result once the owner confirms it", async () => {
    let order: OrderView = makeSubmittedUpiOrder();
    await setup(() => order);
    order = makeSubmittedUpiOrder({
      status: "paid",
      paid_at: NOW,
      period_start: NOW,
      period_end: "2026-11-19T18:30:00Z",
      valid_through: "2026-11-19",
      receipt: { id: 41, number: "RA/26-27/000041" },
      method: "upi",
    });
    await advance(30_000);
    expect(title()).toHaveTextContent("Payment received");
    const slip = document.querySelector(".plans-receipt") as HTMLElement;
    expect(slip).toHaveTextContent("Paid withUPI (direct)");
    expect(screen.getByText("Your receipt is in Settings → Plan & billing.")).toBeInTheDocument();
    expect(screen.queryByText(/Razorpay also emails/)).not.toBeInTheDocument();
  });

  it("promises an email with a verified email and mail set up, and offers the chat with access", async () => {
    await setup(makeSubmittedUpiOrder({ kind: "now" }), {
      me: makeMe({ email: "maya@example.com", plan: makePaidPlanAccess() }),
      routes: {
        "GET /billing/status": makeBillingStatus({ payments: makePaymentsInfo({ email_reminders: true, upi_direct: true }) }),
      },
    });
    expect(await screen.findByText(/We'll email you when it's confirmed/)).toHaveTextContent(
      "Please don't pay again. We'll email you when it's confirmed.",
    );
    const slip = document.querySelector(".plans-receipt") as HTMLElement;
    expect(slip).toHaveTextContent("PlanStandard, 30 daysStarts when confirmed, in place of your current period");
    const card = screen.getByRole("region", { name: "We're checking your payment" });
    expect(within(card).getByRole("link", { name: "Back to chat" })).toHaveAttribute("href", "/");
    expect(within(card).getByRole("link", { name: "Back to plans" })).toHaveAttribute("href", "/plans");
    expect(screen.getByText(/Wrong UTR\?/)).toHaveTextContent(
      "Wrong UTR? Write to billing@example.com with the right one.",
    );
  });

  it("says where the result shows without an email, and goes back to the plans without access", async () => {
    await setup(makeSubmittedUpiOrder(), { routes: { "GET /billing/status": errorResponse(500, "Down") } });
    expect(screen.getByText(/You can close this page/)).toHaveTextContent(
      "Please don't pay again. You can close this page: you'll see it here and in Settings → Plan & billing.",
    );
    expect(screen.getByRole("link", { name: "Done" })).toHaveAttribute("href", "/plans");
    expect(screen.queryByRole("link", { name: "Back to chat" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Wrong UTR/)).not.toBeInTheDocument();
  });
});

describe("A rejected direct-UPI payment", () => {
  const rejected = makeSubmittedUpiOrder({
    status: "rejected",
    upi: makeUpiInfo({
      uri: null,
      utr: "630512345678",
      submitted_at: "2026-10-20T09:34:00Z",
      decided_at: "2026-10-20T11:02:00Z",
      rejection: {
        reason: "amount_differs",
        message:
          "The payment with UTR 6305 1234 5678 wasn't for ₹2,000. We'll send what you paid back to you by UPI. Start a new payment for the exact amount.",
        note: "We received ₹200.",
      },
    }),
  });

  it("gives the reason, the owner's note and a way to start again", async () => {
    await setup(rejected);
    expect(title()).toHaveTextContent("We couldn't confirm your payment");
    expect(screen.getByText(/wasn't for ₹2,000/)).toBeInTheDocument();
    expect(await screen.findByText(/Note from Asha Rao Docs/)).toHaveTextContent(
      "Note from Asha Rao Docs: We received ₹200.",
    );
    const slip = document.querySelector(".plans-receipt") as HTMLElement;
    expect(slip).toHaveTextContent("Amount₹2,000ReferenceAN-7K4M-Q9TXUTR6305 1234 5678");
    expect(within(slip).queryByText("Plan")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a new payment" })).toHaveAttribute(
      "href",
      "/plans/checkout/standard?kind=period",
    );
    expect(screen.getByText(/Questions\?/)).toHaveTextContent("Questions? Write to billing@example.com.");
  });

  it("names us when the site details can't load", async () => {
    await setup(rejected, { routes: { "GET /public/site": errorResponse(500, "Down") } });
    expect(await screen.findByText(/Note from us/)).toHaveTextContent("Note from us: We received ₹200.");
  });
});
