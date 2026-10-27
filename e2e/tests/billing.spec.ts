import type { Page } from "@playwright/test";
import type { CheckoutStarted, ConversationSummary } from "../../frontend/src/api/types";
import { composer, openChat, settingsDialog, submitLogin } from "../support/app";
import { fakeCatalog, fakeConfig, fakeId, fakeModel, fakeOrder, serveJson } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";
import { CHAT_STREAM } from "../support/sse";

// Plans, checkout and the return from Razorpay (docs/briefs/plans-billing.md 12.3). Razorpay is never called: the
// catalog, the checkout and the order are intercepted, the "Razorpay" page is a page on the app's own origin (accepted
// as a payment page only in test mode), and the outside-host guard stays on. Access itself is real: users start
// without a plan, and the admin API grants or ends one mid-test.

test.use({ userOptions: { plan: null } });

const ORDER_ID = "e2eOrder0000000000001";

function heading(page: Page, name: string | RegExp) {
  return page.getByRole("heading", { name, level: 1 });
}

/** The payment side: catalog, checkout, a fake Razorpay page back to the app, and the order (pending until paid). */
async function mockPayments(page: Page): Promise<{ markPaid: () => void; checkouts: unknown[] }> {
  let paid = false;
  const checkouts: unknown[] = [];
  await serveJson(page, "/billing/plans", fakeCatalog());
  await serveJson(page, "/billing/profile", { name: null, email: null });
  await page.route("**/api/billing/checkout", async (route) => {
    checkouts.push(route.request().postDataJSON());
    const pending = fakeOrder(ORDER_ID, "pending");
    const started: CheckoutStarted = { order: pending, pay_url: pending.pay_url ?? "" };
    await route.fulfill({ status: 201, json: started });
  });
  await page.route("**/__fake-razorpay/*", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><title>Razorpay</title><h1>Razorpay</h1><a href="/billing/return?order=${ORDER_ID}">Pay</a>`,
    }),
  );
  await page.route(`**/api/billing/orders/${ORDER_ID}*`, (route) =>
    route.fulfill({ json: fakeOrder(ORDER_ID, paid ? "paid" : "pending") }),
  );
  return { markPaid: () => (paid = true), checkouts };
}

test("a new user signs in to the plans, pays on Razorpay and lands in the chat", async ({ page, admin, user }) => {
  const payments = await mockPayments(page);
  await page.goto("/login");
  await submitLogin(page, user.username, user.password);

  await expect(page).toHaveURL(/\/plans$/);
  await expect(heading(page, "Choose a plan")).toBeVisible();
  await expect(page.getByText(/Test mode:/)).toBeVisible();
  await page.getByRole("listitem", { name: "Standard" }).getByRole("link", { name: "Choose Standard" }).click();

  await expect(page).toHaveURL(/\/plans\/checkout\/standard\?kind=period$/);
  await expect(heading(page, "Review and pay")).toBeVisible();
  await expect(page.getByText(/By paying you agree to the Terms of service/)).toBeVisible();
  await page.getByRole("complementary", { name: "Summary" }).getByRole("button", { name: "Pay ₹2,000" }).click();

  await expect(page).toHaveURL(new RegExp(`/__fake-razorpay/${ORDER_ID}$`));
  expect(payments.checkouts).toEqual([expect.objectContaining({ kind: "period", accept_terms: true })]);
  // The payment is applied: the server now gives the user a plan, and the order reads paid.
  await admin.grantPlan(user.id, "standard", 1);
  payments.markPaid();
  await page.getByRole("link", { name: "Pay" }).click();

  await expect(page).toHaveURL(new RegExp(`/billing/return\\?order=${ORDER_ID}$`));
  await expect(heading(page, "Payment received")).toBeVisible();
  await expect(heading(page, "Payment received")).toBeFocused();
  await page.getByRole("button", { name: "Start chatting" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(composer(page)).toBeEnabled();
});

test("a user without a plan goes to the plans from the chat routes, with Settings and their data reachable", async ({
  page,
  context,
  user,
}) => {
  await serveJson(page, "/billing/plans", fakeCatalog());
  const chat: ConversationSummary = {
    id: fakeId(),
    title: "Kept from an earlier plan",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  await serveJson(page, "/conversations", [chat]);
  await signIn(context, user);

  await page.goto("/");
  await expect(page).toHaveURL(/\/plans$/);
  await page.goto(`/c/${chat.id}?settings=plugins`);
  await expect(page).toHaveURL(/\/plans\?settings=plugins$/);

  const dialog = settingsDialog(page);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("group", { name: "Your documents" })).toBeVisible();
  await expect(dialog.getByRole("group", { name: "Your chats" }).getByText("Kept from an earlier plan")).toBeVisible();

  await dialog.getByRole("tab", { name: "Plan & billing" }).click();
  await expect(dialog.getByRole("region", { name: "No active plan" })).toBeVisible();
});

test("a plan that ends in an open chat swaps the composer for a panel and keeps the message", async ({
  page,
  context,
  admin,
  makeUser,
}) => {
  // A dated period, revoked mid-chat: the plan then reads as ended (an open-ended plan that ends leaves no plan at all).
  const user = await makeUser({ plan: null });
  await admin.grantPlan(user.id, "professional", 1);
  await serveJson(page, "/billing/plans", fakeCatalog());
  await signIn(context, user);
  await openChat(page);
  await admin.revokeCurrentPeriod(user.id);
  // The server would refuse the turn the same way; the answer never reaches a model.
  await page.route(CHAT_STREAM, (route) =>
    route.fulfill({ status: 402, json: { detail: "Your Professional plan has ended.", code: "plan_expired" } }),
  );

  await composer(page).fill("Summarise the renewal terms");
  await composer(page).press("Enter");
  const panel = page.getByRole("region", { name: /^Your Professional plan ended/ });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading")).toBeFocused();
  await expect(panel).toContainText("Your unsent message is saved and comes back when you renew.");
  await expect(composer(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/$/);

  // Renewed (a grant here): the next visit to the chat puts the message back.
  await admin.grantPlan(user.id, "professional", 1);
  await page.unroute(CHAT_STREAM);
  await openChat(page);
  await expect(composer(page)).toHaveValue("Summarise the renewal terms");
});

test("features outside the plan stay in the + menu, locked, and lead to the plans", async ({
  page,
  context,
  makeUser,
}) => {
  const user = await makeUser({ plan: "starter" });
  await serveJson(page, "/billing/plans", fakeCatalog());
  await serveJson(page, "/models", [
    fakeModel({
      display_name: "Maker Model",
      is_default: true,
      can_create_files: true,
      can_create_images: true,
      supports_tools: true,
    }),
  ]);
  await serveJson(page, "/documents/config", fakeConfig());
  await signIn(context, user);
  await openChat(page);

  await page.getByRole("button", { name: "Add files and more" }).click();
  const menu = page.getByRole("menu", { name: "Add files and more" });
  const locked = menu.getByRole("menuitem", { name: "Create a document" });
  await expect(locked).toHaveAccessibleDescription(/^Included in Standard/);
  await expect(menu.getByRole("menuitem", { name: "PDF document" })).toHaveCount(0);
  await locked.click();
  await expect(page).toHaveURL(/\/plans$/);
});

test("the public pages open without a session", async ({ newContext }) => {
  const context = await newContext();
  const page = await context.newPage();
  for (const path of ["/pricing", "/terms", "/refunds"]) {
    await page.goto(path);
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
});
