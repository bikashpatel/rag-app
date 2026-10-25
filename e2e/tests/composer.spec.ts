import type { Page } from "@playwright/test";
import { composer, openChat, servedModels, userMessages } from "../support/app";
import { fakeConfig, fakeId, fakeModel, serveJson } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";
import { cannedChat, events } from "../support/sse";

// The model list and the document quota are intercepted, so what the "+" menu offers doesn't depend on the stack's
// models, image model or the user's documents.
const maker = fakeModel({ display_name: "Maker Model", is_default: true, can_create_files: true, can_create_images: true, supports_tools: true });
const plain = fakeModel({ display_name: "Plain Model", provider: "openai" });

test.beforeEach(async ({ context, user }) => {
  await signIn(context, user);
});

function plusButton(page: Page) {
  return page.getByRole("button", { name: "Add files and more" });
}

async function openPlusMenu(page: Page) {
  await plusButton(page).click();
  const menu = page.getByRole("menu", { name: "Add files and more" });
  await expect(menu).toBeVisible();
  return menu;
}

async function pickModel(page: Page, from: string, to: string) {
  await page.getByRole("button", { name: `Model: ${from}` }).click();
  await page.getByRole("menu", { name: "Model" }).getByRole("menuitemradio", { name: new RegExp(`^${to}`) }).click();
  await expect(page.getByRole("button", { name: `Model: ${to}` })).toBeVisible();
}

test("starters go in front of the draft and nothing is sent", async ({ page }) => {
  await serveJson(page, "/models", [maker, plain]);
  await serveJson(page, "/documents/config", fakeConfig());
  const chatRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/chat/stream")) chatRequests.push(request.url());
  });
  await openChat(page);
  await composer(page).fill("the quarterly numbers");

  let menu = await openPlusMenu(page);
  await expect(menu.getByRole("group", { name: "Create a document" }).getByRole("menuitem")).toHaveText([
    "PDF",
    "Word",
    "Excel",
    "Markdown",
  ]);
  await menu.getByRole("menuitem", { name: "PDF document" }).click();
  await expect(menu).toBeHidden();
  await expect(composer(page)).toHaveValue("Create a PDF document: the quarterly numbers");
  await expect(composer(page)).toBeFocused();

  // Another starter replaces the first one and keeps the text after it.
  menu = await openPlusMenu(page);
  await menu.getByRole("menuitem", { name: "Create an image" }).click();
  await expect(composer(page)).toHaveValue("Create an image: the quarterly numbers");
  menu = await openPlusMenu(page);
  await menu.getByRole("menuitem", { name: "Excel spreadsheet" }).click();
  await expect(composer(page)).toHaveValue("Create an Excel spreadsheet: the quarterly numbers");

  await expect(userMessages(page)).toHaveCount(0);
  expect(chatRequests).toEqual([]);
});

test("items that can't be used are disabled with the reason", async ({ page }) => {
  await serveJson(page, "/models", [maker, plain]);
  await serveJson(page, "/documents/config", fakeConfig({ max_documents: 3, document_count: 3 }));
  await openChat(page);
  await pickModel(page, "Maker Model", "Plain Model");

  const menu = await openPlusMenu(page);
  const attach = menu.getByRole("menuitem", { name: "Attach files…" });
  await expect(attach).toBeDisabled();
  await expect(attach).toHaveAccessibleDescription("You've reached your limit of 3 documents.");
  const image = menu.getByRole("menuitem", { name: "Create an image" });
  await expect(image).toBeDisabled();
  await expect(image).toHaveAccessibleDescription("Plain Model can't create images. Pick a model marked Images.");
  const pdf = menu.getByRole("menuitem", { name: "PDF document" });
  await expect(pdf).toBeDisabled();
  await expect(pdf).toHaveAccessibleDescription("Plain Model can't create files. Pick a model marked Files.");

  // A disabled item stays focusable but does nothing.
  await image.click({ force: true });
  await expect(menu).toBeVisible();
  await expect(composer(page)).toHaveValue("");
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(plusButton(page)).toBeFocused();

  // With a model that can, the full documents are the reason.
  await pickModel(page, "Plain Model", "Maker Model");
  const again = await openPlusMenu(page);
  await expect(again.getByRole("menuitem", { name: "Create an image" })).toHaveAccessibleDescription("Your documents are full.");
  await expect(again.getByRole("menuitem", { name: "Word document" })).toBeDisabled();
});

test("Enter sends, Shift+Enter adds a line, and the request carries the draft", async ({ page }) => {
  const chat = await cannedChat(page, (request) => [
    events.meta({ conversationId: fakeId(), title: request.message.slice(0, 60) }),
    events.sources([]),
    events.delta("Noted."),
    events.done(),
  ]);
  await openChat(page);
  await composer(page).pressSequentially("first line");
  await composer(page).press("Shift+Enter");
  await composer(page).pressSequentially("second line");
  await expect(composer(page)).toHaveValue("first line\nsecond line");
  expect(chat.requests).toHaveLength(0);

  await composer(page).press("Enter");
  await expect(userMessages(page)).toHaveText("first line\nsecond line");
  await expect(composer(page)).toHaveValue("");
  await expect(composer(page)).toHaveAttribute("placeholder", "Reply…");
  const { initial } = await servedModels(page);
  expect(chat.requests).toEqual([{ conversation_id: null, message: "first line\nsecond line", model_id: initial?.id, document_ids: null }]);
});

test("with Enter to send off, Enter adds a line and Ctrl+Enter sends", async ({ page }) => {
  expect((await page.request.patch("/api/me/preferences", { data: { enter_to_send: false } })).status()).toBe(200);
  const chat = await cannedChat(page, [events.meta({ conversationId: fakeId(), title: "one" }), events.sources([]), events.delta("Ok."), events.done()]);
  await openChat(page);
  await expect(page.getByText("Enter for a new line")).toBeVisible();
  await composer(page).pressSequentially("one");
  await composer(page).press("Enter");
  await composer(page).pressSequentially("two");
  await expect(composer(page)).toHaveValue("one\ntwo");
  expect(chat.requests).toHaveLength(0);
  await composer(page).press("Control+Enter");
  await expect(userMessages(page)).toHaveText("one two");
  expect(chat.requests.map((request) => request.message)).toEqual(["one\ntwo"]);
});
