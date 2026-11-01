import { useId, useRef, useState } from "react";
import type { AdminOrder, UpiOrderInfo, UpiRejectReason } from "../../api/types";
import { formatInr, formatUtr } from "../../lib/billing";
import Modal from "../ui/Modal";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, AlertIcon } from "../ui/icons";
import { confirmUpi, errorMessage, rejectUpi } from "./adminApi";
import { BuyerEmail, CopyButton, Fact, istDateTime } from "./billingUi";
import { Field } from "./Field";

// The admin's check of a direct-UPI payment (docs/briefs/upi-direct.md 10.5): Confirm and Reject, from Billing → UPI
// and from the order dialog.

const NOTE_MAX = 200;

/** A direct-UPI order with its request (every order the UPI lists show has one). */
export type UpiOrder = AdminOrder & { upi: UpiOrderInfo };

export const REJECT_REASONS: Record<UpiRejectReason, { label: string; short: string }> = {
  not_received: { label: "No payment with this UTR arrived", short: "No payment arrived" },
  amount_differs: { label: "The amount is different", short: "Amount differs" },
  utr_used: { label: "This UTR belongs to another payment", short: "UTR of another payment" },
  other: { label: "Something else", short: "Other" },
};

/** What the customer will read for each reason (the server writes the real message, SPEC Billing, Direct UPI). */
function customerSentence(reason: UpiRejectReason, order: UpiOrder, contact: string | null): string {
  const amount = formatInr(order.amount_paise);
  const utr = formatUtr(order.upi.utr ?? "");
  switch (reason) {
    case "not_received":
      return (
        `We couldn't find a payment of ${amount} with UTR ${utr}. If money left your account, ` +
        (contact ? `write to ${contact} with a screenshot of the payment.` : "write to us from Settings.")
      );
    case "amount_differs":
      return `The payment with UTR ${utr} wasn't for ${amount}. We'll send what you paid back to you by UPI. Start a new payment for the exact amount.`;
    case "utr_used":
      return `UTR ${utr} belongs to another payment. Check the 12-digit number in your UPI app, then start a new payment.`;
    case "other":
      return `We couldn't confirm your payment of ${amount}.`;
  }
}

function planText(order: AdminOrder): string {
  return order.kind === "now"
    ? `${order.plan.name}, 30 days from the confirmation: replaces the current period`
    : `${order.plan.name}, 30 days`;
}

type DialogProps = { order: UpiOrder; onClose: () => void; onDone: (order: AdminOrder) => void };

/** Confirm UPI payment (10.5): the facts to find in the bank app, a required tick, then the payment is applied once. */
function UpiConfirmDialog({ order, onClose, onDone }: DialogProps) {
  const checkId = useId();
  const checkRef = useRef<HTMLInputElement>(null);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const amount = formatInr(order.amount_paise);
  const utr = order.upi.utr ?? "";

  async function confirm() {
    if (busy) return;
    if (!checked) {
      setError("Tick the box once you've found this payment.");
      checkRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onDone(await confirmUpi(order.public_id, { utr }));
    } catch (err) {
      setError(errorMessage(err, "Couldn't confirm the payment. Please try again."));
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title="Confirm UPI payment"
      onClose={onClose}
      width={520}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            aria-disabled={!checked || busy || undefined}
            aria-busy={busy || undefined}
            onClick={() => void confirm()}
          >
            {busy ? <ButtonSpinner size={14} /> : null}
            Confirm and start the plan
          </button>
        </>
      }
    >
      <p className="admin-upi-lead">Confirm only after you've found this payment in your bank or UPI app.</p>
      <dl className="admin-billing-facts admin-upi-facts">
        <Fact term="Amount">
          <span className="admin-upi-amount">{amount}</span>
        </Fact>
        <Fact term="UTR">
          <span className="admin-copy-row">
            <code className="admin-upi-utr">{formatUtr(utr)}</code>
            <CopyButton value={utr} label="Copy the UTR" />
          </span>
        </Fact>
        <Fact term="Reference">
          <code className="admin-code">{order.upi.reference}</code>
          <span className="admin-fact-note">If your app shows the payment's note, it should be this reference.</span>
        </Fact>
        <Fact term="Paid to">
          <code className="admin-code">{order.upi.upi_id}</code>
        </Fact>
        <Fact term="From">
          {order.user.username}
          {order.user.id === null ? <span className="admin-subtext"> (deleted)</span> : null}
          <BuyerEmail email={order.user.email} />
        </Fact>
        <Fact term="Plan">{planText(order)}</Fact>
        {order.upi.submitted_at ? <Fact term="Sent">{istDateTime(order.upi.submitted_at)}</Fact> : null}
      </dl>
      <label className="admin-inline-check admin-upi-tick" htmlFor={checkId}>
        <input
          ref={checkRef}
          id={checkId}
          type="checkbox"
          className="ui-check"
          required
          checked={checked}
          onChange={(e) => {
            setChecked(e.target.checked);
            setError(null);
          }}
        />
        <span>
          I found a credit of exactly {amount} with UTR {formatUtr(utr)}.
        </span>
      </label>
      {error ? (
        <p className="error-text admin-upi-error" role="alert">
          <AlertCircleIcon size={13} />
          {error}
        </p>
      ) : null}
    </Modal>
  );
}

/** Reject UPI payment (10.5): a reason the customer reads, with a note (required for "Something else"). */
function UpiRejectDialog({ order, contact, onClose, onDone }: DialogProps & { contact: string | null }) {
  const name = useId();
  const [reason, setReason] = useState<UpiRejectReason>("not_received");
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  async function reject() {
    if (busy) return;
    const text = note.trim();
    if (reason === "other" && !text) {
      setNoteError("Tell the customer what went wrong.");
      noteRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onDone(await rejectUpi(order.public_id, { reason, note: text || null }));
    } catch (err) {
      setError(errorMessage(err, "Couldn't reject the payment. Please try again."));
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title="Reject UPI payment"
      onClose={onClose}
      width={520}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={busy}
            aria-busy={busy || undefined}
            onClick={() => void reject()}
          >
            {busy ? <ButtonSpinner size={14} /> : null}
            Reject payment
          </button>
        </>
      }
    >
      <div className="admin-form">
        <p className="admin-upi-lead">The customer sees why and can start a new payment.</p>
        <fieldset className="admin-upi-reasons">
          <legend className="field-label">Why</legend>
          {(Object.keys(REJECT_REASONS) as UpiRejectReason[]).map((value) => (
            <label key={value} className="admin-upi-reason">
              <input
                type="radio"
                className="ui-radio"
                name={name}
                value={value}
                checked={reason === value}
                onChange={() => {
                  setReason(value);
                  setNoteError(undefined);
                }}
              />
              <span>
                {REJECT_REASONS[value].label}
                <small>{customerSentence(value, order, contact)}</small>
              </span>
            </label>
          ))}
        </fieldset>
        {reason === "amount_differs" ? (
          <div className="ui-notice ui-notice-warning">
            <AlertIcon size={15} />
            <span className="ui-notice-text">Send what they paid back to the payer by UPI from your bank app.</span>
          </div>
        ) : null}
        <Field
          label={reason === "other" ? "Note to the customer" : "Note to the customer (optional)"}
          hint="The customer reads this. Don't include anyone's bank details."
          error={noteError}
        >
          {(control) => (
            <textarea
              {...control}
              ref={noteRef}
              className="input admin-textarea"
              rows={3}
              maxLength={NOTE_MAX}
              value={note}
              onChange={(e) => {
                setNote(e.target.value);
                setNoteError(undefined);
              }}
            />
          )}
        </Field>
        {error ? (
          <p className="error-text admin-upi-error" role="alert">
            <AlertCircleIcon size={13} />
            {error}
          </p>
        ) : null}
      </div>
    </Modal>
  );
}

export type UpiDecision = { order: UpiOrder; action: "confirm" | "reject" };

/** The flash after a decision: confirmed (or recorded for review) or rejected. */
function decisionMessage(order: AdminOrder, action: UpiDecision["action"]): string {
  const what = `${formatInr(order.amount_paise)} from ${order.user.username}`;
  if (action === "reject") return `Rejected ${what}. They can start a new payment.`;
  if (order.status === "review")
    return `Recorded ${what}. It needs your review${order.review_reason ? `: ${order.review_reason}` : "."}`;
  return `Confirmed ${what}. Their plan has started.`;
}

type DecisionDialogProps = {
  decision: UpiDecision;
  contact: string | null;
  onClose: () => void;
  onDone: (order: AdminOrder, message: string) => void;
};

/** The confirm or reject dialog of one order. */
export function UpiDecisionDialog({ decision, contact, onClose, onDone }: DecisionDialogProps) {
  const done = (order: AdminOrder) => onDone(order, decisionMessage(order, decision.action));
  return decision.action === "confirm" ? (
    <UpiConfirmDialog order={decision.order} onClose={onClose} onDone={done} />
  ) : (
    <UpiRejectDialog order={decision.order} contact={contact} onClose={onClose} onDone={done} />
  );
}

/** Whether a decision can be taken on an order: a direct-UPI order whose UTR waits. */
export function decidable(order: AdminOrder): order is UpiOrder {
  return order.upi !== null && order.status === "submitted" && order.upi.utr !== null;
}
