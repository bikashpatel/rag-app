import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ApiError, cancelPendingSend, confirmPendingSend, getPendingSend } from "../../../api/client";
import type { GmailSendCard as SendCard, PendingSend, PendingSendStatus } from "../../../api/types";
import { formatDateTime } from "../../../lib/format";
import Spinner from "../../ui/Spinner";
import { AlertIcon, CheckIcon, ClockIcon, ExternalLinkIcon, MailIcon } from "../icons";
import EditEmailModal from "./EditEmailModal";
import { formatCountdown, safeHttpsUrl } from "./email";
import { EmailAttachmentList, ReplyToLine } from "./EmailDetails";
import EmailRecipientList from "./EmailRecipientList";

/** A row claimed for sending settles within about two minutes (or reads as failed after five): look again meanwhile. */
const SENDING_POLL_MS = 4000;

type Action = "send" | "cancel" | "newer";

/** The head's title and status line (visual spec 6.7, 6.19): a word with a dot, never colour alone. */
const STATUS_VIEW: Record<PendingSendStatus, { title: string; badge: string; tone: string }> = {
  pending: { title: "Email ready to send", badge: "Not sent", tone: "ui-status-attention" },
  sending: { title: "Sending…", badge: "Sending", tone: "ui-status-neutral" },
  sent: { title: "Email sent", badge: "Sent", tone: "ui-status-connected" },
  failed: { title: "Email not sent", badge: "Failed", tone: "chat-mail-status-failed" },
  cancelled: { title: "Cancelled. Nothing was sent.", badge: "Cancelled", tone: "ui-status-neutral" },
  expired: { title: "Expired. Nothing was sent.", badge: "Expired", tone: "ui-status-neutral" },
  replaced: { title: "Replaced by a newer version", badge: "Replaced", tone: "ui-status-neutral" },
};

/** The current time, ticking every second until `until` (the countdown of a sendable email); null: no ticking. */
function useNow(until: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (until === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => {
      const time = Date.now();
      setNow(time);
      if (time >= until) window.clearInterval(timer);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [until]);
  return now;
}

/**
 * An email the assistant proposed (brief 13, P12). The card shows exactly what Send sends; only the click sends it
 * (once, with the content hash of what is shown). Edit makes a new proposal; Cancel withdraws it.
 */
export default function GmailSendCard({ card, stopped }: { card: SendCard; stopped: boolean }) {
  // The latest state after an action here (send, cancel, edit, reload); the card's own until then.
  const [latest, setLatest] = useState<PendingSend | null>(null);
  const pending = latest ?? card.pending;
  if (!pending) return <ProposalStatus card={card} stopped={stopped} />;
  return <PendingSendView pending={pending} onChange={setLatest} />;
}

function ProposalStatus({ card, stopped }: { card: SendCard; stopped: boolean }) {
  const failed = card.status === "failed";
  const busy = card.status === "writing" && !stopped;
  let lead: ReactNode = <MailIcon size={16} />;
  if (busy) lead = <span className="spinner chat-mail-spinner" aria-hidden="true" />;
  else if (failed) lead = <AlertIcon size={16} />;
  const title = failed
    ? "Couldn't prepare the email"
    : stopped
      ? "Stopped before the email was prepared. Nothing was sent."
      : "Preparing an email for you to review…";
  return (
    <div
      className={`chat-mail-card${failed ? " chat-mail-card-failed" : stopped ? " chat-mail-card-muted" : ""}`}
      role="group"
      aria-label="Email to send"
    >
      <div className="chat-mail-head">
        <span className="chat-mail-lead">{lead}</span>
        <span className="chat-mail-title" aria-live="polite">
          {title}
        </span>
      </div>
      {failed && card.error ? <p className="chat-mail-error">{card.error}</p> : null}
    </div>
  );
}

function PendingSendView({ pending, onChange }: { pending: PendingSend; onChange: (next: PendingSend) => void }) {
  const [action, setAction] = useState<Action | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const expiresAt = Date.parse(pending.expires_at);
  const now = useNow(pending.status === "pending" ? expiresAt : null);
  const msLeft = expiresAt - now;
  // Expiry is checked by the server on Send; until the card reloads, a pending email past its time reads as expired.
  const status: PendingSendStatus = pending.status === "pending" && msLeft <= 0 ? "expired" : pending.status;
  const view = STATUS_VIEW[status];
  const { id } = pending;

  const reload = useCallback(async () => {
    try {
      onChange(await getPendingSend(id));
    } catch {
      /* keep what is shown; the message above says what went wrong */
    }
  }, [id, onChange]);

  useEffect(() => {
    if (status !== "sending" || action) return;
    const timer = window.setInterval(() => void reload(), SENDING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [status, action, reload]);

  async function run(kind: Action, call: () => Promise<PendingSend>, fallback: string) {
    setAction(kind);
    setProblem(null);
    try {
      onChange(await call());
    } catch (err) {
      // Without an answer from the server (status 0) the outcome is unknown: say so in this action's words.
      setProblem(err instanceof ApiError && err.status !== 0 ? err.message : fallback);
      // 409/403 (expired, changed, already sent, turned off) or a lost connection: show the server's current state.
      if (!(err instanceof ApiError) || err.status !== 429) await reload();
    } finally {
      setAction(null);
    }
  }

  const send = () =>
    void run(
      "send",
      () => confirmPendingSend(pending.id, pending.content_hash),
      "Couldn't reach the server, so the email may or may not have been sent. Check your Sent folder.",
    );
  const cancel = () => void run("cancel", () => cancelPendingSend(pending.id), "Couldn't cancel the email. Try again.");
  const showNewer = (newer: number) => void run("newer", () => getPendingSend(newer), "Couldn't load the newer version.");

  const sentLink = status === "sent" ? safeHttpsUrl(pending.gmail_url) : null;
  const canEdit = status === "pending" || status === "failed" || status === "expired" || status === "cancelled";
  const inactive = status === "cancelled" || status === "expired" || status === "replaced";
  let lead: ReactNode = <MailIcon size={16} />;
  if (status === "sending" || action === "send") lead = <span className="spinner chat-mail-spinner" aria-hidden="true" />;
  else if (status === "sent") lead = <CheckIcon size={16} />;
  else if (status === "failed") lead = <AlertIcon size={16} />;

  return (
    <div
      className={`chat-mail-card chat-mail-card-send${inactive ? " chat-mail-card-muted" : ""}`}
      role="group"
      aria-label={`Email to send: ${pending.subject || "no subject"}`}
    >
      <div className="chat-mail-head">
        <span className="chat-mail-lead">{lead}</span>
        <span className="chat-mail-title" aria-live="polite">
          {action === "send" ? "Sending…" : view.title}
        </span>
        {status === "pending" ? (
          <span className="chat-mail-countdown">
            <ClockIcon size={14} />
            {formatCountdown(msLeft)} left to send
          </span>
        ) : null}
        <span className={`ui-status chat-mail-status ${view.tone}`}>{view.badge}</span>
      </div>
      {status === "pending" ? (
        <p className="chat-mail-note">Review the whole email. Nothing is sent until you click Send.</p>
      ) : null}
      {status === "sent" && pending.sent_at ? (
        <p className="chat-mail-note">Sent {formatDateTime(pending.sent_at)}</p>
      ) : null}
      {status === "expired" ? (
        <p className="chat-mail-note">This email can no longer be sent. Ask the assistant again or use Edit.</p>
      ) : null}
      {pending.reply_to ? <ReplyToLine reply={pending.reply_to} /> : null}
      <EmailRecipientList from={pending.account_email} to={pending.to} cc={pending.cc} bcc={pending.bcc} />
      <div className="chat-mail-subject">
        <span className="chat-mail-label">Subject</span>
        <span className="chat-mail-subject-text">{pending.subject || "(no subject)"}</span>
      </div>
      <div className="chat-mail-body" tabIndex={0} role="region" aria-label="Email text">
        {pending.body}
      </div>
      <EmailAttachmentList attachments={pending.attachments} />
      {status === "failed" && pending.error ? <p className="chat-mail-error">{pending.error}</p> : null}
      {problem ? (
        <p className="chat-mail-error" role="alert">
          {problem}
        </p>
      ) : null}
      {status === "pending" || canEdit || sentLink || (status === "replaced" && pending.replaced_by !== null) ? (
        <div className="chat-mail-actions">
          {status === "pending" ? (
            <button type="button" className="btn btn-primary btn-sm" onClick={send} disabled={action !== null}>
              {action === "send" ? <Spinner size={12} label="Sending" /> : null}
              Send
            </button>
          ) : null}
          {canEdit ? (
            <button type="button" className="btn btn-sm" onClick={() => setEditing(true)} disabled={action !== null}>
              Edit
            </button>
          ) : null}
          {status === "pending" ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={cancel} disabled={action !== null}>
              {action === "cancel" ? <Spinner size={12} label="Cancelling" /> : null}
              Cancel
            </button>
          ) : null}
          {status === "replaced" && pending.replaced_by !== null ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => showNewer(pending.replaced_by as number)}
              disabled={action !== null}
            >
              Show the newer version
            </button>
          ) : null}
          {sentLink ? (
            <a className="btn btn-sm chat-mail-open" href={sentLink} target="_blank" rel="noopener noreferrer">
              Open in Gmail
              <ExternalLinkIcon size={13} />
            </a>
          ) : null}
        </div>
      ) : null}
      {editing ? (
        <EditEmailModal
          pending={pending}
          onClose={() => setEditing(false)}
          onRevised={(next) => {
            setProblem(null);
            onChange(next);
          }}
        />
      ) : null}
    </div>
  );
}
