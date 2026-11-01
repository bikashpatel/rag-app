import type { Page } from "@playwright/test";
import type { OrderView, PlanCatalog } from "../../frontend/src/api/types";
import { FAKE_QR_SVG, fakeCatalog, fakeUpiOrder, mockApi, serveJson } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";

// Direct UPI (docs/briefs/upi-direct.md 12): the checkout's "Pay with UPI", the order page with its QR code and UTR
// field, and "We're checking your payment". The payment side is intercepted (the catalog with direct UPI on, the UPI
// checkout, the order, its UTR and its QR code); no money moves and the backend never sees these orders.

test.use({ userOptions: { plan: null } });

const ORDER_ID = "e2eUpiOrder000000001";
const ORDER_PATH = `/billing/orders/${ORDER_ID}`;

function upiCatalog(): PlanCatalog {
  const catalog = fakeCatalog();
  return { ...catalog, payments: { ...catalog.payments, upi_direct: true } };
}

type Mocked = { checkouts: unknown[]; utrs: unknown[] };

/** The catalog, the UPI checkout, the order (pending until its UTR arrives) and its QR codes. */
async function mockUpi(page: Page, start: OrderView = fakeUpiOrder(ORDER_ID, "pending")): Promise<Mocked> {
  let order = start;
  await serveJson(page, "/billing/plans", upiCatalog());
  await serveJson(page, "/billing/profile", { name: null });
  // `*` takes the return page's first `?refresh=true` too (never a further path: "/utr", "/upi-qr.svg").
  await serveJson(page, `${ORDER_PATH}*`, () => order);
  await page.route(`**/api${ORDER_PATH}/upi-qr.*`, (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: FAKE_QR_SVG }),
  );
  const checkouts = await mockApi(page, "POST", "/billing/upi/checkout", () => ({ status: 201, body: order }));
  const utrs = await mockApi(page, "POST", `${ORDER_PATH}/utr`, () => {
    order = fakeUpiOrder(ORDER_ID, "submitted");
    return { body: order };
  });
  return { checkouts, utrs };
}

function heading(page: Page, name: string | RegExp) {
  return page.getByRole("heading", { name, level: 1 });
}

async function expectOneMainAndHeading(page: Page): Promise<void> {
  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
}

test("pays by UPI from the checkout: the QR code, the UTR, then the owner's check", async ({ page, context, user }) => {
  const mocked = await mockUpi(page);
  await signIn(context, user);
  await page.goto("/plans/checkout/standard?kind=period");
  const summary = page.getByRole("complementary", { name: "Summary" });
  await expect(summary.getByRole("button", { name: "Pay online · ₹2,000" })).toBeVisible();
  await expect(summary.getByText("Paying online needs a confirmed email.")).toBeVisible();
  await summary.getByRole("button", { name: "Pay ₹2,000 with UPI" }).click();

  await expect(page).toHaveURL(new RegExp(`/billing/return\\?order=${ORDER_ID}$`));
  expect(mocked.checkouts).toEqual([expect.objectContaining({ kind: "period", accept_terms: true })]);
  await expect(heading(page, "Pay ₹2,000 by UPI")).toBeFocused();
  await expectOneMainAndHeading(page);
  await expect(page.getByRole("img", { name: "QR code: pay ₹2,000 to Asha Rao by UPI" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Save QR image" })).toHaveAttribute("download", "upi-AN-7K4M-Q9TX.png");
  for (const what of ["UPI ID", "amount", "note"]) {
    await expect(page.getByRole("button", { name: `Copy ${what}` })).toBeVisible();
  }

  const utr = page.getByRole("textbox", { name: "UTR (12-digit UPI reference number)" });
  await utr.fill("6305 12");
  await page.getByRole("button", { name: "I've paid · Send UTR" }).click({ force: true });
  await expect(page.getByRole("alert")).toHaveText("Enter all 12 digits.");
  await expect(page.getByRole("alert")).toBeFocused();
  await utr.fill("UPI Ref No: 6305 1234 5678 9");
  await expect(
    page.getByText("That's 13 digits. A UTR has exactly 12: look for UPI Ref No. or UTR in the payment's details."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "I've paid · Send UTR" })).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByRole("main")).not.toHaveAttribute("aria-live");
  await utr.fill("6305 1234 5678");
  await expect(page.getByText("6305 1234 5678 (all 12 digits)")).toBeVisible();
  await page.getByRole("button", { name: "I've paid · Send UTR" }).click();

  await expect(heading(page, "We're checking your payment")).toBeFocused();
  expect(mocked.utrs).toEqual([{ utr: "630512345678" }]);
  await expect(page.getByText("Please don't pay again.", { exact: false })).toBeVisible();
  await expect(page.getByRole("main")).toHaveAttribute("aria-live", "polite");
  await expectOneMainAndHeading(page);
});

test("the UPI panel works in the dark theme too", async ({ page, context, user }) => {
  await mockUpi(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await signIn(context, user);
  await page.goto(`/billing/return?order=${ORDER_ID}`);
  await expect(heading(page, "Pay ₹2,000 by UPI")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expectOneMainAndHeading(page);
  await expect(page.getByRole("img", { name: /^QR code: pay/ })).toBeVisible();
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test("opens the UPI app with the payment request, and shows the QR code on request", async ({
    page,
    context,
    user,
  }) => {
    await mockUpi(page);
    await signIn(context, user);
    await page.goto(`/billing/return?order=${ORDER_ID}`);
    const open = page.getByRole("link", { name: "Open UPI app" });
    await expect(open).toHaveAttribute("href", /^upi:\/\/pay\?pa=asha\.rao@okhdfcbank&pn=Asha%20Rao&am=2000\.00&cu=INR&tn=/);
    await expect(page.getByRole("img", { name: /^QR code: pay/ })).toHaveCount(0);
    const toggle = page.getByRole("button", { name: "Show QR code" });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(page.getByRole("button", { name: "Hide QR code" })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("img", { name: /^QR code: pay/ })).toBeVisible();
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(390);
  });
});

test("a rejected payment says why and starts again from the same plan", async ({ page, context, user }) => {
  await mockUpi(page, fakeUpiOrder(ORDER_ID, "rejected"));
  await signIn(context, user);
  await page.goto(`/billing/return?order=${ORDER_ID}`);
  await expect(heading(page, "We couldn't confirm your payment")).toBeFocused();
  await expect(page.getByText("We couldn't find a payment of ₹2,000 with UTR 6305 1234 5678.")).toBeVisible();
  await page.getByRole("link", { name: "Start a new payment" }).click();
  await expect(page).toHaveURL(/\/plans\/checkout\/standard\?kind=period$/);
});
