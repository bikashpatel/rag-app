import type { ChatModel } from "../../frontend/src/api/types";
import {
  avatarButton,
  documentsPanel,
  openAvatarMenu,
  openChat,
  preferences,
  send,
  servedModels,
  settingsDialog,
  userMessages,
} from "../support/app";
import { fakeId } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";
import { cannedChat, events } from "../support/sse";

/** A model item's name: the model's name, with " (default)" for the server default. */
function exactName(name: string): RegExp {
  return new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}( \\(default\\))?$`);
}

test("the model menu lists the user's models and switches this chat's model", async ({ page, context, admin, makeUser }) => {
  const enabled = (await admin.listModels()).filter((model) => model.kind === "chat" && model.enabled);
  test.skip(enabled.length < 2, "needs two enabled chat models");
  const allowed = enabled.slice(0, 2);
  const user = await makeUser({ restrict_models: true, allowed_model_ids: allowed.map((model) => model.id) });
  await signIn(context, user);
  const chat = await cannedChat(page, (request) => [
    events.meta({ conversationId: fakeId(), title: request.message }),
    events.sources([]),
    events.delta("Noted."),
    events.done(),
  ]);
  await openChat(page);

  const { models: served, initial } = await servedModels(page);
  expect(served.map((model) => model.id).sort()).toEqual(allowed.map((model) => model.id).sort());
  const first = initial as ChatModel;
  const other = served.find((model) => model.id !== first.id) as ChatModel;

  const button = page.getByRole("button", { name: `Model: ${first.display_name}` });
  await button.click();
  const menu = page.getByRole("menu", { name: "Model" });
  await expect(menu.getByRole("menuitemradio")).toHaveCount(2);
  await expect(menu.getByRole("menuitemradio", { name: exactName(first.display_name) })).toHaveAttribute("aria-checked", "true");
  await expect(menu.getByRole("menuitemradio", { name: exactName(other.display_name) })).toHaveAttribute("aria-checked", "false");
  await expect(menu.getByRole("menuitem", { name: "Set the default in Settings…" })).toBeVisible();

  await menu.getByRole("menuitemradio", { name: exactName(other.display_name) }).click();
  await expect(menu).toBeHidden();
  const switched = page.getByRole("button", { name: `Model: ${other.display_name}` });
  await expect(switched).toBeFocused();
  await switched.click();
  await expect(menu.getByRole("menuitemradio", { name: exactName(other.display_name) })).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(switched).toBeFocused();

  // The next request uses the picked model.
  await send(page, "Which model is this?");
  await expect(userMessages(page)).toHaveText("Which model is this?");
  await expect.poll(() => chat.requests.map((request) => request.model_id)).toEqual([other.id]);

  // A pick applies to this chat only: the account default stays unset.
  expect((await preferences(page)).default_model_id).toBeNull();
});

test.describe("member", () => {
  test.beforeEach(async ({ context, user, page }) => {
    await signIn(context, user);
    await openChat(page);
  });

  test("the Plugins menu shows Gmail with a status and links to the plugin settings", async ({ page }) => {
    const trigger = page.getByRole("button", { name: /^Plugins/ });
    await trigger.click();
    const menu = page.getByRole("menu", { name: "Plugins" });
    const gmail = menu.getByRole("menuitem", { name: "Gmail" });
    await expect(gmail).toBeVisible();
    // Gmail may be unconfigured (CI) or set up but not connected (a fresh user): never "Connected".
    await expect(gmail).toHaveAccessibleDescription(/Not set up yet|Not connected|Turned off by the admin/);
    await expect(gmail).not.toHaveAccessibleDescription(/Connected ·/);
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(trigger).toBeFocused();

    await trigger.click();
    await menu.getByRole("menuitem", { name: "Manage plugins…" }).click();
    const dialog = settingsDialog(page);
    await expect(dialog.getByRole("tab", { name: "Plugins & data" })).toHaveAttribute("aria-selected", "true");
    await expect(dialog.getByRole("heading", { name: "Gmail" })).toBeVisible();
    await expect(page).toHaveURL(/\?settings=plugins$/);
  });

  test("the Documents toggle hides and shows the panel, and the choice survives a reload", async ({ page }) => {
    const toggle = page.getByRole("button", { name: "Documents", exact: true });
    const panel = documentsPanel(page);
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(panel).toBeVisible();

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(panel).toBeHidden();
    // With the column hidden, the composer's scope chip opens it again.
    const chip = page.getByRole("button", { name: "Document scope: All documents. Show documents" });
    await expect(chip).toBeVisible();

    await page.reload();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(panel).toBeHidden();
    await chip.click();
    await expect(panel).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  test("the avatar menu has usage, Plan & billing, Settings, a theme switch and Log out, and no Admin", async ({ page, user }) => {
    const menu = await openAvatarMenu(page);
    await expect(menu).toHaveAccessibleName(`Account: ${user.username}, Member`);
    await expect(menu.getByText("Complimentary Professional")).toBeVisible();
    for (const item of ["View usage", "Plan & billing", "Settings", "Log out"]) {
      await expect(menu.getByRole("menuitem", { name: item, exact: true })).toBeVisible();
    }
    await expect(menu.getByRole("menuitem", { name: "Admin" })).toHaveCount(0);
    await expect(menu.getByRole("menuitem", { name: "View usage" })).toHaveAccessibleDescription(/^Chat allowance this period: 0/);

    const theme = menu.getByRole("group", { name: "Theme" });
    await expect(theme.getByRole("menuitemradio")).toHaveText(["System", "Light", "Dark"]);
    await expect(theme.getByRole("menuitemradio", { name: "System" })).toHaveAttribute("aria-checked", "true");
    await theme.getByRole("menuitemradio", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(menu).toBeVisible();
    await expect(theme.getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    await expect
      .poll(async () => (await preferences(page)).theme)
      .toBe("dark");

    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(avatarButton(page)).toBeFocused();

    await (await openAvatarMenu(page)).getByRole("menuitem", { name: "View usage" }).click();
    await expect(settingsDialog(page).getByRole("tab", { name: "Usage" })).toHaveAttribute("aria-selected", "true");
  });
});
