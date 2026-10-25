import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GmailSendCard as SendCard, PendingSend } from "../../../api/types";
import { formatDateTime } from "../../../lib/format";
import { makeDocument, makeMe, makePendingSend, makeRecipient } from "../../../test/fixtures";
import { deferred, errorResponse, jsonResponse, stubFetch, type Route } from "../../../test/http";
import { renderWithAuth } from "../../../test/render";
import { publishDocuments } from "../../documents/documentEvents";
import GmailSendCard from "./GmailSendCard";

const SEND_PATH = "/plugins/gmail/sends/31";

function proposed(pending: Partial<PendingSend> = {}): SendCard {
  return { card_id: "g1", plugin_id: "gmail", type: "gmail_send", status: "proposed", pending: makePendingSend(pending), error: null };
}

function renderCard(card: SendCard, stopped = false) {
  return render(<GmailSendCard card={card} stopped={stopped} />);
}

function renderEditable(card: SendCard, routes: Record<string, Route> = {}) {
  const calls = stubFetch({ "GET /auth/me": makeMe(), ...routes });
  return { calls, rendered: renderWithAuth(<GmailSendCard card={card} stopped={false} />) };
}

function card() {
  return screen.getByRole("group", { name: /^Email to send/ });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T09:55:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
  act(() => publishDocuments(null));
});

describe("GmailSendCard before the email is proposed", () => {
  const base: SendCard = { card_id: "g1", plugin_id: "gmail", type: "gmail_send", status: "writing", pending: null, error: null };

  it("says the email is being prepared", () => {
    renderCard(base);
    expect(screen.getByRole("group", { name: "Email to send" })).toHaveTextContent("Preparing an email for you to review…");
  });

  it("says nothing was sent when the answer stopped first", () => {
    renderCard(base, true);
    expect(card()).toHaveTextContent("Stopped before the email was prepared. Nothing was sent.");
  });

  it("explains why the email couldn't be prepared", () => {
    renderCard({ ...base, status: "failed", error: "The request check refused this email." });
    expect(card()).toHaveTextContent("Couldn't prepare the email");
    expect(within(card()).getByText("The request check refused this email.")).toBeInTheDocument();
  });
});

describe("GmailSendCard showing a proposed email", () => {
  it("shows exactly what Send would send, with the time left", () => {
    renderCard(proposed());
    const group = screen.getByRole("group", { name: "Email to send: Q3 summary" });
    expect(within(group).getByText("Email ready to send")).toBeInTheDocument();
    expect(within(group).getByText("Not sent")).toBeInTheDocument();
    expect(within(group).getByText("30:00 left to send")).toBeInTheDocument();
    expect(within(group).getByText("Review the whole email. Nothing is sent until you click Send.")).toBeInTheDocument();
    expect(within(group).getByText("maya@example.com")).toBeInTheDocument();
    expect(within(group).getByText("priya@example.com")).toBeInTheDocument();
    expect(within(group).getByText("Priya Raman")).toBeInTheDocument();
    expect(within(group).getByRole("region", { name: "Email text" })).toHaveTextContent("Here is the Q3 summary you asked for.");
    for (const name of ["Send", "Edit", "Cancel"]) {
      expect(within(group).getByRole("button", { name })).toBeEnabled();
    }
  });

  it("flags recipients the user never mentioned, Bcc and internationalized domains", () => {
    renderCard(
      proposed({
        to: [makeRecipient(), makeRecipient({ address: "finance@example.net", name: "Finance", unmentioned: true })],
        cc: [makeRecipient({ address: "info@xn--bcher-kva.de", name: null, unicode_domain: "bücher.de" })],
        bcc: [makeRecipient({ address: "audit@example.org", name: null })],
      }),
    );
    const flagged = screen.getByText("finance@example.net").closest("li") as HTMLElement;
    expect(within(flagged).getByText("not mentioned by you")).toBeInTheDocument();
    expect(screen.getAllByText("not mentioned by you")).toHaveLength(1);

    const idn = screen.getByText("info@xn--bcher-kva.de").closest("li") as HTMLElement;
    expect(within(idn).getByText("info@bücher.de")).toBeInTheDocument();
    expect(idn).toHaveTextContent("Internationalized domain: check the spelling");

    const bccRow = screen.getByText("Bcc (hidden from the other recipients)").closest("div") as HTMLElement;
    expect(within(bccRow).getByText("audit@example.org")).toBeInTheDocument();
  });

  it("shows the email it replies to and flags attachments", () => {
    renderCard(
      proposed({
        reply_to: {
          message_id: "18f2a9c0d1e2f3a4",
          subject: "Q3 numbers",
          sender: { address: "priya@example.com", name: "Priya Raman", unicode_domain: null },
          date: null,
        },
        attachments: [
          { document_id: 205, filename: "summary.docx", size_bytes: 18_432, unrequested: false },
          { document_id: 206, filename: "salaries.xlsx", size_bytes: 9_000, unrequested: true },
          { document_id: null, filename: "old-draft.pdf", size_bytes: 1_000, unrequested: false },
        ],
      }),
    );
    expect(screen.getByText(/Replying to “Q3 numbers” from/)).toHaveTextContent("Replying to “Q3 numbers” from priya@example.com");
    const unrequested = screen.getByText("salaries.xlsx").closest("li") as HTMLElement;
    expect(within(unrequested).getByText("not named by you")).toBeInTheDocument();
    const deleted = screen.getByText("old-draft.pdf").closest("li") as HTMLElement;
    expect(within(deleted).getByText("deleted")).toBeInTheDocument();
  });

  it("names an email without a subject", () => {
    renderCard(proposed({ subject: "" }));
    expect(screen.getByRole("group", { name: "Email to send: no subject" })).toHaveTextContent("(no subject)");
  });
});

describe("GmailSendCard actions", () => {
  it("sends the email with the content hash of what is shown", async () => {
    const response = deferred<Response>();
    const sent = makePendingSend({
      status: "sent",
      sent_at: "2026-10-20T09:56:00Z",
      gmail_url: "https://mail.google.com/mail/u/0/#sent/18f2b",
    });
    const calls = stubFetch({ [`POST ${SEND_PATH}/send`]: () => response.promise });
    const user = userEvent.setup();
    renderCard(proposed());

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(calls).toEqual([expect.objectContaining({ method: "POST", path: `${SEND_PATH}/send`, body: { content_hash: "sha256:9f2c1e" } })]);
    expect(within(card()).getByText("Sending…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toHaveAttribute("aria-busy", "true");
    for (const name of ["Send", "Edit", "Cancel"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }

    await act(async () => response.resolve(jsonResponse(sent)));
    expect(within(card()).getByText("Email sent")).toBeInTheDocument();
    expect(within(card()).getByText("Sent")).toBeInTheDocument();
    expect(within(card()).getByText(`Sent ${formatDateTime(sent.sent_at)}`)).toBeInTheDocument();
    const open = within(card()).getByRole("link", { name: "Open in Gmail" });
    expect(open).toHaveAttribute("href", sent.gmail_url);
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(card()).queryByRole("button")).not.toBeInTheDocument();
    expect(calls).toHaveLength(1);
  });

  it("shows the server's refusal and then the email's current state", async () => {
    const calls = stubFetch({
      [`POST ${SEND_PATH}/send`]: () => errorResponse(409, "This email has expired. Ask the assistant again."),
      [`GET ${SEND_PATH}`]: makePendingSend({ status: "expired" }),
    });
    const user = userEvent.setup();
    renderCard(proposed());

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This email has expired. Ask the assistant again.");
    expect(within(card()).getByText("Expired. Nothing was sent.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(calls.map(({ method, path }) => `${method} ${path}`)).toEqual([`POST ${SEND_PATH}/send`, `GET ${SEND_PATH}`]);
  });

  it("says the outcome is unknown when the server can't be reached", async () => {
    const calls = stubFetch({
      [`POST ${SEND_PATH}/send`]: () => Promise.reject(new TypeError("Failed to fetch")),
      [`GET ${SEND_PATH}`]: () => Promise.reject(new TypeError("Failed to fetch")),
    });
    const user = userEvent.setup();
    renderCard(proposed());

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't reach the server, so the email may or may not have been sent. Check your Sent folder.",
    );
    expect(within(card()).getByText("Email ready to send")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(calls).toHaveLength(2);
  });

  it("doesn't reload after a rate limit", async () => {
    const calls = stubFetch({ [`POST ${SEND_PATH}/send`]: () => errorResponse(429, "Too many requests. Try again in a minute.") });
    const user = userEvent.setup();
    renderCard(proposed());

    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests. Try again in a minute.");
    expect(calls).toHaveLength(1);
  });

  it("cancels the email", async () => {
    const calls = stubFetch({ [`POST ${SEND_PATH}/cancel`]: makePendingSend({ status: "cancelled" }) });
    const user = userEvent.setup();
    renderCard(proposed());

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await within(card()).findByText("Cancelled. Nothing was sent.")).toBeInTheDocument();
    expect(within(card()).getByText("Cancelled")).toBeInTheDocument();
    expect(calls).toEqual([expect.objectContaining({ method: "POST", path: `${SEND_PATH}/cancel`, body: {} })]);
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("loads the newer version of a replaced email", async () => {
    const calls = stubFetch({ "GET /plugins/gmail/sends/33": makePendingSend({ id: 33, subject: "Q3 summary (final)" }) });
    const user = userEvent.setup();
    renderCard(proposed({ status: "replaced", replaced_by: 33 }));
    expect(within(card()).getByText("Replaced by a newer version")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show the newer version" }));
    expect(await screen.findByRole("group", { name: "Email to send: Q3 summary (final)" })).toHaveTextContent("Email ready to send");
    expect(calls.map((call) => call.path)).toEqual(["/plugins/gmail/sends/33"]);
  });
});

describe("GmailSendCard final states", () => {
  it("explains a failed send and offers Edit", () => {
    renderCard(proposed({ status: "failed", error: "Gmail refused the message: the attachment is too large." }));
    expect(within(card()).getByText("Email not sent")).toBeInTheDocument();
    expect(within(card()).getByText("Failed")).toBeInTheDocument();
    expect(within(card()).getByText("Gmail refused the message: the attachment is too large.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  });

  it("reads as expired once its time ran out", () => {
    vi.setSystemTime(new Date("2026-10-20T10:30:00Z"));
    renderCard(proposed());
    expect(within(card()).getByText("Expired. Nothing was sent.")).toBeInTheDocument();
    expect(within(card()).getByText("This email can no longer be sent. Ask the assistant again or use Edit.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(screen.queryByText(/left to send/)).not.toBeInTheDocument();
  });

  it("expires while shown when the countdown runs out", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T10:24:58Z"));
    renderCard(proposed());
    expect(screen.getByText("0:02 left to send")).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByText("0:01 left to send")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1_000));
    expect(within(card()).getByText("Expired. Nothing was sent.")).toBeInTheDocument();
  });

  it("looks again while the email is being sent", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T09:55:00Z"));
    const calls = stubFetch({ [`GET ${SEND_PATH}`]: makePendingSend({ status: "sent", sent_at: "2026-10-20T09:55:03Z" }) });
    renderCard(proposed({ status: "sending" }));
    expect(within(card()).getByText("Sending…")).toBeInTheDocument();
    expect(calls).toHaveLength(0);

    await act(() => vi.advanceTimersByTimeAsync(4_000));
    expect(within(card()).getByText("Email sent")).toBeInTheDocument();
    expect(calls).toHaveLength(1);
  });

  it("links to Gmail only with an https URL", () => {
    renderCard(proposed({ status: "sent", sent_at: "2026-10-20T09:56:00Z", gmail_url: "javascript:alert(1)" }));
    expect(within(card()).getByText("Email sent")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("GmailSendCard Edit", () => {
  it("saves the edited email as a new proposal", async () => {
    const revised = makePendingSend({
      id: 32,
      to: [makeRecipient({ address: "ann@example.com", name: "Ann Lee" }), makeRecipient({ address: "bob@example.com", name: null })],
      body: "Hi both,\n\nThe Q3 summary.",
      content_hash: "sha256:77aa01",
    });
    const { calls, rendered } = renderEditable(proposed(), { [`POST ${SEND_PATH}/revise`]: revised });
    await rendered;
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit email" });
    const to = within(dialog).getByRole("textbox", { name: "To" });
    expect(to).toHaveValue("Priya Raman <priya@example.com>");
    expect(within(dialog).getByRole("textbox", { name: "Subject" })).toHaveValue("Q3 summary");
    await user.clear(to);
    await user.type(to, "Ann Lee <ann@example.com>, bob@example.com");
    const text = within(dialog).getByRole("textbox", { name: "Text" });
    await user.clear(text);
    await user.type(text, "Hi both,{Enter}{Enter}The Q3 summary.");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("ann@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((call) => call.path === `${SEND_PATH}/revise`)?.body).toEqual({
      to: ["Ann Lee <ann@example.com>", "bob@example.com"],
      cc: [],
      bcc: [],
      subject: "Q3 summary",
      body: "Hi both,\n\nThe Q3 summary.",
      attachment_document_ids: [],
    });
    expect(screen.getByRole("region", { name: "Email text" })).toHaveTextContent("The Q3 summary.");
  });

  it("asks for a recipient and a text before saving", async () => {
    const { calls, rendered } = renderEditable(proposed());
    await rendered;
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit email" });
    await user.clear(within(dialog).getByRole("textbox", { name: "To" }));
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Add at least one recipient under To.");

    await user.type(within(dialog).getByRole("textbox", { name: "To" }), "bob@example.com");
    await user.clear(within(dialog).getByRole("textbox", { name: "Text" }));
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Write the text of the email.");
    expect(calls.some((call) => call.path.endsWith("/revise"))).toBe(false);
  });

  it("keeps the dialog open with the server's error", async () => {
    const { rendered } = renderEditable(proposed(), {
      [`POST ${SEND_PATH}/revise`]: () => errorResponse(422, [{ loc: ["body", "to", 0], msg: "not a valid email address" }]),
    });
    await rendered;
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit email" });
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("to.0: not a valid email address");
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("keeps a reply's subject", async () => {
    const reply = proposed({
      subject: "Re: Q3 numbers",
      reply_to: {
        message_id: "18f2a9c0d1e2f3a4",
        subject: "Q3 numbers",
        sender: { address: "priya@example.com", name: "Priya Raman", unicode_domain: null },
        date: null,
      },
    });
    const { calls, rendered } = renderEditable(reply, { [`POST ${SEND_PATH}/revise`]: makePendingSend({ id: 32, subject: "Re: Q3 numbers" }) });
    await rendered;
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit email" });
    const subject = within(dialog).getByRole("textbox", { name: "Subject" });
    expect(subject).toHaveAttribute("readonly");
    expect(subject).toHaveAccessibleDescription("A reply keeps the subject of the email it answers.");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await screen.findByRole("group", { name: "Email to send: Re: Q3 numbers" });
    expect(calls.find((call) => call.path.endsWith("/revise"))?.body).toMatchObject({ subject: "" });
  });

  it("attaches only the user's own documents", async () => {
    act(() =>
      publishDocuments([
        makeDocument({ id: 401, filename: "summary.docx", size_bytes: 18_432 }),
        makeDocument({ id: 402, filename: "handbook.pdf", scope: "shared", owner_id: 1, owner_username: "admin" }),
      ]),
    );
    const { calls, rendered } = renderEditable(proposed(), { [`POST ${SEND_PATH}/revise`]: makePendingSend({ id: 32 }) });
    await rendered;
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit email" });
    const picker = within(dialog).getByRole("combobox", { name: "Choose one of my documents to attach" });
    expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Choose one of my documents…",
      expect.stringContaining("summary.docx"),
    ]);
    await user.selectOptions(picker, "401");
    await user.click(within(dialog).getByRole("button", { name: "Attach summary.docx" }));
    expect(within(dialog).getByRole("button", { name: "Remove summary.docx" })).toBeInTheDocument();
    expect(picker).toBeDisabled();

    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await screen.findByRole("group", { name: "Email to send: Q3 summary" });
    expect(calls.find((call) => call.path.endsWith("/revise"))?.body).toMatchObject({ attachment_document_ids: [401] });
  });
});
