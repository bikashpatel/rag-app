import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError, revisePendingSend } from "../../../api/client";
import type { PendingSend, PendingSendRevision } from "../../../api/types";
import { useAuth } from "../../../auth/AuthContext";
import { formatBytes } from "../../../lib/format";
import { useDocumentList } from "../../documents/documentEvents";
import Modal from "../../ui/Modal";
import Spinner from "../../ui/Spinner";
import { CloseIcon, PaperclipIcon } from "../icons";
import { formatAddressForInput, splitAddressList } from "./email";
import { ReplyToLine } from "./EmailDetails";

/** PendingSendRevision limits (SPEC): recipients per field, attachments, body length. */
const MAX_RECIPIENTS = 20;
const MAX_ATTACHMENTS = 5;
const MAX_BODY_CHARS = 50_000;
const MAX_SUBJECT_CHARS = 250;

type PickedAttachment = { document_id: number; filename: string; size_bytes: number };

type EditEmailModalProps = {
  pending: PendingSend;
  onClose: () => void;
  /** The new proposal that replaces this one (it still has to be sent with Send). */
  onRevised: (next: PendingSend) => void;
};

function joinRecipients(list: PendingSend["to"]): string {
  return list.map(formatAddressForInput).join(", ");
}

/** Edit a proposed email: saving creates a new proposal (POST …/revise), which the card then shows for Send. */
export default function EditEmailModal({ pending, onClose, onRevised }: EditEmailModalProps) {
  const formId = useId();
  const fieldId = useId();
  const { user } = useAuth();
  const documents = useDocumentList();
  const [to, setTo] = useState(() => joinRecipients(pending.to));
  const [cc, setCc] = useState(() => joinRecipients(pending.cc));
  const [bcc, setBcc] = useState(() => joinRecipients(pending.bcc));
  const [subject, setSubject] = useState(pending.subject);
  const [body, setBody] = useState(pending.body);
  const [attachments, setAttachments] = useState<PickedAttachment[]>(() =>
    pending.attachments.flatMap(({ document_id, filename, size_bytes }) =>
      document_id === null ? [] : [{ document_id, filename, size_bytes }],
    ),
  );
  const [pick, setPick] = useState("");
  const [attachNote, setAttachNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pickerRef = useRef<HTMLSelectElement>(null);
  const chipsRef = useRef<HTMLUListElement>(null);
  /** Where focus goes once the attachments changed: back to the picker, or the Remove button at this index. */
  const focusAfter = useRef<"picker" | number | null>(null);
  const isReply = pending.reply_to !== null;
  const deletedAttachments = pending.attachments.filter((attachment) => attachment.document_id === null);

  // Only the user's own documents can be attached (the panel's list also has the shared knowledge base).
  const addable = useMemo(
    () =>
      (documents ?? [])
        .filter((doc) => doc.owner_id === user?.id && !attachments.some((item) => item.document_id === doc.id))
        .sort((a, b) => a.filename.localeCompare(b.filename)),
    [documents, user?.id, attachments],
  );

  // Choosing (arrow keys and type-ahead change a closed select's value at once) and attaching are separate steps.
  const pickDoc = addable.find((doc) => String(doc.id) === pick) ?? null;
  const atLimit = attachments.length >= MAX_ATTACHMENTS;

  // The Attach or Remove button that was used is disabled or gone now: keep focus in the attachments.
  useEffect(() => {
    const target = focusAfter.current;
    if (target === null) return;
    focusAfter.current = null;
    const picker = pickerRef.current;
    const removes = chipsRef.current?.querySelectorAll<HTMLButtonElement>(".chat-mail-chip-remove") ?? [];
    const index = target === "picker" ? removes.length - 1 : Math.min(target, removes.length - 1);
    const chip = index >= 0 ? removes[index] : null;
    if (target === "picker" && picker && !picker.disabled) picker.focus();
    else if (chip) chip.focus();
    else if (picker && !picker.disabled) picker.focus();
  }, [attachments]);

  function attachPicked() {
    if (!pickDoc || atLimit) return;
    focusAfter.current = "picker";
    setAttachments((list) => [
      ...list,
      { document_id: pickDoc.id, filename: pickDoc.filename, size_bytes: pickDoc.size_bytes },
    ]);
    setPick("");
    setAttachNote(`Attached ${pickDoc.filename}.`);
  }

  function removeAttachment(index: number) {
    const item = attachments[index];
    if (!item) return;
    focusAfter.current = index;
    setAttachments((list) => list.filter((other) => other.document_id !== item.document_id));
    setAttachNote(`Removed ${item.filename}.`);
  }

  function validate(revision: PendingSendRevision): string | null {
    if (revision.to.length === 0) return "Add at least one recipient under To.";
    for (const [label, list] of [
      ["To", revision.to],
      ["Cc", revision.cc],
      ["Bcc", revision.bcc],
    ] as const) {
      if (list.length > MAX_RECIPIENTS) return `${label} can have at most ${MAX_RECIPIENTS} addresses.`;
    }
    if (!revision.body.trim()) return "Write the text of the email.";
    return null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const revision: PendingSendRevision = {
      to: splitAddressList(to),
      cc: splitAddressList(cc),
      bcc: splitAddressList(bcc),
      // A reply keeps the original's subject; the server ignores this one.
      subject: isReply ? "" : subject.trim(),
      body,
      attachment_document_ids: attachments.map((item) => item.document_id),
    };
    const problem = validate(revision);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      onRevised(await revisePendingSend(pending.id, revision));
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the changes. Try again.");
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      title="Edit email"
      onClose={saving ? () => undefined : onClose}
      width={620}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" form={formId} className="btn btn-primary" disabled={saving}>
            {saving ? <Spinner size={14} label="Saving" /> : null}
            Save changes
          </button>
        </>
      }
    >
      <form id={formId} className="stack chat-mail-form" onSubmit={(event) => void submit(event)} noValidate>
        <p className="chat-modal-text">
          Saving prepares a new version for you to review. Nothing is sent until you click Send on the card.
        </p>
        {pending.reply_to ? <ReplyToLine reply={pending.reply_to} /> : null}
        <p className="field-hint">From {pending.account_email}</p>
        <AddressField
          id={`${fieldId}-to`}
          label="To"
          value={to}
          onChange={setTo}
          disabled={saving}
          placeholder="Ann Lee <ann@example.com>, bob@example.com"
        />
        <AddressField id={`${fieldId}-cc`} label="Cc" value={cc} onChange={setCc} disabled={saving} />
        <AddressField
          id={`${fieldId}-bcc`}
          label="Bcc (hidden from the other recipients)"
          value={bcc}
          onChange={setBcc}
          disabled={saving}
        />
        <div className="field">
          <label className="field-label" htmlFor={`${fieldId}-subject`}>
            Subject
          </label>
          <input
            id={`${fieldId}-subject`}
            className="input"
            value={isReply ? pending.subject : subject}
            onChange={(event) => setSubject(event.target.value)}
            readOnly={isReply}
            maxLength={isReply ? undefined : MAX_SUBJECT_CHARS}
            aria-describedby={isReply ? `${fieldId}-subject-hint` : undefined}
            disabled={saving}
          />
          {isReply ? (
            <span id={`${fieldId}-subject-hint`} className="field-hint">
              A reply keeps the subject of the email it answers.
            </span>
          ) : null}
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${fieldId}-body`}>
            Text
          </label>
          <textarea
            id={`${fieldId}-body`}
            className="textarea chat-mail-textarea"
            rows={10}
            value={body}
            maxLength={MAX_BODY_CHARS}
            onChange={(event) => setBody(event.target.value)}
            disabled={saving}
          />
        </div>
        <fieldset className="chat-mail-fieldset">
          <legend className="field-label">Attachments</legend>
          {attachments.length > 0 ? (
            <ul ref={chipsRef} className="chat-mail-chips">
              {attachments.map((item, index) => (
                <li key={item.document_id} className="chat-mail-chip-item">
                  <PaperclipIcon size={13} />
                  <span className="chat-mail-attachment-name" title={item.filename}>
                    {item.filename}
                  </span>
                  <span className="chat-mail-attachment-size">{formatBytes(item.size_bytes)}</span>
                  <button
                    type="button"
                    className="btn btn-ghost btn-icon chat-mail-chip-remove"
                    aria-label={`Remove ${item.filename}`}
                    disabled={saving}
                    onClick={() => removeAttachment(index)}
                  >
                    <CloseIcon size={12} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <span className="field-hint">No attachments.</span>
          )}
          {deletedAttachments.length > 0 ? (
            <span className="field-hint">
              Left out because the document was deleted: {deletedAttachments.map((item) => item.filename).join(", ")}
            </span>
          ) : null}
          <label className="sr-only" htmlFor={`${fieldId}-add`}>
            Choose one of my documents to attach
          </label>
          <div className="row chat-mail-add">
            <select
              ref={pickerRef}
              id={`${fieldId}-add`}
              className="select"
              value={pickDoc ? pick : ""}
              disabled={saving || atLimit || addable.length === 0}
              onChange={(event) => setPick(event.target.value)}
            >
              <option value="">
                {documents === null
                  ? "Your documents are still loading…"
                  : atLimit
                    ? `At most ${MAX_ATTACHMENTS} attachments`
                    : addable.length === 0
                      ? "No other documents of yours"
                      : "Choose one of my documents…"}
              </option>
              {addable.map((doc) => (
                <option key={doc.id} value={doc.id}>
                  {doc.filename} ({formatBytes(doc.size_bytes)})
                </option>
              ))}
            </select>
            {/* type="button": inside the form, a plain button would submit it (Save changes). */}
            <button
              type="button"
              className="btn"
              disabled={saving || atLimit || !pickDoc}
              aria-label={pickDoc ? `Attach ${pickDoc.filename}` : undefined}
              onClick={attachPicked}
            >
              Attach
            </button>
          </div>
          <span className="sr-only" aria-live="polite">
            {attachNote}
          </span>
        </fieldset>
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}

type AddressFieldProps = {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  placeholder?: string;
};

function AddressField({ id, label, value, onChange, disabled, placeholder }: AddressFieldProps) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
      />
    </div>
  );
}
