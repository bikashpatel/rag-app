import type { Locator } from "@playwright/test";
import { avatarButton, documentsPanel, loginHeading, openChat, settingsDialog } from "../support/app";
import { fakeDocument, serveJson } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";

// An accessibility smoke test: landmarks, names of menus and dialogs, and the keyboard (the admin page is covered in
// admin.spec.ts, the phone layouts in phone.spec.ts).

test("the sign-in page has one main landmark and one h1, with focus in the username field", async ({ page }) => {
  await page.goto("/login");
  await expect(loginHeading(page)).toBeVisible();
  await expect(page.getByRole("main")).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expect(page.getByLabel("Username")).toBeFocused();
  await expect(page.getByRole("button", { name: "Show password" })).toHaveAttribute("aria-pressed", "false");
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
