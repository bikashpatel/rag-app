import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import type { AdminPayment, AdminUser, DisputeStatus, Page, PaymentStatus, RefundView } from "../../api/types";
import { formatInr, formatIstDate, formatIstRange } from "../../lib/billing";
import Modal from "../ui/Modal";
import SegmentedControl from "../ui/SegmentedControl";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, AlertIcon, BankIcon, DownloadIcon, InfoIcon } from "../ui/icons";
import { adminReceiptUrl, errorMessage, getPayment, PAGE_SIZE, paymentsPath, refundPayment } from "./adminApi";
import {
  CopyButton,
  Fact,
  istDateTime,
  kindLabel,
  methodLabel,
  Pagination,
  PaymentStatusBadge,
  shortIstDate,
  TestBadge,
  UserCell,
} from "./billingUi";
import { Field } from "./Field";
import FlashMessage from "./FlashMessage";
import { FormValidator } from "./form";
import { useFlash, useResource } from "./hooks";
import { EmptyState, ResourceGate } from "./StateViews";

/** A receipt number that downloads the PDF, or a dash. */
function ReceiptLink({ payment }: { payment: AdminPayment }) {
  if (!payment.receipt) return <span className="admin-subtext">—</span>;
  return (
    <a className="admin-receipt-link" href={adminReceiptUrl(payment.receipt.id)} download>
      {payment.receipt.number}
    </a>
  );
}

type PaymentsTableProps = { payments: AdminPayment[]; onOpen: (payment: AdminPayment) => void };

/** Payments as a table: date, user, plan, kind, amount, method, status, receipt and Open. */
export function PaymentsTable({ payments, onOpen }: PaymentsTableProps) {
  return (
    <div className="table-wrap">
      <table className="table admin-table">
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">User</th>
            <th scope="col">Plan</th>
            <th scope="col">Kind</th>
            <th scope="col" className="num">
              Amount
            </th>
            <th scope="col">Method</th>
            <th scope="col">Status</th>
            <th scope="col">Receipt</th>
            <th scope="col" className="admin-actions-cell">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {payments.map((payment) => (
            <tr key={payment.id}>
              <td className="admin-nowrap">
                <time dateTime={payment.paid_at}>{shortIstDate(payment.paid_at)}</time>
              </td>
              <td>
                <UserCell user={payment.user} />
              </td>
              <td>{payment.plan?.name ?? "—"}</td>
              <td>{kindLabel(payment.kind)}</td>
              <td className="num admin-nowrap">
                {formatInr(payment.amount_paise)}
                {payment.test_mode ? (
                  <>
                    {" "}
                    <TestBadge />
                  </>
                ) : null}
              </td>
              <td className="admin-nowrap">{methodLabel(payment.method)}</td>
              <td>
                <PaymentStatusBadge payment={payment} />
              </td>
              <td className="admin-nowrap">
                <ReceiptLink payment={payment} />
              </td>
              <td className="admin-actions-cell">
                <span className="admin-actions">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    aria-label={`Open the payment of ${formatInr(payment.amount_paise)} by ${payment.user.username} on ${shortIstDate(payment.paid_at)}`}
                    onClick={() => onOpen(payment)}
                  >
                    Open
                  </button>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type StatusFilter = "all" | PaymentStatus;
const STATUS_FILTERS = [
  { value: "all", label: "All" },
  { value: "captured", label: "Paid" },
  { value: "partially_refunded", label: "Partly refunded" },
  { value: "refunded", label: "Refunded" },
  { value: "disputed", label: "Disputed" },
] as const satisfies readonly { value: StatusFilter; label: string }[];

type BillingPaymentsProps = {
  month: string;
  /** From the hash: a user to filter by (every month), or a payment to open. */
  userParam: number | null;
  paymentParam: number | null;
  onParamsRead: () => void;
  onRecord: () => void;
  /** Bumped by the parent after a bank transfer is recorded. */
  version: number;
};

export default function BillingPayments({
  month,
  userParam,
  paymentParam,
  onParamsRead,
  onRecord,
  version,
}: BillingPaymentsProps) {
  const [userId, setUserId] = useState<number | null>(userParam);
  const [allMonths, setAllMonths] = useState(userParam !== null);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<AdminPayment | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const flash = useFlash();
  const allMonthsId = useId();
  const users = useResource<AdminUser[]>("/admin/users", "Couldn't load the users.");
  const path = paymentsPath({
    month: allMonths ? null : month,
    userId,
    status: status === "all" ? null : status,
    page,
  });
  const payments = useResource<Page<AdminPayment>>(path, "Couldn't load the payments.");
  const { reload } = payments;
  const firstVersion = useRef(version);

  useEffect(() => {
    if (version !== firstVersion.current) void reload();
  }, [version, reload]);

  useEffect(() => {
    if (userParam !== null) {
      setUserId(userParam);
      setAllMonths(true);
      setPage(1);
    }
    if (paymentParam === null) {
      if (userParam !== null) onParamsRead();
      return;
    }
    let cancelled = false;
    setLookupError(null);
    getPayment(paymentParam)
      .then((found) => !cancelled && setOpen(found))
      .catch((err: unknown) => !cancelled && setLookupError(errorMessage(err, "Couldn't open that payment.")))
      .finally(() => !cancelled && onParamsRead());
    return () => {
      cancelled = true;
    };
  }, [userParam, paymentParam, onParamsRead]);

  const members = useMemo(
    () => (users.data ?? []).filter((user) => !user.is_admin).sort((a, b) => a.username.localeCompare(b.username)),
    [users.data],
  );

  function changed(updated: AdminPayment) {
    setOpen(updated);
    payments.setData((data) => data && { ...data, items: data.items.map((p) => (p.id === updated.id ? updated : p)) });
  }

  return (
    <section className="card admin-card" aria-labelledby="admin-payments-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-payments-title" className="admin-card-title">
            Payments
          </h2>
          <p className="admin-card-subtitle">
            Every payment with its refunds, disputes and receipt. Months are in Indian time (IST).
          </p>
        </div>
        <span className="spacer" />
        <button type="button" className="btn" onClick={onRecord}>
          <BankIcon size={16} />
          Record bank transfer
        </button>
      </div>
      <div className="admin-toolbar">
        <label className="sr-only" htmlFor={`${allMonthsId}-user`}>
          User
        </label>
        <select
          id={`${allMonthsId}-user`}
          className="select admin-billing-user-filter"
          value={userId ?? ""}
          onChange={(e) => {
            setUserId(e.target.value ? Number(e.target.value) : null);
            setPage(1);
          }}
        >
          <option value="">All users</option>
          {members.map((user) => (
            <option key={user.id} value={user.id}>
              {user.username}
            </option>
          ))}
          {userId !== null && !members.some((user) => user.id === userId) ? (
            <option value={userId}>User {userId}</option>
          ) : null}
        </select>
        <SegmentedControl
          label="Status"
          size="sm"
          value={status}
          options={STATUS_FILTERS}
          onChange={(value) => {
            setStatus(value);
            setPage(1);
          }}
        />
        <label className="admin-inline-check" htmlFor={allMonthsId}>
          <input
            id={allMonthsId}
            type="checkbox"
            className="ui-check"
            checked={allMonths}
            onChange={(e) => {
              setAllMonths(e.target.checked);
              setPage(1);
            }}
          />
          All months
        </label>
      </div>
      <FlashMessage flash={flash} />
      {lookupError ? (
        <div className="ui-notice ui-notice-info admin-inline-notice" role="status">
          <InfoIcon size={15} />
          <span className="ui-notice-text">{lookupError}</span>
        </div>
      ) : null}
      <ResourceGate resource={payments} loadingLabel="Loading payments">
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState title="No payments">
              <p>{allMonths ? "Nothing matches these filters." : "Nothing matches these filters in this month."}</p>
            </EmptyState>
          ) : (
            <>
              <PaymentsTable payments={data.items} onOpen={setOpen} />
              <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} label="Payments pages" onPage={setPage} />
            </>
          )
        }
      </ResourceGate>
      {open ? (
        <PaymentDialog
          payment={open}
          onClose={() => setOpen(null)}
          onChanged={(updated, message) => {
            changed(updated);
            flash.success(message);
          }}
        />
      ) : null}
    </section>
  );
}

const REFUND_STATUS: Record<RefundView["status"], { label: string; className: string }> = {
  creating: { label: "Starting", className: "badge badge-info" },
  pending: { label: "Pending", className: "badge badge-info" },
  processed: { label: "Refunded", className: "badge badge-success" },
  failed: { label: "Failed", className: "badge badge-danger" },
};

const DISPUTE_STATUS: Record<DisputeStatus, string> = {
  open: "Open",
  under_review: "Under review",
  action_required: "Action required",
  won: "Won",
  lost: "Lost",
  closed: "Closed",
};
const DISPUTE_ACTIVE: ReadonlySet<DisputeStatus> = new Set(["open", "under_review", "action_required"]);

type PaymentDialogProps = {
  payment: AdminPayment;
  onClose: () => void;
  onChanged: (payment: AdminPayment, message: string) => void;
};

/** One payment: its facts, disputes and refunds, and the refund form (plans-billing-visual.md 9.4). */
export function PaymentDialog({ payment, onClose, onChanged }: PaymentDialogProps) {
  const [refunding, setRefunding] = useState(false);
  const offline = payment.mode === "offline";
  const canRefund = payment.refundable_paise > 0 && payment.status !== "disputed";
  const footer = refunding ? null : (
    <>
      <button type="button" className="btn" onClick={onClose}>
        Close
      </button>
      {payment.receipt ? (
        <a className="btn" href={adminReceiptUrl(payment.receipt.id)} download>
          <DownloadIcon size={16} />
          Download receipt
        </a>
      ) : null}
      {canRefund ? (
        <button type="button" className="btn btn-danger" onClick={() => setRefunding(true)}>
          Refund…
        </button>
      ) : null}
    </>
  );
  return (
    <Modal open title={`Payment of ${formatInr(payment.amount_paise)}`} onClose={onClose} width={640} footer={footer}>
      <dl className="admin-billing-facts">
        <Fact term="Paid on">{istDateTime(payment.paid_at)}</Fact>
        <Fact term="User">
          {payment.user.username}
          {payment.user.id === null ? <span className="admin-subtext"> (deleted)</span> : null}
        </Fact>
        <Fact term="Plan and period">
          {payment.plan?.name ?? "—"}
          {payment.period_start && payment.period_end
            ? ` · ${formatIstRange(payment.period_start, payment.period_end)}`
            : " · receipt only, no period"}
        </Fact>
        <Fact term="Amount">
          {formatInr(payment.amount_paise)} · {kindLabel(payment.kind)}
          {payment.test_mode ? (
            <>
              {" "}
              <TestBadge />
            </>
          ) : null}
          {payment.credit_used_paise > 0 ? (
            <span className="admin-fact-note">
              {formatInr(payment.credit_used_paise)} of this payment went as credit to a later upgrade.
            </span>
          ) : null}
        </Fact>
        <Fact term="Method">{methodLabel(payment.method)}</Fact>
        {payment.razorpay_payment_id ? (
          <Fact term="Razorpay payment">
            <span className="admin-copy-row">
              <code className="admin-code">{payment.razorpay_payment_id}</code>
              <CopyButton value={payment.razorpay_payment_id} label="Copy the Razorpay payment id" />
            </span>
          </Fact>
        ) : null}
        {payment.offline_reference ? <Fact term="Bank reference">{payment.offline_reference}</Fact> : null}
        {payment.fee_paise !== null ? (
          <Fact term="Razorpay fee">
            {formatInr(payment.fee_paise)}
            {payment.fee_tax_paise ? ` incl. ${formatInr(payment.fee_tax_paise)} GST` : ""}
          </Fact>
        ) : null}
        <Fact term="Status">
          <PaymentStatusBadge payment={payment} />
          {payment.refunded_paise > 0 ? ` ${formatInr(payment.refunded_paise)} refunded` : ""}
        </Fact>
        <Fact term="Receipt">{payment.receipt ? payment.receipt.number : "None"}</Fact>
      </dl>

      {payment.disputes.length ? (
        <section className="admin-billing-sub" aria-label="Disputes">
          <h3 className="admin-billing-sub-title">Disputes</h3>
          <ul className="admin-billing-list">
            {payment.disputes.map((dispute) => (
              <li key={dispute.id}>
                <span>
                  <b>{formatInr(dispute.amount_paise)}</b>{" "}
                  <span
                    className={
                      dispute.status === "lost"
                        ? "badge badge-danger"
                        : DISPUTE_ACTIVE.has(dispute.status)
                          ? "badge badge-warning"
                          : "badge badge-neutral"
                    }
                  >
                    {DISPUTE_STATUS[dispute.status]}
                  </span>
                </span>
                {DISPUTE_ACTIVE.has(dispute.status) && dispute.respond_by ? (
                  <span className="admin-text-warning">
                    <AlertIcon size={14} />
                    Respond in Razorpay's Dashboard by {formatIstDate(dispute.respond_by)}
                  </span>
                ) : (
                  <span className="admin-subtext">Updated {formatIstDate(dispute.updated_at)}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {payment.refunds.length ? (
        <section className="admin-billing-sub" aria-label="Refunds">
          <h3 className="admin-billing-sub-title">Refunds</h3>
          <ul className="admin-billing-list">
            {payment.refunds.map((refund) => (
              <li key={refund.id}>
                <span>
                  <b>{formatInr(refund.amount_paise)}</b>{" "}
                  <span className={REFUND_STATUS[refund.status].className}>{REFUND_STATUS[refund.status].label}</span>
                  {refund.end_access ? <span className="admin-subtext"> · ended access</span> : null}
                </span>
                <span className="admin-subtext">
                  {refund.initiated_by === "razorpay"
                    ? "From Razorpay's Dashboard"
                    : `By ${refund.admin_username ?? "the admin"}`}{" "}
                  on {formatIstDate(refund.created_at)}
                  {refund.reason ? ` · ${refund.reason}` : ""}
                </span>
                {refund.status === "failed" && refund.last_error ? (
                  <div className="ui-notice ui-notice-danger">
                    <AlertCircleIcon size={15} />
                    <span className="ui-notice-text">Razorpay: {refund.last_error}</span>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {refunding ? (
        <RefundForm
          payment={payment}
          offline={offline}
          onCancel={() => setRefunding(false)}
          onDone={(updated, amount) => {
            setRefunding(false);
            onChanged(
              updated,
              offline ? `Recorded a refund of ${formatInr(amount)}.` : `Refund of ${formatInr(amount)} started.`,
            );
          }}
        />
      ) : null}
    </Modal>
  );
}

type RefundFormProps = {
  payment: AdminPayment;
  offline: boolean;
  onCancel: () => void;
  onDone: (payment: AdminPayment, amountPaise: number) => void;
};

/** "Rupees" with up to two decimals → paise; NaN when it isn't one. */
function rupeesToPaise(raw: string): number {
  const text = raw.replace(/[,\s₹]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return Number.NaN;
  const [whole, fraction = ""] = text.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

const asRupees = (paise: number) => (paise % 100 ? (paise / 100).toFixed(2) : String(paise / 100));

function RefundForm({ payment, offline, onCancel, onDone }: RefundFormProps) {
  const [amount, setAmount] = useState(asRupees(payment.refundable_paise));
  const [reason, setReason] = useState("");
  const [endAccess, setEndAccess] = useState(false);
  const [errors, setErrors] = useState<{ amount?: string; reason?: string }>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [invalidSubmits, setInvalidSubmits] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const paise = rupeesToPaise(amount);
  const full = paise === payment.refundable_paise;

  useEffect(() => {
    const selector = invalidSubmits > 0 ? "[aria-invalid='true']" : "input";
    formRef.current?.querySelector<HTMLElement>(selector)?.focus();
  }, [invalidSubmits]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const v = new FormValidator<"amount" | "reason">();
    if (Number.isNaN(paise)) v.fail("amount", "Enter an amount in rupees, like 500 or 499.50.");
    else if (paise < 100) v.fail("amount", "Refund at least ₹1.");
    else if (paise > payment.refundable_paise)
      v.fail("amount", `At most ${formatInr(payment.refundable_paise)} can be refunded.`);
    const text = v.text("reason", reason, 1, 200);
    setErrors(v.errors);
    if (!v.valid) {
      setInvalidSubmits((n) => n + 1);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      const updated = await refundPayment(payment.id, {
        amount_paise: full ? null : paise,
        reason: text,
        end_access: endAccess || full,
      });
      onDone(updated, paise);
    } catch (err) {
      setServerError(errorMessage(err, "Couldn't start the refund. Please try again."));
      setBusy(false);
    }
  }

  return (
    <form ref={formRef} className="admin-form admin-refund-form" onSubmit={onSubmit} noValidate aria-label="Refund">
      <h3 className="admin-billing-sub-title">Refund</h3>
      <Field
        label="Amount"
        error={errors.amount}
        hint={
          <>
            Up to {formatInr(payment.refundable_paise)}, all that's refundable.
            {payment.credit_used_paise > 0
              ? ` ${formatInr(payment.credit_used_paise)} of this payment went as credit to an upgrade.`
              : ""}
          </>
        }
      >
        {(control) => (
          <div className="admin-input-prefix">
            <span aria-hidden="true">₹</span>
            <input
              {...control}
              className="input"
              inputMode="decimal"
              autoComplete="off"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
        )}
      </Field>
      <Field label="Reason" error={errors.reason} hint="Kept with the refund in the billing record.">
        {(control) => (
          <input
            {...control}
            className="input"
            autoComplete="off"
            maxLength={200}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        )}
      </Field>
      <label className="admin-inline-check">
        <input
          type="checkbox"
          className="ui-check"
          checked={endAccess || full}
          disabled={full}
          onChange={(e) => setEndAccess(e.target.checked)}
        />
        <span>
          End access now
          <span className="field-hint">A full refund always ends this payment's period.</span>
        </span>
      </label>
      <div className="ui-notice ui-notice-warning">
        <AlertIcon size={15} />
        <span className="ui-notice-text">
          {offline
            ? "This records a refund you make by bank transfer yourself; no money moves through Razorpay."
            : "The refund goes back to the payer's account in 5 to 7 working days."}
        </span>
      </div>
      {serverError ? (
        <div className="ui-notice ui-notice-danger" role="alert">
          <AlertCircleIcon size={15} />
          <span className="ui-notice-text">{serverError}</span>
        </div>
      ) : null}
      <div className="admin-form-actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="submit" className="btn btn-danger" disabled={busy} aria-busy={busy || undefined}>
          {busy ? <ButtonSpinner size={14} /> : null}
          Refund {Number.isNaN(paise) ? "" : formatInr(paise)}
        </button>
      </div>
    </form>
  );
}
