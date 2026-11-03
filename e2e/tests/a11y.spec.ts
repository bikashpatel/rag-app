import type { Locator } from "@playwright/test";
import { avatarButton, documentsPanel, identifierField, loginHeading, openChat, settingsDialog } from "../support/app";
import { fakeAuthConfig, fakeDocument, mockApi, serveJson, stubTurnstile } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";

// An accessibility smoke test: landmarks, names of menus and dialogs, and the keyboard (the admin page is covered in
// admin.spec.ts, the phone layouts in phone.spec.ts).

test("the sign-in page has one main landmark and one h1, with focus in the identifier field", async ({ page }) => {
  await page.goto("/login");
  await expect(loginHeading(page)).toBeVisible();
  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expect(identifierField(page)).toBeFocused();
  await expect(page.getByRole("button", { name: "Show password" })).toHaveAttribute("aria-pressed", "false");
});

test("the sign-up, reset and emailed-link pages: one main landmark, one h1, and errors said in words", async ({
  page,
}) => {
  await serveJson(page, "/auth/config", fakeAuthConfig());
  await stubTurnstile(page);
  await mockApi(page, "POST", "/auth/password/check", {
    status: 400,
    body: { detail: "This link has expired or was already used.", code: "link_invalid" },
  });
  for (const [path, heading] of [
    ["/signup", "Create your account"],
    ["/forgot-password", "Reset your password"],
    ["/verify-email", "This link is incomplete"],
    ["/reset-password#t=q3Zx8LmP0aB2cD4eF6gHq3Zx8LmP0aB2cD4eF6gH123", "This link has expired"],
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  }

  // A submit with nothing filled in: each field says what it needs, linked to it, and focus goes to the first.
  await page.goto("/signup");
  await page.getByRole("button", { name: "Create account" }).click();
  const name = page.getByRole("textbox", { name: "Your name" });
  await expect(name).toBeFocused();
  await expect(name).toHaveAttribute("aria-invalid", "true");
  await expect(name).toHaveAccessibleDescription("Enter your name.");
  const email = page.getByRole("textbox", { name: "Email" });
  await expect(email).toHaveAttribute("aria-invalid", "true");
  await expect(email).toHaveAccessibleDescription("Enter your email.");
  await expect(page.getByRole("checkbox", { name: /I agree to the Terms/ })).toHaveAccessibleDescription(
    "Tick the box to accept the terms.",
  );
});

test.describe("signed in", () => {
  test.beforeEach(async ({ context, user, page }) => {
    await signIn(context, user);
    await openChat(page);
  });

  test("the chat page has one main landmark beside the conversations and the documents", async ({ page }) => {
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("complementary", { name: "Conversations" })).toBeVisible();
    await expect(documentsPanel(page)).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Conversation history" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("New chat");
  });

  test("every menu has a name, and Escape closes it with focus back on its button", async ({ page, user }) => {
    const menus: [Locator, string | RegExp][] = [
      [page.getByRole("button", { name: /^Model: / }), "Model"],
      [page.getByRole("button", { name: /^Plugins/ }), "Plugins"],
      [avatarButton(page), `Account: ${user.username}, Member`],
      [page.getByRole("button", { name: "Add files and more" }), "Add files and more"],
    ];
    for (const [trigger, name] of menus) {
      await expect(trigger).toHaveAttribute("aria-haspopup", "menu");
      await trigger.click();
      await expect(trigger).toHaveAttribute("aria-expanded", "true");
      const menu = page.getByRole("menu");
      await expect(menu).toHaveAccessibleName(name);
      await expect(menu.locator(":focus")).toHaveCount(1);
      await page.keyboard.press("Escape");
      await expect(menu).toHaveCount(0);
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await expect(trigger).toBeFocused();
    }
  });

  test("Settings by keyboard alone: a named dialog that traps focus and closes on Escape", async ({ page }) => {
    await avatarButton(page).focus();
    await page.keyboard.press("ArrowDown");
    const menu = page.getByRole("menu", { name: /^Account: / });
    await expect(menu.getByRole("menuitem", { name: "View usage" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "Plan & billing" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "Settings", exact: true })).toBeFocused();
    await page.keyboard.press("Enter");

    const dialog = settingsDialog(page);
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("aria-modal", "true");
    await expect(page.locator("#root")).toHaveJSProperty("inert", true); // the page behind can't be reached
    const general = dialog.getByRole("tab", { name: "General" });
    await expect(general).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(dialog.getByRole("tab", { name: "Account" })).toBeFocused();
    await expect(dialog.getByRole("tab", { name: "Account" })).toHaveAttribute("aria-selected", "true");
    await expect(dialog.getByRole("tabpanel", { name: "Account" })).toBeVisible();

    for (let i = 0; i < 12; i++) {
      await page.keyboard.press("Tab");
      await expect(dialog.locator(":focus")).toHaveCount(1);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(avatarButton(page)).toBeFocused();
    await expect(page.locator("#root")).toHaveJSProperty("inert", false);
    await expect(page).toHaveURL(/\/$/);
  });

  test("Escape cancels a document's delete confirmation and puts focus back on Delete", async ({ page }) => {
    await serveJson(page, "/documents", [fakeDocument({ filename: "alpha.pdf" })]);
    await page.reload();
    const panel = documentsPanel(page);
    const remove = panel.getByRole("button", { name: "Delete alpha.pdf" });
    await remove.click();
    await expect(remove).toHaveAttribute("aria-expanded", "true");
    const confirm = panel.getByRole("group", { name: "Confirm deleting alpha.pdf" });
    await expect(confirm.getByRole("button", { name: "Cancel" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(confirm).toHaveCount(0);
    await expect(remove).toBeFocused();
  });
});
