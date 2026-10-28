import { readFile } from "node:fs/promises";
import type { AccountInfo, MyUsage } from "../../frontend/src/api/types";
import {
  INVALID_LOGIN,
  composer,
  conversationList,
  loginHeading,
  openAvatarMenu,
  openChat,
  openSettings,
  preferences,
  send,
  settingsDialog,
  submitLogin,
} from "../support/app";
import { expect, signIn, test } from "../support/fixtures";
import { fakeId } from "../support/fakes";
import { cannedChat, events } from "../support/sse";

test.describe("General", () => {
  test("theme, accent and font size apply at once, survive a reload and follow the account", async ({
    page,
    context,
    user,
    newContext,
  }) => {
    await signIn(context, user);
    await openChat(page);
    const html = page.locator("html");
    await expect(html).toHaveAttribute("data-theme", "light");
    const dialog = await openSettings(page, "General");

    await dialog.getByRole("radiogroup", { name: "Theme" }).getByRole("radio", { name: "Dark" }).check();
    await expect(html).toHaveAttribute("data-theme", "dark");
    await expect(html).toHaveAttribute("data-theme-preference", "dark");
    await dialog.getByRole("radiogroup", { name: "Accent colour" }).getByRole("radio", { name: "Ocean" }).check();
    await expect(html).toHaveAttribute("data-accent", "ocean");
    await dialog.getByRole("radiogroup", { name: "Chat font size" }).getByRole("radio", { name: "Large" }).check();
    await expect(html).toHaveAttribute("data-chat-font", "large");
    await expect.poll(() => preferences(page)).toMatchObject({ theme: "dark", accent: "ocean", chat_font_size: "large" });

    await page.reload();
    await expect(html).toHaveAttribute("data-theme", "dark");
    await expect(html).toHaveAttribute("data-accent", "ocean");
    await expect(html).toHaveAttribute("data-chat-font", "large");
    await expect(settingsDialog(page).getByRole("radio", { name: "Dark" })).toBeChecked();

    // Saved in the account: another browser (no local cache) gets them after signing in.
    const other = await newContext();
    await signIn(other, user);
    const otherPage = await other.newPage();
    await otherPage.goto("/");
    const otherHtml = otherPage.locator("html");
    await expect(otherHtml).toHaveAttribute("data-theme", "dark");
    await expect(otherHtml).toHaveAttribute("data-accent", "ocean");
    await expect(otherHtml).toHaveAttribute("data-chat-font", "large");
  });
});

test.describe("Account", () => {
  test("the sign-in history lists this session as This device", async ({ page, context, user, newContext }) => {
    await signIn(await newContext(), user);
    await signIn(context, user);
    await openChat(page);
    const dialog = await openSettings(page, "Account");
    await expect(dialog.getByRole("heading", { name: "Recent sign-ins" })).toBeVisible();
    const account = (await (await page.request.get("/api/me/account")).json()) as AccountInfo;
    expect(account.recent_logins).toHaveLength(2);
    expect(account.recent_logins.filter((login) => login.current)).toHaveLength(1);

    const signIns = dialog.getByRole("listitem").filter({ has: page.locator("time") });
    await expect(signIns).toHaveCount(2);
    await expect(signIns.filter({ hasText: "This device" })).toHaveCount(1);
    await expect(signIns.first()).toContainText("This device");
    await expect(dialog.getByRole("definition").first()).toHaveText(user.username);
    await expect(dialog.getByRole("button", { name: "Change password…" })).toBeVisible();
  });

  test("Change password: the new one works, the old one fails, other sessions end", async ({
    page,
    context,
    user,
    newContext,
  }) => {
    const other = await newContext();
    await signIn(other, user);
    await signIn(context, user);
    await openChat(page);
    const dialog = await openSettings(page, "Account");
    await dialog.getByRole("button", { name: "Change password…" }).click();
    const form = dialog.getByRole("form", { name: "Change password" });
    const submit = form.getByRole("button", { name: "Change password" });
    const newPassword = `${user.password}-new`;

    await form.getByLabel("Current password", { exact: true }).fill("not-the-password");
    await form.getByLabel("New password", { exact: true }).fill(newPassword);
    await form.getByLabel("Confirm new password", { exact: true }).fill(`${newPassword}x`);
    await expect(form.getByText("The passwords don't match.")).toBeVisible();
    await expect(submit).toBeDisabled();
    await form.getByLabel("Confirm new password", { exact: true }).fill(newPassword);
    await submit.click();
    await expect(form.getByRole("alert")).toHaveText("Current password is incorrect");
    await expect(form.getByLabel("Current password", { exact: true })).toBeFocused();

    await form.getByLabel("Current password", { exact: true }).fill(user.password);
    await submit.click();
    await expect(dialog.getByRole("status").filter({ hasText: "Your password was changed. Your other sessions were signed out." })).toBeVisible();
    expect((await other.request.get("/api/auth/me")).status()).toBe(401);
    expect((await page.request.get("/api/auth/me")).status()).toBe(200);

    await page.keyboard.press("Escape");
    await (await openAvatarMenu(page)).getByRole("menuitem", { name: "Log out" }).click();
    await expect(loginHeading(page)).toBeVisible();
    await submitLogin(page, user.username, user.password);
    await expect(page.getByRole("alert")).toHaveText(INVALID_LOGIN);
    await page.getByLabel("Password", { exact: true }).fill(newPassword);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(composer(page)).toBeVisible();
  });

  test("Log out of all other sessions signs out the other browser and keeps this one", async ({
    page,
    context,
    user,
    newContext,
  }) => {
    const other = await newContext();
    await signIn(other, user);
    const otherPage = await other.newPage();
    await openChat(otherPage);
    await signIn(context, user);
    await openChat(page);

    const dialog = await openSettings(page, "Account");
    const trigger = dialog.getByRole("button", { name: "Log out of all other sessions" });
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await dialog.getByRole("group", { name: "Confirm logging out of other sessions" }).getByRole("button", { name: "Log out others" }).click();
    await expect(dialog.getByRole("status").filter({ hasText: "Other sessions were signed out." })).toBeVisible();

    await otherPage.reload();
    await expect(loginHeading(otherPage)).toBeVisible();
    await expect(otherPage).toHaveURL(/\/login$/);
    await page.reload();
    await expect(settingsDialog(page)).toBeVisible();
    await expect(page.getByRole("tab", { name: "Account" })).toHaveAttribute("aria-selected", "true");
  });
});

test.describe("Usage", () => {
  test.use({
    userOptions: { overrides: { monthly_token_limit: 123_456, rate_limit_per_min: 7, monthly_image_limit: 5, max_documents: 9 } },
  });

  test("shows this period's usage against the user's limits", async ({ page, context, user }) => {
    await signIn(context, user);
    await openChat(page);
    const dialog = await openSettings(page, "Usage");
    await expect(dialog.getByRole("heading", { name: "Usage", level: 2 })).toBeVisible();
    await expect(dialog.getByRole("meter", { name: "Chat allowance" })).toHaveAttribute(
      "aria-valuetext",
      "0 of 123,456 standard tokens",
    );
    await expect(dialog.getByRole("meter", { name: "Images" })).toHaveAttribute("aria-valuetext", "0 of 5 images");
    const fact = (term: string) => dialog.getByRole("term").filter({ hasText: term }).locator("xpath=following-sibling::dd[1]");
    await expect(fact("Requests per minute")).toHaveText("7 per minute");
    await expect(fact("Chat answers")).toHaveText("0");
    await expect(fact("Documents")).toHaveText("0 of 9");
    const perDay = dialog.getByRole("figure", { name: "Per day" });
    await expect(perDay).toContainText("No usage yet this period.");
    await expect(perDay).toContainText("Up to 7 requests a minute");
    await expect(dialog.getByText("Nothing used yet.")).toBeVisible();
  });

  test("a period with usage shows the meters, the daily chart and its table", async ({ page, context, user }) => {
    // The numbers are intercepted: real ones would need model calls.
    await page.route("**/api/me/usage", async (route) => {
      const response = await route.fetch();
      const usage = (await response.json()) as MyUsage;
      const today = usage.daily[usage.daily.length - 1];
      Object.assign(today, { chat_allowance: 61_728, chat_tokens: 61_728, requests: 3, cost_usd: 0.42 });
      await route.fulfill({
        response,
        json: {
          ...usage,
          chat_allowance: { ...usage.chat_allowance, used: 61_728 },
          chat_tokens: 61_728,
          requests: 3,
          cost_usd: { ...usage.cost_usd, total: 0.42, chat: 0.42 },
          by_model: [{ model_name: "Canned Model", kind: "chat", tokens: 61_728, allowance_tokens: 61_728, requests: 3, cost_usd: 0.42 }],
        } satisfies MyUsage,
      });
    });
    await signIn(context, user);
    await openChat(page);
    const dialog = await openSettings(page, "Usage");
    await expect(dialog.getByRole("meter", { name: "Chat allowance" })).toHaveAttribute(
      "aria-valuetext",
      "61,728 of 123,456 standard tokens",
    );
    await expect(dialog.getByText("$0.42").first()).toBeVisible();
    const perDay = dialog.getByRole("figure", { name: "Per day" });
    await expect(perDay.getByRole("group", { name: /chat tokens/i })).toBeVisible();
    await perDay.getByRole("button", { name: "Show as table" }).click();
    const table = perDay.getByRole("table", { name: /^Usage per day/ });
    await expect(table.getByRole("row").filter({ hasText: "61,728" })).toHaveCount(1);
    await expect(dialog.getByRole("table", { name: /^Usage by model/ }).getByRole("row", { name: /Canned Model/ })).toBeVisible();
  });
});

test.describe("Plugins & data", () => {
  test("Export my data downloads a ZIP of the account", async ({ page, context, user }) => {
    await signIn(context, user);
    await openChat(page);
    const dialog = await openSettings(page, "Plugins & data");
    const download = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "Export", exact: true }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(new RegExp(`^asknuvexusdoc-export-${user.username}-\\d{4}-\\d{2}-\\d{2}\\.zip$`));
    const bytes = await readFile(await file.path());
    expect(bytes.subarray(0, 4)).toEqual(Buffer.from("PK\x03\x04", "latin1"));
    for (const name of ["README.txt", "account.json", "documents.json", "conversations/index.json"]) {
      expect(bytes.includes(name), `${name} in the ZIP`).toBe(true);
    }
    await expect(dialog.getByRole("status").filter({ hasText: `Saved ${file.suggestedFilename()}` })).toBeVisible();
  });

  test("Delete all chats asks for DELETE, then empties the sidebar", async ({ page, context, user }) => {
    // A canned conversation (client side only) and the server's count and DELETE answer to match it: nothing real is
    // deleted, and no model is called to make a real conversation.
    await signIn(context, user);
    const title = "Plan the team offsite";
    await cannedChat(page, [events.meta({ conversationId: fakeId(), title }), events.sources([]), events.delta("Sure."), events.done()]);
    await page.route("**/api/me/account", async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...((await response.json()) as AccountInfo), conversation_count: 1 } });
    });
    let deletes = 0;
    await page.route("**/api/conversations", (route) => {
      if (route.request().method() !== "DELETE") return route.fallback();
      deletes += 1;
      return route.fulfill({ json: { deleted: 1 } });
    });
    await openChat(page);
    await send(page, title);
    await expect(conversationList(page).getByRole("link", { name: title })).toBeVisible();

    const dialog = await openSettings(page, "Plugins & data");
    await dialog.getByRole("button", { name: "Delete all chats…" }).click();
    const form = dialog.getByRole("form", { name: "Confirm deleting all chats" });
    const confirm = form.getByRole("button", { name: "Delete 1 chat" });
    const word = form.getByLabel("Type DELETE to confirm");
    await expect(word).toBeFocused();
    await expect(confirm).toBeDisabled();
    await word.fill("DELET");
    await expect(confirm).toBeDisabled();
    await word.fill("DELETE");
    await confirm.click();
    await expect(dialog.getByRole("status").filter({ hasText: "Deleted 1 conversation." })).toBeVisible();
    expect(deletes).toBe(1);

    await page.keyboard.press("Escape");
    await expect(settingsDialog(page)).toBeHidden();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText("No conversations yet. Your chats will appear here.")).toBeVisible();
  });
});
