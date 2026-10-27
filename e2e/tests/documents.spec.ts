import type { Page } from "@playwright/test";
import { documentsPanel, openChat, send } from "../support/app";
import { fakeDocument, fakeId, serveJson } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";
import { cannedChat, events } from "../support/sse";

// A successful upload would be embedded (a model call), so uploads here are refused: by the client's checks against
// the user's limits, or by an intercepted response. The guards fail the test if any upload reaches the backend.

test.use({ userOptions: { overrides: { max_upload_mb: 1 } } });

test.beforeEach(async ({ context, user, page }) => {
  await signIn(context, user);
  await openChat(page);
});

async function pickFiles(page: Page, ...files: { name: string; mimeType: string; buffer: Buffer }[]) {
  const chooser = page.waitForEvent("filechooser");
  await documentsPanel(page).getByRole("button", { name: "Drop files here or browse" }).click();
  await (await chooser).setFiles(files);
}

function uploadItem(page: Page, name: string) {
  return documentsPanel(page).getByRole("list", { name: "Uploads" }).getByRole("listitem").filter({ hasText: name });
}

test("unsupported and oversized files are refused with the reason and never sent", async ({ page }) => {
  const uploads: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/documents")) uploads.push(request.url());
  });
  const panel = documentsPanel(page);
  await expect(panel.getByRole("button", { name: "Drop files here or browse" })).toHaveAccessibleDescription(/up to 1 MB each$/);

  await pickFiles(
    page,
    { name: "setup.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ not really a program") },
    { name: "minutes.doc", mimeType: "application/msword", buffer: Buffer.from("old word file") },
    { name: "big-notes.txt", mimeType: "text/plain", buffer: Buffer.alloc(1.5 * 1024 * 1024, "a") },
  );
  await expect(uploadItem(page, "setup.exe").getByRole("alert")).toHaveText(".exe files aren't supported.");
  await expect(uploadItem(page, "minutes.doc").getByRole("alert")).toHaveText(
    "Legacy .doc files aren't supported. Save it as .docx and upload that instead.",
  );
  await expect(uploadItem(page, "big-notes.txt").getByRole("alert")).toHaveText("Too large (1.5 MB). The limit is 1 MB per file.");

  await uploadItem(page, "setup.exe").getByRole("button", { name: "Dismiss setup.exe" }).click();
  await expect(uploadItem(page, "setup.exe")).toHaveCount(0);
  await expect(uploadItem(page, "big-notes.txt")).toBeVisible();
  expect(uploads).toEqual([]);
});

test("an upload the server refuses shows the server's reason", async ({ page }) => {
  let refused = 0;
  await page.route("**/api/documents", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    refused += 1;
    return route.fulfill({ status: 409, json: { detail: "This file has already been uploaded" } });
  });
  await pickFiles(page, { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("Meeting notes") });
  await expect(uploadItem(page, "notes.txt").getByRole("alert")).toHaveText("This file has already been uploaded");
  expect(refused).toBe(1);
});

test("ticking documents changes the scope chip and the documents the next question searches", async ({ page }) => {
  const alpha = fakeDocument({ filename: "alpha.pdf" });
  const beta = fakeDocument({ filename: "beta.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
  const gamma = fakeDocument({ filename: "gamma.md", status: "processing", num_chunks: 0, content_type: "text/markdown" });
  await serveJson(page, "/documents", [alpha, beta, gamma]);
  const chat = await cannedChat(page, [events.meta({ conversationId: fakeId(), title: "What changed?" }), events.sources([]), events.delta("Nothing."), events.done()]);
  await page.reload();

  const panel = documentsPanel(page);
  const chip = page.getByTitle("Documents searched for answers (change them in the documents panel)");
  const all = panel.getByRole("checkbox", { name: "Use all documents in chat" });
  await expect(panel.getByRole("checkbox", { name: "Use alpha.pdf in chat" })).toBeChecked();
  await expect(panel.getByRole("checkbox", { name: "Use gamma.md in chat" })).toBeDisabled();
  await expect(chip).toHaveText("All documents");
  await expect(all).toHaveAccessibleDescription("Searching all 2 documents");

  await panel.getByRole("checkbox", { name: "Use beta.docx in chat" }).uncheck();
  await expect(chip).toHaveText("1 document");
  await expect(all).toHaveAccessibleDescription("Searching 1 of 2 documents");

  await all.click();
  await expect(chip).toHaveText("All documents");
  await all.uncheck();
  await expect(chip).toHaveText("No documents");
  await expect(all).toHaveAccessibleDescription("Document search off");

  await panel.getByRole("checkbox", { name: "Use alpha.pdf in chat" }).check();
  await expect(chip).toHaveText("1 document");
  await send(page, "What changed?");
  await expect.poll(() => chat.requests.length).toBe(1);
  expect(chat.requests[0].document_ids).toEqual([alpha.id]);
});
