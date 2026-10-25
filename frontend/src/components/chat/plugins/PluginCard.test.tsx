import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { GmailActivityCard, GmailDraftCard, GmailImportCard, GmailImportItem } from "../../../api/types";
import { makeDocument, makeRecipient } from "../../../test/fixtures";
import { publishDocuments } from "../../documents/documentEvents";
import type { ThreadPluginCard } from "../useChatStream";
import PluginCard, { isKnownPluginCard } from "./PluginCard";

function renderCard(card: ThreadPluginCard) {
  return render(<PluginCard card={card} />);
}

afterEach(() => {
  act(() => publishDocuments(null));
});

describe("PluginCard", () => {
  it("renders nothing for a card type this client doesn't know", () => {
    const unknown = { card_id: "p9", plugin_id: "calendar", type: "calendar_event", stopped: false } as unknown as ThreadPluginCard;
    const { container } = renderCard(unknown);
    expect(container).toBeEmptyDOMElement();
    expect(isKnownPluginCard(unknown)).toBe(false);
  });
});

describe("Gmail activity", () => {
  const activity = (overrides: Partial<GmailActivityCard> = {}, stopped = false): ThreadPluginCard => ({
    card_id: "g1",
    plugin_id: "gmail",
    type: "gmail_activity",
    status: "done",
    action: "search",
    query: "invoice",
    count: 8,
    error: null,
    ...overrides,
    stopped,
  });

  it.each<[string, ThreadPluginCard, string]>([
    ["a running search", activity({ status: "running", count: 0 }), "Searching Gmail for “invoice”…"],
    ["a running search without a query", activity({ status: "running", query: null }), "Searching Gmail…"],
    ["a stopped read", activity({ status: "running", action: "read_message" }, true), "Reading emails: stopped"],
    ["a running thread read", activity({ status: "running", action: "read_thread" }), "Reading a conversation…"],
    ["a finished search", activity(), "Searched Gmail for “invoice” · 8 results"],
    ["a search without results", activity({ count: 0, query: null }), "Searched Gmail · no results"],
    ["one search result", activity({ count: 1 }), "Searched Gmail for “invoice” · 1 result"],
    ["one email read", activity({ action: "read_message", count: 1 }), "Read an email"],
    ["several emails read", activity({ action: "read_message", count: 3 }), "Read 3 emails"],
    ["a conversation read", activity({ action: "read_thread", count: 4 }), "Read a conversation (4 messages)"],
    ["a failed search", activity({ status: "failed", error: "Gmail is rate limiting." }), "Gmail search failed: Gmail is rate limiting."],
    ["a failed thread read", activity({ status: "failed", action: "read_thread" }), "Reading the conversation failed"],
    ["a failed read", activity({ status: "failed", action: "read_message" }), "Reading emails failed"],
  ])("describes %s", (_, card, text) => {
    renderCard(card);
    expect(screen.getByText(text)).toBeInTheDocument();
  });
});

describe("Gmail draft card", () => {
  const draft = (overrides: Partial<GmailDraftCard> = {}, stopped = false): ThreadPluginCard => ({
    card_id: "g2",
    plugin_id: "gmail",
    type: "gmail_draft",
    status: "created",
    account_email: "maya@example.com",
    to: [makeRecipient()],
    cc: [makeRecipient({ address: "lee@example.com", name: null, unmentioned: true })],
    bcc: [],
    subject: "Q3 summary",
    preview: "Hi Priya, here is the Q3 summary.",
    attachments: [{ document_id: 101, filename: "quarterly-report.pdf", size_bytes: 482_133, unrequested: false }],
    reply_to: null,
    gmail_url: "https://mail.google.com/mail/u/0/#drafts?compose=r-123",
    error: null,
    ...overrides,
    stopped,
  });

  const card = () => within(screen.getByRole("group", { name: "Gmail draft" }));

  it("shows a saved draft with its recipients, subject, preview and link", () => {
    renderCard(draft());
    expect(card().getByText("Draft saved in Gmail")).toBeInTheDocument();
    expect(card().getByRole("link", { name: "Open in Gmail" })).toHaveAttribute(
      "href",
      "https://mail.google.com/mail/u/0/#drafts?compose=r-123",
    );
    expect(card().getByRole("link", { name: "Open in Gmail" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(card().getByText("maya@example.com")).toBeInTheDocument();
    expect(card().getByText("priya@example.com")).toBeInTheDocument();
    expect(card().getByText("not mentioned by you")).toBeInTheDocument();
    expect(card().getByText("Q3 summary")).toBeInTheDocument();
    expect(card().getByText("Hi Priya, here is the Q3 summary.")).toBeInTheDocument();
    expect(card().getByText("quarterly-report.pdf")).toBeInTheDocument();
    expect(card().getByText("Nothing was sent. Later changes to the draft in Gmail aren't shown here.")).toBeInTheDocument();
  });

  it("doesn't link to a URL that isn't https", () => {
    renderCard(draft({ gmail_url: "javascript:alert(1)" }));
    expect(card().queryByRole("link")).not.toBeInTheDocument();
  });

  it("marks a preview cut at 500 characters and a missing subject", () => {
    renderCard(draft({ preview: "a".repeat(500), subject: "" }));
    expect(card().getByText(`${"a".repeat(500)}…`)).toBeInTheDocument();
    expect(card().getByText("(no subject)")).toBeInTheDocument();
  });

  it("says what it is replying to", () => {
    renderCard(
      draft({
        reply_to: {
          message_id: "m1",
          subject: "Q3 numbers",
          sender: { address: "priya@example.com", name: "Priya Raman", unicode_domain: null },
          date: null,
        },
      }),
    );
    expect(card().getByText(/^Replying to “Q3 numbers” from/)).toHaveTextContent("Replying to “Q3 numbers” from priya@example.com");
  });

  it.each<[string, ThreadPluginCard, string]>([
    ["writing", draft({ status: "writing", gmail_url: null }), "Writing a draft…"],
    ["creating", draft({ status: "creating", gmail_url: null }), "Saving the draft in Gmail…"],
    ["stopped while writing", draft({ status: "writing" }, true), "Stopped before the draft was created."],
    ["stopped while creating", draft({ status: "creating" }, true), "Stopped; it may have finished. Check your Gmail drafts."],
  ])("reports a draft %s without a link", (_, value, headline) => {
    renderCard(value);
    expect(card().getByText(headline)).toBeInTheDocument();
    expect(card().queryByRole("link")).not.toBeInTheDocument();
    expect(card().queryByText(/^Nothing was sent/)).not.toBeInTheDocument();
  });

  it("explains a failed draft, without details it never had", () => {
    renderCard(
      draft({ status: "failed", to: [], cc: [], subject: null, preview: "", attachments: [], error: "The request check refused this draft." }),
    );
    expect(card().getByText("Couldn't create the draft")).toBeInTheDocument();
    expect(card().getByText("The request check refused this draft.")).toBeInTheDocument();
    expect(card().queryByText("Subject")).not.toBeInTheDocument();
  });
});

describe("Gmail import card", () => {
  const item = (overrides: Partial<GmailImportItem>): GmailImportItem => ({
    kind: "thread",
    thread_id: "t1",
    message_id: null,
    part_id: null,
    label: "Q3 numbers",
    status: "imported",
    reason: null,
    document_id: null,
    document: null,
    ...overrides,
  });

  const imported = (overrides: Partial<GmailImportCard> = {}, stopped = false): ThreadPluginCard => ({
    card_id: "g3",
    plugin_id: "gmail",
    type: "gmail_import",
    status: "done",
    items: [],
    error: null,
    ...overrides,
    stopped,
  });

  const card = () => within(screen.getByRole("group", { name: "Import from Gmail" }));
  const entry = (label: string) =>
    within(card().getAllByRole("listitem").find((li) => within(li).queryByText(label)) as HTMLElement);

  it("lists what was imported, skipped and failed", () => {
    const thread = makeDocument({ id: 801, filename: "Q3 numbers.txt", status: "processing", imported_from: "Gmail" });
    const attachment = makeDocument({ id: 802, filename: "q3.xlsx", status: "ready", imported_from: "Gmail" });
    renderCard(
      imported({
        items: [
          item({ document_id: 801, document: thread }),
          item({ kind: "attachment", message_id: "m1", part_id: "2", label: "q3.xlsx", status: "already_existed", document_id: 802, document: attachment }),
          item({ kind: "attachment", message_id: "m1", part_id: "3", label: "logo.png", status: "skipped", reason: "Images can't be imported." }),
          item({ thread_id: "t2", label: "", status: "failed", reason: "Gmail returned an error." }),
        ],
      }),
    );

    expect(card().getByText("Imported 2 items from Gmail")).toBeInTheDocument();
    expect(entry("Q3 numbers").getByText("Indexing…")).toBeInTheDocument();
    expect(entry("q3.xlsx").getByText("Already in your documents")).toBeInTheDocument();
    expect(entry("logo.png").getByText("Skipped")).toBeInTheDocument();
    expect(entry("logo.png").getByText("Images can't be imported.")).toBeInTheDocument();
    expect(entry("(no subject)").getByText("Failed")).toBeInTheDocument();
    expect(card().getByText("Imported items are private documents marked “Imported from Gmail”.")).toBeInTheDocument();
  });

  it("follows the documents panel's list for each saved item", () => {
    const thread = makeDocument({ id: 811, filename: "Budget.txt", status: "processing", imported_from: "Gmail" });
    const sheet = makeDocument({ id: 812, filename: "budget.xlsx", status: "processing", imported_from: "Gmail" });
    renderCard(
      imported({
        items: [
          item({ label: "Budget", document_id: 811, document: thread }),
          item({ kind: "attachment", message_id: "m2", part_id: "1", label: "budget.xlsx", document_id: 812, document: sheet }),
        ],
      }),
    );

    act(() => publishDocuments([{ ...thread, status: "ready", is_stale: true }, { ...sheet, status: "failed", error: "Unreadable" }]));
    expect(entry("Budget").getByText("Needs re-index")).toBeInTheDocument();
    expect(entry("budget.xlsx").getByText("Saved, but indexing failed")).toHaveAttribute("title", "Unreadable");

    act(() => publishDocuments([{ ...thread, status: "ready" }]));
    expect(entry("Budget").getByText("Ready")).toBeInTheDocument();
    expect(entry("budget.xlsx").getByText("Deleted from your documents")).toBeInTheDocument();
  });

  it.each<[string, ThreadPluginCard, string]>([
    ["importing", imported({ status: "importing" }), "Importing from Gmail…"],
    ["done with nothing saved", imported({ items: [item({ status: "skipped" })] }), "Nothing was imported from Gmail"],
    ["stopped", imported({ status: "importing" }, true), "Stopped; it may have finished. Check your documents."],
  ])("reports an import %s", (_, value, headline) => {
    renderCard(value);
    expect(card().getByText(headline)).toBeInTheDocument();
  });

  it("explains a failed import", () => {
    renderCard(imported({ status: "failed", error: "The request check refused this import." }));
    expect(card().getByText("Couldn't import from Gmail")).toBeInTheDocument();
    expect(card().getByText("The request check refused this import.")).toBeInTheDocument();
    expect(card().queryByText(/^Imported items are private/)).not.toBeInTheDocument();
  });
});
