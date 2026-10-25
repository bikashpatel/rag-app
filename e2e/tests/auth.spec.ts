import { INVALID_LOGIN, avatarButton, composer, loginHeading, openAvatarMenu, openChat, settingsDialog, submitLogin } from "../support/app";
import { expect, signIn, test } from "../support/fixtures";

test.describe("sign-in", () => {
  test("a wrong password shows the error and stays on the sign-in page", async ({ page, user }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(loginHeading(page)).toBeVisible();
    await submitLogin(page, user.username, `${user.password}-wrong`);
    await expect(page.getByRole("alert")).toHaveText(INVALID_LOGIN);
    await expect(page).toHaveURL(/\/login$/);
    // The fields are described by the error, for screen readers.
    await expect(page.getByLabel("Username")).toHaveAccessibleDescription(INVALID_LOGIN);
  });

  test("the right password lands on the chat", async ({ page, user }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeDisabled();
    await submitLogin(page, user.username, user.password);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: `How can I help, ${user.username}?` })).toBeVisible();
    await expect(composer(page)).toBeEnabled();
    await expect(avatarButton(page)).toHaveAccessibleName(`Account: ${user.username}`);
  });

  test("signing in returns to the deep link that asked for it", async ({ page, user }) => {
    await page.goto("/?settings=account");
    await expect(page).toHaveURL(/\/login$/);
    await submitLogin(page, user.username, user.password);
    await expect(page).toHaveURL(/\/\?settings=account$/);
    const dialog = settingsDialog(page);
    await expect(dialog.getByRole("tab", { name: "Account" })).toHaveAttribute("aria-selected", "true");
    await expect(dialog.getByRole("heading", { name: "Account", level: 2 })).toBeVisible();
  });

  test("/settings/<tab> links work through the sign-in too", async ({ page, user }) => {
    await page.goto("/settings/usage");
    await expect(page).toHaveURL(/\/login$/);
    await submitLogin(page, user.username, user.password);
    await expect(page).toHaveURL(/\/\?settings=usage$/);
    await expect(settingsDialog(page).getByRole("tab", { name: "Usage" })).toHaveAttribute("aria-selected", "true");
  });
});

test.describe("signed in", () => {
  test("Log out ends the session and the next visit asks to sign in", async ({ page, context, user }) => {
    await signIn(context, user);
    await openChat(page);
    const menu = await openAvatarMenu(page);
    await menu.getByRole("menuitem", { name: "Log out" }).click();
    await expect(loginHeading(page)).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
    expect((await page.request.get("/api/auth/me")).status()).toBe(401);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(loginHeading(page)).toBeVisible();
  });

  test("a member who opens /admin gets the chat, not the admin page", async ({ page, context, user }) => {
    await signIn(context, user);
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/$/);
    await expect(composer(page)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Administration" })).toHaveCount(0);
    expect((await page.request.get("/api/admin/users")).status()).toBe(403);
  });
});
