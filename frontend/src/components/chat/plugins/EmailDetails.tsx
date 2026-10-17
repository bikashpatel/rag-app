import type { EmailAttachment, EmailReplyInfo } from "../../../api/types";
import { formatBytes } from "../../../lib/format";
import { PaperclipIcon } from "../icons";
import { formatEmailDate } from "./email";

/** "Replying to “<subject>” from <sender address> · <date>": the email a draft or proposed email answers. */
export function ReplyToLine({ reply }: { reply: EmailReplyInfo }) {
  const date = formatEmailDate(reply.date);
  return (
    <p className="chat-mail-reply">
      Replying to “{reply.subject || "(no subject)"}” from{" "}
      <span className="chat-mail-address">{reply.sender.address}</span>
      {date ? ` · ${date}` : null}
    </p>
  );
}

export function EmailAttachmentList({ attachments }: { attachments: EmailAttachment[] }) {
  if (attachments.length === 0) return null;
  return (
    <div className="chat-mail-attachments">
      <span className="chat-mail-label">Attachments</span>
      <ul>
        {attachments.map((attachment, i) => (
          <li key={`${attachment.document_id ?? "x"}-${i}`} className="chat-mail-attachment">
            <PaperclipIcon size={13} />
            <span className="chat-mail-attachment-name" title={attachment.filename}>
              {attachment.filename}
            </span>
            <span className="chat-mail-attachment-size">{formatBytes(attachment.size_bytes)}</span>
            {attachment.document_id === null ? <span className="badge badge-neutral">deleted</span> : null}
            {attachment.unrequested ? <span className="badge badge-warning chat-mail-flag">not named by you</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
