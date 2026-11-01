import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BillingAdminConfig } from "../../api/types";
import { markdownResponse } from "../../test/adminTabs";
import { makeBillingAdminConfig } from "../../test/billing";
import { NOW } from "../../test/fixtures";
import { errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import BillingTab from "./BillingTab";

const live = makeBillingAdminConfig();
const setupDefaults = live.payments;

/** Every policy page is final except those named. */
function pages(drafts: string[] = [], missing: string[] = []): Record<string, Route> {
  return Object.fromEntries(
    ["terms", "privacy", "refunds", "shipping", "contact", "about", "pricing"].map((slug) => [
      `GET /legal/${slug}.md`,
      () =>
        missing.includes(slug)
          ? new Response("Not found", { status: 404 })
          : markdownResponse(`${drafts.includes(slug) ? "<!-- draft -->\n" : ""}# ${slug}\n\nText.`),
    ]),
  );
}

function setup(config: BillingAdminConfig | Route = live, routes: Record<string, Route> = {}) {
  const calls = stubFetch({ "GET /admin/billing/config": config, ...pages(), ...routes });
  const onNavigate = vi.fn();
  render(<BillingTab section="business" params={new URLSearchParams()} onNavigate={onNavigate} />);
  return { calls, onNavigate };
}

const field = (name: string) => screen.getByRole("textbox", { name });
const businessSave = () =>
  within(screen.getByRole("region", { name: "Business details" })).getByRole("button", { name: "Save" });
const checklist = () => screen.getByRole("region", { name: "Going-live checklist" });
const row = (text: string) => within(checklist()).getByText(text).closest("li") as HTMLElement;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Billing → Business & payments", () => {
  it("validates and saves the business details", async () => {
    const saved = makeBillingAdminConfig({
      settings: {
        ...live.settings,
        business_name: "Rao Docs",
        contact_phone: "+91 98765 43210",
        receipt_prefix: "RD",
        upi_limit_paise: 0,
      },
    });
    const { calls } = setup(live, { "PUT /admin/billing/settings": saved });
    const user = userEvent.setup();

    expect(await screen.findByRole("textbox", { name: "Business name" })).toHaveValue("AskNuvexusDoc");
    expect(field("Receipt prefix")).toHaveAccessibleDescription(
      expect.stringContaining("Receipts read RA/26-27/000001."),
    );
    await user.clear(field("Business name"));
    await user.clear(field("Contact email"));
    await user.type(field("Contact email"), "billing@");
    await user.clear(field("Receipt prefix"));
    await user.type(field("Receipt prefix"), "test");
    await user.clear(field("UPI limit per payment"));
    await user.type(field("UPI limit per payment"), "lots");
    await user.click(businessSave());
    expect(field("Business name")).toHaveAccessibleDescription(expect.stringContaining("This field is required."));
    expect(field("Business name")).toHaveFocus();
    expect(field("Contact email")).toHaveAccessibleDescription(expect.stringContaining("Enter an email address."));
    expect(field("Receipt prefix")).toHaveValue("TEST");
    expect(field("Receipt prefix")).toHaveAccessibleDescription(
      expect.stringContaining("TEST is kept for test-mode receipts."),
    );
    expect(field("UPI limit per payment")).toHaveAccessibleDescription(
      expect.stringContaining("Enter a whole number."),
    );

    await user.type(field("Business name"), "Rao Docs");
    await user.clear(field("Contact email"));
    await user.type(field("Contact phone"), "+91 98765 43210");
    await user.clear(field("Receipt prefix"));
    await user.type(field("Receipt prefix"), "rd");
    expect(field("Receipt prefix")).toHaveAccessibleDescription(
      expect.stringContaining("Receipts read RD/26-27/000001."),
    );
    await user.clear(field("UPI limit per payment"));
    await user.type(field("UPI limit per payment"), "0");
    await user.type(field("Address"), "12 MG Road{Enter}Bengaluru");
    await user.click(businessSave());

    expect(await screen.findByText("Saved the business details.")).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      {
        business_name: "Rao Docs",
        address: "12 MG Road\nBengaluru",
        contact_email: null,
        contact_phone: "+91 98765 43210",
        receipt_prefix: "RD",
        upi_limit_paise: 0,
        upi_enabled: false,
        upi_id: null,
        upi_payee_name: null,
      },
    ]);
  });

  it("shows a refused save in the form", async () => {
    setup(live, { "PUT /admin/billing/settings": () => errorResponse(422, "receipt_prefix: invalid") });
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Business details" });
    await user.click(businessSave());
    expect(await screen.findByRole("alert")).toHaveTextContent("receipt_prefix: invalid");
  });

  it("shows the live setup, copies the webhook URL and checks pending payments now", async () => {
    const { calls } = setup(
      makeBillingAdminConfig({
        payments: { ...setupDefaults, previous_webhook_secret_set: true, paused_orders: 2, email_reminders: true },
      }),
      { "POST /admin/billing/reconcile": { checked: 3 } },
    );
    const user = userEvent.setup();
    const card = await screen.findByRole("region", { name: "Razorpay" });
    expect(within(card).getByText("Live")).toBeInTheDocument();
    expect(within(card).getByText("rzp_live_…AbCd")).toBeInTheDocument();
    expect(within(card).getByText("The previous secret is also accepted.")).toBeInTheDocument();
    expect(within(card).getByText("payment.dispute.created")).toBeInTheDocument();
    expect(within(card).getByText("2 test orders can't be checked with live keys.")).toBeInTheDocument();
    expect(within(card).getByText("On")).toBeInTheDocument();
    expect(within(card).queryByRole("meter")).not.toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Copy the webhook URL" }));
    expect(await navigator.clipboard.readText()).toBe("https://rag.example.com/api/billing/razorpay/webhook");
    expect(await within(card).findByText("Copied")).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Check pending payments now" }));
    expect(await screen.findByText("Checked 3 orders.")).toBeInTheDocument();
    await waitFor(() =>
      expect(calls.filter((call: FetchCall) => call.path === "/admin/billing/config")).toHaveLength(2),
    );
  });

  it("shows test keys, rejected keys, testers and a failed check", async () => {
    const { onNavigate } = setup(
      makeBillingAdminConfig({
        payments: {
          ...setupDefaults,
          mode: "test",
          key_id_hint: "rzp_test_…AbCd",
          test_links_created: 26,
          testers: 0,
          webhook_secret_set: false,
          last_webhook_at: null,
          reconciler_last_run_at: null,
          last_error: "Razorpay rejected the keys (401).",
        },
      }),
      { "POST /admin/billing/reconcile": () => errorResponse(502, "Razorpay didn't answer.") },
    );
    const user = userEvent.setup();
    const card = await screen.findByRole("region", { name: "Razorpay" });
    expect(within(card).getByRole("meter", { name: "Test links used" })).toHaveAttribute(
      "aria-valuetext",
      "26 of about 30",
    );
    expect(within(card).getByRole("meter", { name: "Test links used" })).toHaveClass("ui-meter-warning");
    expect(within(card).getByText("Not set")).toBeInTheDocument();
    expect(within(card).getAllByText("Never")).toHaveLength(1);
    expect(within(card).getByText("Not run yet")).toBeInTheDocument();
    expect(
      within(card).getByText(
        "Off: set MAIL_SMTP_HOST and MAIL_FROM (the MAIL_SMTP_* settings) in .env to send receipts and reminders.",
      ),
    ).toBeInTheDocument();
    expect(within(card).getByRole("alert")).toHaveTextContent("Razorpay rejected the keys (401).");
    expect(within(card).getByText(/Nobody can make test payments\./)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Mark testers in Users" }));
    expect(onNavigate).toHaveBeenCalledWith("users");
    await user.click(within(card).getByRole("button", { name: "Check pending payments now" }));
    expect(await screen.findByText("Razorpay didn't answer.")).toBeInTheDocument();
  });

  it("explains how to set up keys when payments aren't configured", async () => {
    setup(makeBillingAdminConfig({ payments: { ...setupDefaults, configured: false, mode: null, key_id_hint: null } }));
    const card = await screen.findByRole("region", { name: "Razorpay" });
    expect(within(card).getByText("Not set up")).toBeInTheDocument();
    expect(within(card).getByText(/Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in \.env/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Check pending payments now" })).toBeDisabled();
  });

  it("ticks the checklist from the setup and the pages, and lists what is left", async () => {
    let round = 0;
    const routes = pages(["terms", "privacy"], ["shipping"]);
    const { calls } = setup(
      makeBillingAdminConfig({ settings: { ...live.settings, address: "", contact_phone: null } }),
      {
        ...routes,
        "GET /legal/terms.md": () =>
          ++round > 1 ? markdownResponse("# Terms\n\nFinal.") : markdownResponse("<!-- draft -->\n# Terms"),
        "GET /legal/about.md": () => errorResponse(500, "Oops"),
      },
    );
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Going-live checklist" });

    expect(row("Business details filled")).toHaveTextContent(
      "To do: Business details filledMissing: address, contact phone.",
    );
    await user.click(within(row("Business details filled")).getByRole("button", { name: "Fill them in" }));
    expect(field("Address")).toHaveFocus();

    const pagesRow = row("No draft policy pages");
    await within(pagesRow).findByRole("link", { name: "Terms of service" });
    expect(pagesRow).toHaveTextContent(
      "Terms of service (draft), Privacy policy (draft), Shipping and delivery (missing), About us (couldn't be read)",
    );
    expect(within(pagesRow).getByRole("link", { name: "Privacy policy" })).toHaveAttribute("href", "/privacy");
    expect(row("Live keys in .env")).toHaveTextContent("Done: Live keys in .env");
    expect(row("Webhook secret set and a webhook received")).toHaveTextContent("Done:");
    expect(row("Confirm the UPI limit with Razorpay and set it above")).toHaveTextContent("Now: ₹2,000 per payment.");
    expect(row("Reminder emails set up")).toHaveTextContent("Optional, not done:");

    await user.click(within(pagesRow).getByRole("button", { name: "check again" }));
    await waitFor(() => expect(calls.filter((call) => call.path === "/legal/terms.md")).toHaveLength(2));
    await waitFor(() =>
      expect(
        within(row("No draft policy pages")).queryByRole("link", { name: "Terms of service" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("ticks every item when everything is in place", async () => {
    setup(
      makeBillingAdminConfig({
        settings: { ...live.settings, address: "12 MG Road", contact_phone: "+91 98765 43210", upi_limit_paise: 0 },
        payments: { ...setupDefaults, email_reminders: true },
      }),
    );
    await screen.findByRole("region", { name: "Going-live checklist" });
    await waitFor(() => expect(row("No draft policy pages")).toHaveTextContent("Done: No draft policy pages"));
    expect(row("Business details filled")).toHaveTextContent("Done:");
    expect(row("Confirm the UPI limit with Razorpay and set it above")).toHaveTextContent("Now: no limit.");
    expect(row("Reminder emails set up")).toHaveTextContent("Done:");
  });

  describe("UPI payments", () => {
    const card = () => screen.getByRole("region", { name: "UPI payments" });
    const upiSave = () => within(card()).getByRole("button", { name: "Save" });
    const withUpi = (overrides: Partial<BillingAdminConfig["settings"]> = {}) =>
      makeBillingAdminConfig({
        settings: { ...live.settings, upi_enabled: true, upi_id: "asha.rao@okhdfcbank", upi_payee_name: "Asha Rao", ...overrides },
      });

    it("checks the UPI ID and payee name as the server does, and won't turn on without both", async () => {
      const { calls } = setup();
      const user = userEvent.setup();
      await screen.findByRole("region", { name: "UPI payments" });
      expect(card()).toHaveTextContent(
        "Customers pay your UPI ID directly. You check each payment in your bank app, then confirm it here.",
      );
      expect(within(card()).queryByRole("img")).not.toBeInTheDocument();
      expect(card()).toHaveTextContent("Email alerts: off. Set the contact email in Business details and the mail settings.");

      const upiId = within(card()).getByRole("textbox", { name: "UPI ID" });
      await user.type(upiId, "Asha");
      expect(upiId).toHaveValue("asha");
      expect(upiId).not.toHaveAttribute("aria-invalid");
      await user.type(upiId, "@1bank");
      expect(upiId).toHaveAttribute("aria-invalid", "true");
      expect(upiId).toHaveAccessibleDescription(expect.stringContaining("Enter a UPI ID like yourname@okhdfcbank"));
      await user.clear(upiId);

      await user.click(within(card()).getByRole("switch", { name: "Accept UPI payments" }));
      await user.click(upiSave());
      const toggle = within(card()).getByRole("switch", { name: "Accept UPI payments" });
      expect(toggle).toHaveFocus();
      expect(toggle).toHaveAccessibleDescription("Add your UPI ID and the payee name to turn on UPI payments.");

      await user.type(within(card()).getByRole("textbox", { name: "Payee name" }), "Asha <Rao>");
      await user.type(upiId, "asha.rao@okhdfcbank");
      await user.click(upiSave());
      expect(within(card()).getByRole("textbox", { name: "Payee name" })).toHaveAccessibleDescription(
        expect.stringContaining("Use letters, digits, spaces and . , & ' ( ) / - only."),
      );
      expect(calls.some((call) => call.method === "PUT")).toBe(false);
    });

    it("saves the UPI settings with the saved business details, then offers the ₹1 test", async () => {
      const saved = withUpi();
      const { calls } = setup(live, { "PUT /admin/billing/settings": saved });
      const user = userEvent.setup();
      await screen.findByRole("region", { name: "UPI payments" });
      await user.type(field("Business name"), " (unsaved)");
      await user.click(within(card()).getByRole("switch", { name: "Accept UPI payments" }));
      await user.type(within(card()).getByRole("textbox", { name: "UPI ID" }), "  Asha.Rao@OKHDFCBANK ");
      await user.type(within(card()).getByRole("textbox", { name: "Payee name" }), "Asha   Rao ");
      await user.click(upiSave());

      expect(await screen.findByText("Saved the UPI payments settings.")).toBeInTheDocument();
      expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
        { ...live.settings, upi_enabled: true, upi_id: "asha.rao@okhdfcbank", upi_payee_name: "Asha Rao" },
      ]);
      const qr = within(card()).getByRole("img", { name: "QR code: pay ₹1 to Asha Rao by UPI" });
      expect(qr).toHaveAttribute("src", "/api/admin/billing/upi-preview.svg?v=asha.rao%40okhdfcbank+Asha+Rao");
      expect(card()).toHaveTextContent("Test it before customers use it");
      expect(row("Test direct UPI with ₹1 from Google Pay, PhonePe and Paytm")).toBeInTheDocument();
    });

    it("says where email alerts go, and shows a refused save", async () => {
      setup(
        makeBillingAdminConfig({
          ...withUpi({ contact_email: "owner@example.com" }),
          payments: { ...setupDefaults, email_reminders: true },
        }),
        { "PUT /admin/billing/settings": () => errorResponse(422, "upi_id: not a UPI ID") },
      );
      const user = userEvent.setup();
      await screen.findByRole("region", { name: "UPI payments" });
      expect(card()).toHaveTextContent("Email alerts: on, to owner@example.com.");
      expect(card()).toHaveTextContent("Requests already made keep the UPI ID they were made with for up to 24 hours.");
      await user.click(upiSave());
      expect(await within(card()).findByRole("alert")).toHaveTextContent("upi_id: not a UPI ID");
    });
  });
});
