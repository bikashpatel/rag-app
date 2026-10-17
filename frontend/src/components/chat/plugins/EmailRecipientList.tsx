import type { EmailRecipient } from "../../../api/types";
import { AlertIcon } from "../icons";
import { unicodeAddress } from "./email";

type EmailRecipientListProps = {
  /** The sending account (From); omitted while unknown. */
  from?: string | null;
  to: EmailRecipient[];
  cc: EmailRecipient[];
  bcc: EmailRecipient[];
};

/**
 * Who an email goes to, for draft and send cards (brief 15.4, P24). Each address is shown on its own, with the
 * untrusted display name after it in muted text; an internationalized domain is shown in both forms with a warning;
 * recipients the user never typed are flagged; Bcc is always highlighted. Everything is rendered as text.
 */
export default function EmailRecipientList({ from, to, cc, bcc }: EmailRecipientListProps) {
  return (
    <dl className="chat-mail-recipients">
      {from ? (
        <div className="chat-mail-row">
          <dt>From</dt>
          <dd>
            <span className="chat-mail-address">{from}</span>
          </dd>
        </div>
      ) : null}
      <RecipientRow label="To" recipients={to} />
      {cc.length > 0 ? <RecipientRow label="Cc" recipients={cc} /> : null}
      {bcc.length > 0 ? <RecipientRow label="Bcc (hidden from the other recipients)" recipients={bcc} bcc /> : null}
    </dl>
  );
}

function RecipientRow({ label, recipients, bcc = false }: { label: string; recipients: EmailRecipient[]; bcc?: boolean }) {
  return (
    <div className={bcc ? "chat-mail-row chat-mail-row-bcc" : "chat-mail-row"}>
      <dt>
        {bcc ? <AlertIcon size={13} /> : null}
        {label}
      </dt>
      <dd>
        {recipients.length === 0 ? (
          <span className="chat-mail-none">No recipients</span>
        ) : (
          <ul className="chat-mail-recipient-list">
            {recipients.map((recipient, i) => (
              <Recipient key={`${recipient.address}-${i}`} recipient={recipient} />
            ))}
          </ul>
        )}
      </dd>
    </div>
  );
}

function Recipient({ recipient }: { recipient: EmailRecipient }) {
  const unicode = unicodeAddress(recipient);
  return (
    <li className="chat-mail-recipient">
      <span className="chat-mail-address">{recipient.address}</span>
      {recipient.name ? <span className="chat-mail-name">{recipient.name}</span> : null}
      {recipient.unmentioned ? <span className="badge badge-warning chat-mail-flag">not mentioned by you</span> : null}
      {unicode ? (
        <span className="chat-mail-idn">
          <AlertIcon size={12} />
          <span>
            <span className="chat-mail-address">{unicode}</span> Internationalized domain: check the spelling
          </span>
        </span>
      ) : null}
    </li>
  );
}
