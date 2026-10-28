import {
  assistantMessages,
  composer,
  conversationList,
  documentsPanel,
  openChat,
  send,
  sendButton,
  servedModels,
  stopButton,
  userMessages,
} from "../support/app";
import { fakeDocument, fakeId, serveJson } from "../support/fakes";
import { expect, signIn, test } from "../support/fixtures";
import { CHAT_STREAM, cannedChat, events, source } from "../support/sse";

// Every answer here is canned (page.route on POST /api/chat/stream): no request reaches the backend or a model.

test.beforeEach(async ({ context, user }) => {
  await signIn(context, user);
});

test("an answer streams in piece by piece, with citations and its sources", async ({ page, sseStub }) => {
  await sseStub.attach(page);
  await openChat(page);
  const { initial: defaultModel } = await servedModels(page);

  const question = "How many vacation days do employees get?";
  await composer(page).fill(question);
  await composer(page).press("Enter");
  const stream = await sseStub.next();
  expect(stream.body).toEqual({ conversation_id: null, message: question, model_id: defaultModel?.id ?? null, document_ids: null });
  await expect(userMessages(page)).toHaveText(question);

  const conversationId = fakeId();
  stream.send(
    events.meta({ conversationId, title: question, model: { id: defaultModel?.id ?? 1, display_name: "Canned Model" } }),
    events.sources([
      source(1, "handbook.pdf", "Employees receive 25 days of paid vacation per year.", 3),
      source(2, "policy-2026.docx", "Unused vacation days carry over until the end of March."),
    ]),
    events.delta("Employees get **25 days** of paid vacation"),
  );
  const answer = assistantMessages(page).last();
  await expect(answer).toContainText("Employees get 25 days of paid vacation");
  await expect(answer).toHaveAttribute("aria-busy", "true");
  await expect(stopButton(page)).toBeVisible();
  await expect(answer).not.toContainText("carry over");
  await expect(page).toHaveURL(new RegExp(`/c/${conversationId}$`));

  stream.keepalive();
  stream.send(events.delta(" a year [1]. Unused days carry over"));
  await expect(answer).toContainText("Unused days carry over");
  await expect(answer).not.toContainText("until March");
  stream.end(events.delta(" until March [2]."), events.done({ prompt_tokens: 1000, completion_tokens: 234 }));

  await expect(answer).toHaveAttribute("aria-busy", "false");
  await expect(answer).toContainText("until March");
  await expect(sendButton(page)).toBeVisible();
  await expect(answer).toContainText("Canned Model · 1,234 tokens");
  await expect(answer.getByRole("button", { name: "Copy answer" })).toBeVisible();

  const cite1 = answer.getByRole("button", { name: "Source 1: handbook.pdf, page 3" });
  const cite2 = answer.getByRole("button", { name: "Source 2: policy-2026.docx" });
  await expect(cite1).toHaveText("1");
  await expect(cite2).toHaveText("2");

  const toggle = answer.getByRole("button", { name: "2 sources" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const handbook = answer.getByRole("listitem").filter({ hasText: "handbook.pdf" });
  await expect(handbook).toContainText("page 3");
  await expect(handbook).toContainText("Employees receive 25 days of paid vacation per year.");
  await expect(answer.getByRole("listitem").filter({ hasText: "policy-2026.docx" })).toContainText("carry over until the end of March");

  // A citation opens the sources it points to.
  await toggle.click();
  await expect(answer.getByRole("listitem")).toHaveCount(0);
  await cite2.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(answer.getByRole("listitem").filter({ hasText: "policy-2026.docx" })).toBeVisible();

  // The new conversation from the meta event is in the sidebar and the top bar.
  await expect(conversationList(page).getByRole("link", { name: question })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { level: 1, name: question })).toBeVisible();
  await expect(page).toHaveTitle(`${question} · AskNuvexusDoc`);
});

test("a canned answer is never saved: the server doesn't know the conversation", async ({ page }) => {
  // The backend saves a conversation and the user's message only when the request reaches it; here it never does,
  // so the sidebar entry comes from the meta event alone.
  await cannedChat(page, [events.meta({ conversationId: fakeId(), title: "Hello there" }), events.sources([]), events.delta("Hi!"), events.done()]);
  await openChat(page);
  await send(page, "Hello there");
  await expect(assistantMessages(page)).toHaveText(/Hi!/);
  const link = conversationList(page).getByRole("link", { name: "Hello there" });
  await expect(link).toHaveAttribute("aria-current", "page");

  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: /^How can I help/ })).toBeVisible();
  await expect(link).not.toHaveAttribute("aria-current", "page");

  // Opening it asks the server, which has never seen it.
  await link.click();
  await expect(page.getByRole("status").filter({ hasText: "That conversation doesn't exist or has been deleted." })).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  await page.reload();
  await expect(page.getByText("No conversations yet. Your chats will appear here.")).toBeVisible();
  expect(await (await page.request.get("/api/conversations")).json()).toEqual([]);
});

test("a file event shows a file card with type, size, status and Download", async ({ page, user }) => {
  const doc = fakeDocument({
    filename: "Q3 report.pdf",
    generated: true,
    size_bytes: 12_288,
    num_pages: 2,
    owner_id: user.id,
    owner_username: user.username,
  });
  let created = false;
  await serveJson(page, "/documents", () => (created ? [doc] : []));
  await cannedChat(page, () => {
    created = true;
    return [
      events.meta({ conversationId: fakeId(), title: "Make a PDF report of the Q3 figures" }),
      events.sources([]),
      events.delta("Here is the report."),
      events.fileWriting("f1", null, null),
      events.fileWriting("f1", "Q3 report", "pdf", 1800),
      events.fileCreated("f1", doc, "pdf"),
      events.delta("\n\nIt is saved in your documents."),
      events.done(),
    ];
  });
  await openChat(page);
  await send(page, "Make a PDF report of the Q3 figures");

  const answer = assistantMessages(page).last();
  await expect(answer).toContainText("It is saved in your documents.");
  const files = answer.getByRole("list", { name: "Files" });
  const card = files.getByRole("group", { name: "Q3 report.pdf" });
  await expect(card).toContainText("PDF");
  await expect(card).toContainText("12 KB");
  await expect(card).toContainText("Ready");
  await expect(card.getByRole("link", { name: "Download Q3 report.pdf" })).toHaveAttribute("href", `/api/documents/${doc.id}/download`);

  const panel = documentsPanel(page);
  const row = panel.getByRole("listitem").filter({ hasText: "Q3 report.pdf" });
  await expect(row).toContainText("Created in chat");
  await expect(row.getByRole("link", { name: "Download Q3 report.pdf" })).toBeVisible();
});

test("a warning and an error event are shown with the partial answer, and Retry is offered", async ({ page }) => {
  await cannedChat(page, [
    events.meta({ conversationId: fakeId(), title: "Summarize the contract" }),
    events.sources([]),
    events.warning("Document search unavailable: the vector database didn't answer."),
    events.delta("The contract covers"),
    events.error("The model provider returned an error. Please try again."),
  ]);
  await openChat(page);
  await send(page, "Summarize the contract");

  const answer = assistantMessages(page).last();
  await expect(answer).toContainText("The contract covers");
  await expect(answer).toContainText("Document search unavailable: the vector database didn't answer.");
  await expect(answer).toContainText("The model provider returned an error. Please try again.");
  await expect(answer.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(answer).toHaveAttribute("aria-busy", "false");
  await expect(sendButton(page)).toBeVisible();
});

test("a request refused before streaming shows the reason and gives the text back", async ({ page }) => {
  const detail = "Rate limit reached: 20 requests per minute. Try again in a minute.";
  await page.route(CHAT_STREAM, (route) => route.fulfill({ status: 429, json: { detail }, headers: { "Retry-After": "30" } }));
  await openChat(page);
  await send(page, "Is this too fast?");
  await expect(page.getByRole("alert").filter({ hasText: detail })).toBeVisible();
  await expect(composer(page)).toHaveValue("Is this too fast?");
  await expect(userMessages(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Dismiss error" }).click();
  await expect(page.getByRole("alert").filter({ hasText: detail })).toHaveCount(0);
});

test("Stop keeps the partial answer and ends the request", async ({ page, sseStub }) => {
  await sseStub.attach(page);
  await openChat(page);
  await send(page, "Tell me a long story");
  const stream = await sseStub.next();
  stream.send(events.meta({ conversationId: fakeId(), title: "Tell me a long story" }), events.sources([]), events.delta("Once upon a time"));

  const answer = assistantMessages(page).last();
  await expect(answer).toContainText("Once upon a time");
  await stopButton(page).click();
  await stream.closedByClient;
  await expect(answer.getByText("Stopped", { exact: true })).toBeVisible();
  await expect(answer).toContainText("Once upon a time");
  await expect(answer.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(sendButton(page)).toBeVisible();
  await expect(composer(page)).toHaveValue("");
});

test("Stop before the answer starts drops the turn and gives the question back", async ({ page, sseStub }) => {
  await sseStub.attach(page);
  await openChat(page);
  await send(page, "Are you there?");
  const stream = await sseStub.next();
  await expect(stopButton(page)).toBeVisible();
  await stopButton(page).click();
  await stream.closedByClient;
  await expect(page.getByRole("alert").filter({ hasText: "Stopped before the answer started." })).toBeVisible();
  await expect(composer(page)).toHaveValue("Are you there?");
  await expect(userMessages(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/$/);
});
