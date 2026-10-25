import { assistantMessages, composer, openAvatarMenu, openChat, openSettings, send, sidewaysScrollers } from "../support/app";
import { fakeId } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";
import { cannedChat, events } from "../support/sse";

// The "phone" project (Pixel 7, 412 px): the sidebar is a drawer, the documents panel a slide-over, menus are bottom
// sheets and Settings is a full-screen sheet.

test.beforeEach(async ({ context, user, page }) => {
  await signIn(context, user);
  await openChat(page);
});

test("the conversations drawer opens and closes", async ({ page }) => {
  const toggle = page.getByRole("button", { name: "Conversations", exact: true });
  const drawer = page.getByRole("dialog", { name: "Conversations" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("button", { name: "New chat" })).toBeHidden();

  await toggle.click();
  await expect(drawer).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(drawer.getByRole("button", { name: "New chat" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();

  await toggle.click();
  await drawer.getByRole("button", { name: "Close conversations" }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New chat" })).toBeHidden();
});

test("the documents slide-over opens from the top bar and the scope chip, and closes", async ({ page }) => {
  const toggle = page.getByRole("button", { name: "Documents", exact: true });
  const sheet = page.getByRole("dialog", { name: "Documents" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");

  await toggle.click();
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("heading", { name: "Documents" })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Drop files here or browse" })).toBeVisible();
  await sheet.getByRole("button", { name: "Close documents panel" }).click();
  await expect(sheet).toHaveCount(0);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");

  await page.getByRole("button", { name: "Document scope: All documents. Show documents" }).click();
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
});

test("nothing scrolls sideways: the chat, a long answer, a menu sheet and Settings", async ({ page }) => {
  expect(await sidewaysScrollers(page)).toEqual([]);

  const longLine = "const result = computeTheQuarterlyRevenueForEveryRegionAndProduct(region, product, quarter, options);";
  await cannedChat(page, [
    events.meta({ conversationId: fakeId(), title: "Show me the code" }),
    events.sources([]),
    events.delta("Here it is, see https://example.com/a/very/long/path/that/keeps/going/and/going/without/any/break/at/all\n\n"),
    events.delta(`\`\`\`ts\n${longLine}\n\`\`\`\n\n| Region | Q1 | Q2 | Q3 | Q4 | Total |\n|---|---|---|---|---|---|\n| North | 1,200 | 1,350 | 1,410 | 1,500 | 5,460 |\n`),
    events.done(),
  ]);
  await send(page, "Show me the code");
  await expect(assistantMessages(page).getByRole("button", { name: "Copy ts code" })).toBeVisible();
  expect(await sidewaysScrollers(page)).toEqual([]);

  const width = page.viewportSize()?.width ?? 0;
  const menu = await openAvatarMenu(page);
  // A bottom sheet: the full width of the screen (polled: it slides in).
  await expect.poll(async () => Math.round((await menu.boundingBox())?.width ?? 0)).toBe(width);
  expect(await sidewaysScrollers(page)).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();

  const dialog = await openSettings(page, "Usage");
  await expect.poll(async () => Math.round((await dialog.boundingBox())?.width ?? 0)).toBe(width);
  expect(await sidewaysScrollers(page)).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(composer(page)).toBeVisible();
});
