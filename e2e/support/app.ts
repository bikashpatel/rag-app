import type { Locator, Page } from "@playwright/test";
import type { ChatModel, Preferences } from "../../frontend/src/api/types";
import { expect } from "./fixtures";

// Locators and steps shared by the specs, by role, label and text (the accessible names the app gives its controls).

export const INVALID_LOGIN = "Invalid username or password";

/** Opens the chat at `path` and waits until the composer, the sidebar list and the model list have loaded. */
export async function openChat(page: Page, path = "/"): Promise<void> {
  const loaded = Promise.all([
    page.waitForResponse((res) => new URL(res.url()).pathname === "/api/conversations" && res.request().method() === "GET"),
    page.waitForResponse((res) => new URL(res.url()).pathname === "/api/models"),
  ]);
  await page.goto(path);
  await loaded;
  await expect(composer(page)).toBeEnabled();
}

export function composer(page: Page): Locator {
  return page.getByRole("textbox", { name: "Message" });
}

export function sendButton(page: Page): Locator {
  return page.getByRole("button", { name: "Send message" });
}

export function stopButton(page: Page): Locator {
  return page.getByRole("button", { name: "Stop generating" });
}

export function assistantMessages(page: Page): Locator {
  return page.getByRole("article", { name: "Assistant" });
}

export function userMessages(page: Page): Locator {
  return page.getByRole("article", { name: "You" });
}

export async function send(page: Page, text: string): Promise<void> {
  await composer(page).fill(text);
  await sendButton(page).click();
}

export function conversationList(page: Page): Locator {
  return page.getByRole("navigation", { name: "Conversation history" });
}

/** The documents column (desktop). */
export function documentsPanel(page: Page): Locator {
  return page.getByRole("complementary", { name: "Documents" });
}

/** The signed-in user's saved preferences, read from the server. */
export async function preferences(page: Page): Promise<Preferences> {
  return (await (await page.request.get("/api/me/preferences")).json()) as Preferences;
}

/** The chat models served to the signed-in user, and the one a new chat starts with (the default, else the first). */
export async function servedModels(page: Page): Promise<{ models: ChatModel[]; initial: ChatModel | undefined }> {
  const models = (await (await page.request.get("/api/models")).json()) as ChatModel[];
  return { models, initial: models.find((model) => model.is_default) ?? models[0] };
}

export function avatarButton(page: Page): Locator {
  return page.getByRole("button", { name: /^Account: / });
}

/** Opens the avatar menu (by its trigger) and returns the menu. */
export async function openAvatarMenu(page: Page): Promise<Locator> {
  await avatarButton(page).click();
  const menu = page.getByRole("menu", { name: /^Account: / });
  await expect(menu).toBeVisible();
  return menu;
}

export function settingsDialog(page: Page): Locator {
  return page.getByRole("dialog", { name: "Settings" });
}

/** Avatar menu → Settings, then the tab ("General", "Account", "Usage", "Plugins & data"). */
export async function openSettings(page: Page, tab = "General"): Promise<Locator> {
  const menu = await openAvatarMenu(page);
  await menu.getByRole("menuitem", { name: "Settings", exact: true }).click();
  const dialog = settingsDialog(page);
  await expect(dialog).toBeVisible();
  const tabButton = dialog.getByRole("tab", { name: tab, exact: true });
  await tabButton.click();
  await expect(tabButton).toHaveAttribute("aria-selected", "true");
  return dialog;
}

/** The login form: fills both fields and submits. */
export async function submitLogin(page: Page, username: string, password: string): Promise<void> {
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export function loginHeading(page: Page): Locator {
  return page.getByRole("heading", { name: "Welcome back to RAG Assistant" });
}

/** Scroll containers meant to scroll sideways on their own: code blocks, tables and tab strips. */
const SIDEWAYS_BY_DESIGN = "pre, .chat-md-table, .table-wrap, .settings-models-table, .settings-tabs, .admin-tabs";

/**
 * Every visible scroll container that scrolls sideways, other than those meant to, as "tag.class (scroll > client)".
 * The page itself never scrolls (.chat-page clips), so the thread, the dialogs and the menus are checked one by one.
 */
export async function sidewaysScrollers(page: Page): Promise<string[]> {
  return page.evaluate((byDesign) => {
    const root = document.documentElement;
    return [root, ...document.body.querySelectorAll("*")]
      .filter((el) => {
        if (el.matches(byDesign) || el.getClientRects().length === 0) return false;
        const { overflowX } = getComputedStyle(el);
        return el === root || overflowX === "auto" || overflowX === "scroll";
      })
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => {
        const classes = (el.getAttribute("class") ?? "").trim().split(/\s+/).filter(Boolean);
        return `${[el.tagName.toLowerCase(), ...classes].join(".")} (${el.scrollWidth} > ${el.clientWidth})`;
      });
  }, SIDEWAYS_BY_DESIGN);
}
