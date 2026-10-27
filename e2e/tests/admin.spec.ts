import type { BrowserContext, Page } from "@playwright/test";
import { INVALID_LOGIN, composer, loginHeading, openAvatarMenu, openChat, submitLogin } from "../support/app";
import { ADMIN_SESSION_ENV, BASE_URL, SESSION_COOKIE } from "../support/env";
import { expect, guardAdminContext, randomPassword, randomUsername, signIn, test as base } from "../support/fixtures";

// The admin's pages show every account, so this file records no traces or screenshots, and closes the admin's page
// before its context (Playwright would otherwise keep an accessibility snapshot of it for a failed test). The admin is
// signed in with the session global-setup.ts made through the API; the UI only ever acts on e2e-… users, and the
// admin guard blocks any other write.
async function useAdminSession(context: BrowserContext): Promise<void> {
  await context.addCookies([
    { name: SESSION_COOKIE, value: process.env[ADMIN_SESSION_ENV] ?? "", url: BASE_URL, httpOnly: true, sameSite: "Strict" },
  ]);
}

const test = base.extend<{ adminPage: Page }>({
  adminPage: async ({ newContext, admin, violations }, use) => {
    const context = await newContext();
    await guardAdminContext(context, admin, violations);
    await useAdminSession(context);
    const page = await context.newPage();
    await use(page);
    await page.close();
  },
});

test.use({ trace: "off", screenshot: "off" });

test.afterEach(async ({}, testInfo) => {
  // A failed expect carries an accessibility snapshot of the page it checked, which Playwright writes to
  // error-context.md: on the admin page that would be every account. The error message itself stays.
  for (const error of testInfo.errors) delete (error as { errorContext?: string }).errorContext;
});

async function openUsers(page: Page): Promise<void> {
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Administration", level: 1 })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Users" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "New user" })).toBeEnabled();
}

function userRow(page: Page, username: string) {
  return page.getByRole("row").filter({ has: page.getByRole("button", { name: `Edit ${username}`, exact: true }) });
}

function flash(page: Page, text: string) {
  return page.getByRole("status").filter({ hasText: text });
}

test("the admin's avatar menu has Admin, which opens the admin page", async ({ adminPage }) => {
  await adminPage.goto("/");
  await expect(composer(adminPage)).toBeEnabled();
  const menu = await openAvatarMenu(adminPage);
  await expect(menu).toHaveAccessibleName(/, Administrator$/);
  await menu.getByRole("menuitem", { name: "Admin" }).click();
  await expect(adminPage).toHaveURL(/\/admin$/);
  await expect(adminPage.getByRole("heading", { name: "Administration", level: 1 })).toBeVisible();
  await expect(adminPage.getByRole("main")).toHaveCount(1);
  await expect(adminPage.getByRole("tablist", { name: "Admin sections" }).getByRole("tab")).toHaveText([
    "Users",
    "Plans",
    "Billing",
    "Models",
    "Usage",
    "Documents",
    "Plugins",
    "System",
  ]);
});

test("the other admin tabs load (read only)", async ({ adminPage }) => {
  await openUsers(adminPage);
  const sections: [string, RegExp][] = [
    ["Plans", /^Plans$/],
    ["Billing", /./],
    ["Models", /^Chat models$/],
    ["Usage", /^Usage in /],
    ["Documents", /^Documents$/],
    ["Plugins", /^Gmail/],
    ["System", /^System status$/],
  ];
  for (const [tab, heading] of sections) {
    await adminPage.getByRole("tab", { name: tab }).click();
    await expect(adminPage).toHaveURL(new RegExp(`#${tab.toLowerCase()}(/[a-z]+)?$`));
    await expect(adminPage.getByRole("tabpanel", { name: tab }).getByRole("heading", { level: 2, name: heading }).first()).toBeVisible();
  }
});

test("creating a user through the form, after fixing its validation errors", async ({ adminPage, admin, deleteAfter }) => {
  await openUsers(adminPage);
  await adminPage.getByRole("button", { name: "New user" }).click();
  const dialog = adminPage.getByRole("dialog", { name: "New user" });
  const username = dialog.getByLabel("Username");
  const password = dialog.getByLabel("Password", { exact: true });
  const create = dialog.getByRole("button", { name: "Create user" });

  await username.fill("ab");
  await password.fill("short");
  await create.click();
  await expect(username).toHaveAccessibleDescription(/^Use at least 3 characters\./);
  await expect(username).toHaveAttribute("aria-invalid", "true");
  await expect(username).toBeFocused();
  await expect(password).toHaveAccessibleDescription(/^Use at least 8 characters\./);

  await username.fill("e2e bad name");
  await create.click();
  await expect(username).toHaveAccessibleDescription(/^Use only letters, digits, dots, dashes and underscores\./);

  const name = randomUsername();
  deleteAfter(name);
  await username.fill(name);
  await password.fill(randomPassword());
  await dialog.getByLabel("Requests per minute").fill("5");
  await create.click();
  await expect(dialog).toBeHidden();
  await expect(flash(adminPage, `Created ${name}.`)).toBeVisible();
  await expect(userRow(adminPage, name)).toContainText("5 / min");
  const created = (await admin.listUsers()).find((user) => user.username === name);
  expect(created).toMatchObject({ is_active: true, is_admin: false, overrides: { rate_limit_per_min: 5 } });

  // The same name again: the server's 409 lands on the field.
  await adminPage.getByRole("button", { name: "New user" }).click();
  await username.fill(name);
  await password.fill(randomPassword());
  await create.click();
  await expect(username).toHaveAccessibleDescription(/^This username is already taken/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("editing a user's limits", async ({ adminPage, admin, makeUser }) => {
  const user = await makeUser();
  await openUsers(adminPage);
  await userRow(adminPage, user.username).getByRole("button", { name: `Edit ${user.username}` }).click();
  const dialog = adminPage.getByRole("dialog", { name: `Edit ${user.username}` });
  await expect(dialog.getByLabel("Username")).toHaveCount(0);

  // By role: once a limit is typed, its "Reset … to the plan's value" button carries the same words.
  const allowance = dialog.getByRole("textbox", { name: "Chat allowance (standard tokens)", exact: true });
  await allowance.fill("lots");
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(allowance).toHaveAccessibleDescription(/^Enter a whole number\./);
  await allowance.fill("50000");
  await dialog.getByRole("textbox", { name: "Requests per minute", exact: true }).fill("7");
  await dialog.getByRole("textbox", { name: "Max file size (MB)", exact: true }).fill("3");
  await dialog.getByRole("button", { name: "Save changes" }).click();

  await expect(dialog).toBeHidden();
  await expect(flash(adminPage, `Saved changes to ${user.username}.`)).toBeVisible();
  const row = userRow(adminPage, user.username);
  await expect(row).toContainText("7 / min");
  await expect(row).toContainText("3 MB per file");
  expect((await admin.getUser(user.id))?.overrides).toMatchObject({
    monthly_token_limit: 50_000,
    rate_limit_per_min: 7,
    max_upload_mb: 3,
  });
});

test("deactivating a user ends their session and blocks sign-in", async ({ adminPage, page, context, makeUser }) => {
  const user = await makeUser();
  await signIn(context, user);
  await openChat(page);

  await openUsers(adminPage);
  await userRow(adminPage, user.username).getByRole("button", { name: `Edit ${user.username}` }).click();
  const dialog = adminPage.getByRole("dialog", { name: `Edit ${user.username}` });
  const active = dialog.getByRole("switch", { name: "Active" });
  await expect(active).toBeChecked();
  await active.uncheck();
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(dialog).toBeHidden();
  await expect(userRow(adminPage, user.username)).toContainText("inactive");

  await page.reload();
  await expect(loginHeading(page)).toBeVisible();
  await submitLogin(page, user.username, user.password);
  await expect(page.getByRole("alert")).toHaveText(INVALID_LOGIN);
});

test("deleting a user asks first, then removes them", async ({ adminPage, admin, makeUser }) => {
  const user = await makeUser();
  await openUsers(adminPage);
  const row = userRow(adminPage, user.username);
  await row.getByRole("button", { name: `Delete ${user.username}` }).click();
  const confirm = adminPage.getByRole("group", { name: `Delete ${user.username} and all of their documents?` });
  await expect(confirm.getByRole("button", { name: "Cancel" })).toBeFocused();
  await adminPage.keyboard.press("Escape");
  await expect(confirm).toBeHidden();
  await expect(row.getByRole("button", { name: `Delete ${user.username}` })).toBeFocused();

  await row.getByRole("button", { name: `Delete ${user.username}` }).click();
  await confirm.getByRole("button", { name: "Delete user" }).click();
  await expect(flash(adminPage, `Deleted ${user.username} and their documents.`)).toBeVisible();
  await expect(adminPage.getByRole("cell", { name: user.username })).toHaveCount(0);
  expect(await admin.getUser(user.id)).toBeUndefined();
});

test("the admin guard blocks a write outside the test users, such as a model's Enable switch", async ({ newContext, admin }) => {
  const context = await newContext();
  // A safety net behind the guard: a write the guard let through would end here, not at the backend.
  const leaked: string[] = [];
  await context.route("**/api/admin/models/**", (route) => {
    if (route.request().method() === "GET") return route.fallback();
    leaked.push(`${route.request().method()} ${route.request().url()}`);
    return route.fulfill({ status: 418, json: { detail: "Stopped by the test's safety net" } });
  });
  const blocked: string[] = [];
  await guardAdminContext(context, admin, blocked);
  await useAdminSession(context);
  const page = await context.newPage();
  const before = await admin.listModels();

  await page.goto("/admin#models");
  const toggle = page.getByRole("tabpanel", { name: "Models" }).getByRole("switch", { name: /^Enable /, disabled: false }).first();
  await toggle.click();
  await expect.poll(() => blocked).toEqual([expect.stringMatching(/^admin write outside the test users: PATCH .*\/api\/admin\/models\/\d+$/)]);
  expect(leaked).toEqual([]);
  expect(await admin.listModels()).toEqual(before);
  await page.close();
});
