import type { GmailActivityCard } from "../../../api/types";
import { AlertIcon, MailIcon } from "../icons";
import { plural } from "./email";

function runningText(card: GmailActivityCard): string {
  switch (card.action) {
    case "search":
      return card.query ? `Searching Gmail for “${card.query}”` : "Searching Gmail";
    case "read_message":
      return "Reading emails";
    case "read_thread":
      return "Reading a conversation";
  }
}

function doneText(card: GmailActivityCard): string {
  switch (card.action) {
    case "search": {
      const results = card.count === 0 ? "no results" : plural(card.count, "result");
      return card.query ? `Searched Gmail for “${card.query}” · ${results}` : `Searched Gmail · ${results}`;
    }
    case "read_message":
      return `Read ${card.count === 1 ? "an email" : plural(card.count, "email")}`;
    case "read_thread":
      return `Read a conversation (${plural(card.count, "message")})`;
  }
}

function failedText(card: GmailActivityCard): string {
  const what =
    card.action === "search" ? "Gmail search" : card.action === "read_thread" ? "Reading the conversation" : "Reading emails";
  return card.error ? `${what} failed: ${card.error}` : `${what} failed`;
}

function chipText(card: GmailActivityCard, stopped: boolean): string {
  if (card.status === "running") return stopped ? `${runningText(card)}: stopped` : `${runningText(card)}…`;
  return card.status === "failed" ? failedText(card) : doneText(card);
}

/** One line per Gmail search or read: "Searched Gmail for “invoice” · 8 results". */
export default function GmailActivityChip({ card, stopped }: { card: GmailActivityCard; stopped: boolean }) {
  const running = card.status === "running" && !stopped;
  const failed = card.status === "failed";
  return (
    <div className={failed ? "chat-mail-chip chat-mail-chip-failed" : "chat-mail-chip"} aria-live="polite">
      {running ? (
        <span className="spinner chat-mail-chip-spinner" aria-hidden="true" />
      ) : failed ? (
        <AlertIcon size={13} />
      ) : (
        <MailIcon size={13} />
      )}
      <span className="chat-mail-chip-text">{chipText(card, stopped)}</span>
    </div>
  );
}
