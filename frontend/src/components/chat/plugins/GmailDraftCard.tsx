import type { ReactNode } from "react";
import type { GmailDraftCard as DraftCard } from "../../../api/types";
import { AlertIcon, ExternalLinkIcon, MailIcon } from "../icons";
import { safeHttpsUrl } from "./email";
import { EmailAttachmentList, ReplyToLine } from "./EmailDetails";
import EmailRecipientList from "./EmailRecipientList";

/** The body preview is the first 500 characters of the draft (SPEC). */
const PREVIEW_MAX_CHARS = 500;

function headline(card: DraftCard, stopped: boolean): string {
  if (stopped) {
    return card.status === "writing"
      ? "Stopped before the draft was created."
      : "Stopped; it may have finished. Check your Gmail drafts.";
  }
  switch (card.status) {
    case "writing":
      return "Writing a draft…";
    case "creating":
      return "Saving the draft in Gmail…";
    case "created":
      return "Draft saved in Gmail";
    case "failed":
      return "Couldn't create the draft";
  }
}

/** A Gmail draft the assistant created (or is creating): recipients, subject, preview and "Open in Gmail". */
export default function GmailDraftCard({ card, stopped }: { card: DraftCard; stopped: boolean }) {
  const busy = (card.status === "writing" || card.status === "creating") && !stopped;
  const failed = card.status === "failed";
  const link = card.status === "created" ? safeHttpsUrl(card.gmail_url) : null;
  const hasDetails = card.to.length > 0 || card.subject !== null || card.preview !== "";
  const tone = failed ? " chat-mail-card-failed" : stopped ? " chat-mail-card-muted" : "";
  let lead: ReactNode = <MailIcon size={16} />;
  if (busy) lead = <span className="spinner chat-mail-spinner" aria-hidden="true" />;
  else if (failed) lead = <AlertIcon size={16} />;

  return (
    <div className={`chat-mail-card${tone}`} role="group" aria-label="Gmail draft">
      <div className="chat-mail-head">
        <span className="chat-mail-lead">{lead}</span>
        <span className="chat-mail-title" aria-live="polite">
          {headline(card, stopped)}
        </span>
        {link ? (
          <a className="btn btn-sm chat-mail-open" href={link} target="_blank" rel="noopener noreferrer">
            Open in Gmail
            <ExternalLinkIcon size={13} />
          </a>
        ) : null}
      </div>
      {card.reply_to ? <ReplyToLine reply={card.reply_to} /> : null}
      {hasDetails ? (
        <>
          <EmailRecipientList from={card.account_email} to={card.to} cc={card.cc} bcc={card.bcc} />
          <div className="chat-mail-subject">
            <span className="chat-mail-label">Subject</span>
            <span className="chat-mail-subject-text">{card.subject || "(no subject)"}</span>
          </div>
          {card.preview ? (
            <p className="chat-mail-preview">
              {card.preview}
              {Array.from(card.preview).length >= PREVIEW_MAX_CHARS ? "…" : null}
            </p>
          ) : null}
          <EmailAttachmentList attachments={card.attachments} />
        </>
      ) : null}
      {failed && card.error ? <p className="chat-mail-error">{card.error}</p> : null}
      {card.status === "created" ? (
        <p className="chat-mail-note">Nothing was sent. Later changes to the draft in Gmail aren't shown here.</p>
      ) : null}
    </div>
  );
}
