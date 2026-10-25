import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  DocumentItem,
  GmailImportResult,
  GmailMessageView,
  GmailThreadDetail,
  GmailThreadPage,
  GmailThreadSummary,
  PluginInfo,
} from "../../api/types";
import { formatBytes } from "../../lib/format";
import { makeDocument, makeGmailPlugin } from "../../test/fixtures";
import { deferred, errorResponse, stubFetch, type FetchCall, type Route } from "../../test/http";
import { subscribeToAnnouncements, subscribeToReloadRequests } from "../documents/documentEvents";
import GmailImportModal from "./GmailImportModal";

function thread(overrides: Partial<GmailThreadSummary> = {}): GmailThreadSummary {
  return {
    thread_id: "t1",
    subject: "Q3 numbers",
    sender: "Priya Raman",
    date: null,
    snippet: "Attached are the final Q3 numbers.",
    message_count: 3,
    unread: true,
    gmail_url: "https://mail.google.com/mail/u/0/#all/t1",
    ...overrides,
  };
}

const q3 = thread();
const offsite = thread({ thread_id: "t2", subject: "", sender: "Sam Ortiz", snippet: "", message_count: 1, unread: false });

function page(threads: GmailThreadSummary[], overrides: Partial<GmailThreadPage> = {}): GmailThreadPage {
  return { threads, next_page_token: null, result_size_estimate: threads.length, ...overrides };
}

function message(overrides: Partial<GmailMessageView> = {}): GmailMessageView {
  return {
    message_id: "m1",
    thread_id: "t1",
    sender: "Priya Raman <priya@example.com>",
    to: ["maya@example.com"],
    cc: ["lee@example.com"],
    date: null,
    subject: "Q3 numbers",
    snippet: "Attached are the final Q3 numbers.",
    body_text: "Hi Maya,\n\nAttached are the final <b>Q3</b> numbers.",
    body_truncated: false,
    attachments: [
      { part_id: "2", filename: "q3.xlsx", mime_type: "application/vnd.ms-excel", size_bytes: 24_576, importable: true, reason: null },
      { part_id: "3", filename: "logo.png", mime_type: "image/png", size_bytes: 4_096, importable: false, reason: "Images can't be imported." },
    ],
    unread: true,
    ...overrides,
  };
}

const detail: GmailThreadDetail = {
  thread_id: "t1",
  subject: "Q3 numbers",
  gmail_url: "https://mail.google.com/mail/u/0/#all/t1",
  messages: [message(), message({ message_id: "m2", sender: "Maya Chen <maya@example.com>", to: [], cc: [], attachments: [], body_text: "" })],
  messages_truncated: false,
};

function renderModal(routes: Record<string, Route> = {}, plugin: PluginInfo = makeGmailPlugin()) {
  const calls = stubFetch({
    "POST /plugins/gmail/threads/search": page([q3, offsite], { result_size_estimate: 40 }),
    "GET /plugins/gmail/threads/t1": detail,
    ...routes,
  });
  const onClose = vi.fn();
  render(<GmailImportModal plugin={plugin} onClose={onClose} />);
  expect(screen.getByRole("dialog", { name: "Import from Gmail" })).toBeInTheDocument();
  return { calls, onClose };
}

const searches = (calls: FetchCall[]) => calls.filter((call) => call.path === "/plugins/gmail/threads/search").map((call) => call.body);
const toggle = (name: RegExp) => screen.getByRole("button", { name });

async function openThread(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Q3 numbers/ }));
  return within(await screen.findByRole("article", { name: "Email from Priya Raman <priya@example.com>" }));
}

describe("GmailImportModal: browsing", () => {
  it("lists the inbox first", async () => {
    const { calls } = renderModal();

    expect(screen.getByText("Searching…")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Inbox · 2 conversations shown of about 40. Tick a conversation to import it, or open it to pick attachments.",
      ),
    ).toBeInTheDocument();
    expect(searches(calls)).toEqual([{ q: null, page_token: null, max_results: 20 }]);
    expect(toggle(/Q3 numbers/)).toHaveAccessibleName(
      /^Unread,\s*Priya Raman,\s*3\s*messages,\s*Q3 numbers,\s*Attached are the final Q3 numbers\.$/,
    );
    expect(toggle(/Q3 numbers/)).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("checkbox", { name: "Import the conversation “Q3 numbers”" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Import the conversation “(no subject)”" })).toBeInTheDocument();
  });

  it("searches with Gmail's syntax and says when nothing matches", async () => {
    const user = userEvent.setup();
    const { calls } = renderModal({
      "POST /plugins/gmail/threads/search": ({ body }) =>
        (body as { q: string | null }).q === "from:priya" ? page([q3]) : (body as { q: string | null }).q ? page([]) : page([q3, offsite]),
    });
    const search = await screen.findByRole("searchbox", { name: "Search Gmail" });
    await screen.findByText(/^Inbox · 2 conversations shown\./);

    await user.type(search, "  from:priya {Enter}");
    expect(await screen.findByText(/^Results for “from:priya” · 1 conversation shown\./)).toBeInTheDocument();
    await user.clear(search);
    await user.type(search, "has:attachment budget");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("No conversations match this search.")).toBeInTheDocument();
    expect(searches(calls).map((body) => (body as { q: string | null }).q)).toEqual([null, "from:priya", "has:attachment budget"]);
  });

  it("says when the inbox is empty, and why a search failed", async () => {
    const user = userEvent.setup();
    let reply: Route = page([]);
    renderModal({ "POST /plugins/gmail/threads/search": () => reply });
    expect(await screen.findByText("Your inbox is empty.")).toBeInTheDocument();

    reply = errorResponse(409, "Gmail needs to be reconnected.");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Gmail needs to be reconnected.");
  });

  it("loads more results without repeating conversations", async () => {
    const user = userEvent.setup();
    const t3 = thread({ thread_id: "t3", subject: "Offsite agenda" });
    const { calls } = renderModal({
      "POST /plugins/gmail/threads/search": ({ body }) =>
        (body as { page_token: string | null }).page_token === "p2"
          ? page([offsite, t3])
          : page([q3, offsite], { next_page_token: "p2", result_size_estimate: 3 }),
    });

    await user.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByText(/^Inbox · 3 conversations shown\./)).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(searches(calls)[1]).toEqual({ q: null, page_token: "p2", max_results: 20 });
  });

  it("opens a conversation inline, showing email text as text", async () => {
    const user = userEvent.setup();
    renderModal();
    const email = await openThread(user);

    expect(toggle(/Q3 numbers/)).toHaveAttribute("aria-expanded", "true");
    expect(email.getByText("To: maya@example.com · Cc: lee@example.com")).toBeInTheDocument();
    expect(email.getByText(/Attached are the final <b>Q3<\/b> numbers\./)).toBeInTheDocument();
    expect(email.queryByText("Q3", { selector: "b" })).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Email from Maya Chen <maya@example.com>" })).toHaveTextContent("No text.");
    expect(email.getByRole("checkbox", { name: "q3.xlsx" })).not.toBeChecked();
    expect(email.queryByRole("checkbox", { name: "logo.png" })).not.toBeInTheDocument();
    expect(email.getByText("Images can't be imported.")).toBeInTheDocument();
    expect(email.getByText(formatBytes(4_096))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in Gmail" })).toHaveAttribute("rel", "noopener noreferrer");

    await user.click(toggle(/Q3 numbers/));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });

  it("collapses long emails and notes what is cut off", async () => {
    const user = userEvent.setup();
    const long = message({ body_text: Array.from({ length: 20 }, (_, i) => `Line ${i + 1}`).join("\n"), body_truncated: true });
    renderModal({ "GET /plugins/gmail/threads/t1": { ...detail, messages: [long], messages_truncated: true } });
    const email = await openThread(user);

    expect(screen.getByText("Only the newest 1 messages are shown.")).toBeInTheDocument();
    expect(email.getByText("This email is longer than shown here.")).toBeInTheDocument();
    const more = email.getByRole("button", { name: "Show more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(email.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
  });

  it("retries a conversation that couldn't be opened", async () => {
    const user = userEvent.setup();
    let fail = true;
    renderModal({ "GET /plugins/gmail/threads/t1": () => (fail ? errorResponse(502, "Gmail didn't answer.") : detail) });

    await user.click(await screen.findByRole("button", { name: /Q3 numbers/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Gmail didn't answer.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("article", { name: "Email from Priya Raman <priya@example.com>" })).toBeInTheDocument();
  });
});

describe("GmailImportModal: importing", () => {
  const threadDoc = makeDocument({ id: 901, filename: "Q3 numbers.txt", status: "pending", imported_from: "Gmail" });
  const sheetDoc = makeDocument({ id: 902, filename: "q3.xlsx", status: "pending", imported_from: "Gmail" });
  const result: GmailImportResult = {
    items: [
      { kind: "thread", thread_id: "t1", message_id: null, part_id: null, label: "Q3 numbers", status: "imported", reason: null, document_id: 901, document: threadDoc },
      { kind: "attachment", thread_id: "t1", message_id: "m1", part_id: "2", label: "q3.xlsx", status: "already_existed", reason: null, document_id: 902, document: sheetDoc },
      { kind: "thread", thread_id: "t2", message_id: null, part_id: null, label: "", status: "skipped", reason: "Empty conversation.", document_id: null, document: null },
    ],
  };

  async function pickThreeItems(user: ReturnType<typeof userEvent.setup>) {
    const email = await openThread(user);
    await user.click(screen.getByRole("checkbox", { name: "Import the conversation “Q3 numbers”" }));
    await user.click(email.getByRole("checkbox", { name: "q3.xlsx" }));
    await user.click(screen.getByRole("checkbox", { name: "Import the conversation “(no subject)”" }));
  }

  it("counts the selection and clears it", async () => {
    const user = userEvent.setup();
    renderModal();
    expect(await screen.findByText("Nothing selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import 0 items" })).toBeDisabled();

    await pickThreeItems(user);
    expect(screen.getByText("3 items selected")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "q3.xlsx" }));
    expect(screen.getByText("2 items selected")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText("Nothing selected")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Import the conversation “Q3 numbers”" })).not.toBeChecked();
  });

  it("refuses more items than one import allows", async () => {
    const user = userEvent.setup();
    renderModal({}, makeGmailPlugin({ import_max_items: 2 }));
    await pickThreeItems(user);
    expect(screen.getByText("3 selected: at most 2 per import")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import 3 items" })).toBeDisabled();
  });

  it("imports the picked conversations and attachments, then shows each result", async () => {
    const user = userEvent.setup();
    const reply = deferred<GmailImportResult>();
    const announced: DocumentItem[] = [];
    const unsubscribe = subscribeToAnnouncements((doc) => announced.push(doc));
    const { calls, onClose } = renderModal({ "POST /plugins/gmail/import": () => reply.promise });
    await pickThreeItems(user);
    await user.click(screen.getByRole("button", { name: "Import 3 items" }));

    expect(calls.find((call) => call.path === "/plugins/gmail/import")?.body).toEqual({
      threads: [
        { thread_id: "t1", include_thread: true, attachments: [{ message_id: "m1", part_id: "2" }] },
        { thread_id: "t2", include_thread: true, attachments: [] },
      ],
    });
    // Focus moves in an effect, which can run after the click has returned.
    await waitFor(() =>
      expect(document.activeElement).toHaveTextContent(/^Importing 3 items… This can take up to a minute and a half\./),
    );
    const importButton = screen.getByRole("button", { name: "Importing…" });
    expect(importButton).toBeDisabled();
    expect(importButton).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Close" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Continue in background" })).toBeEnabled();

    reply.resolve(result);
    const summary = await screen.findByText(
      "2 items saved to your documents as “Imported from Gmail”. They can be searched once indexing finishes.",
    );
    await waitFor(() => expect(summary).toHaveFocus());
    const results = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(results).toEqual(["Q3 numbersImported", "q3.xlsxAlready in your documents", "(no subject)SkippedEmpty conversation."]);
    expect(announced.map((doc) => doc.id)).toEqual([901, 902]);
    unsubscribe();

    await user.click(screen.getByRole("button", { name: "Import more" }));
    await waitFor(() => expect(screen.getByRole("searchbox", { name: "Search Gmail" })).toHaveFocus());
    expect(screen.getByText("Nothing selected")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("says when nothing was imported and closes from Done", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal({ "POST /plugins/gmail/import": { items: [result.items[2]] } });
    await user.click(await screen.findByRole("checkbox", { name: "Import the conversation “(no subject)”" }));
    await user.click(screen.getByRole("button", { name: "Import 1 item" }));

    expect(await screen.findByText("Nothing was imported.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the selection after a failed import and asks the documents panel to reload", async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    const unsubscribe = subscribeToReloadRequests(reload);
    renderModal({ "POST /plugins/gmail/import": () => errorResponse(403, "The admin turned off importing.") });
    await user.click(await screen.findByRole("checkbox", { name: "Import the conversation “Q3 numbers”" }));
    await user.click(screen.getByRole("button", { name: "Import 1 item" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The admin turned off importing.");
    await waitFor(() => expect(screen.getByRole("searchbox", { name: "Search Gmail" })).toHaveFocus());
    expect(screen.getByText("1 item selected")).toBeInTheDocument();
    expect(reload).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await screen.findByText(/^Inbox/);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
