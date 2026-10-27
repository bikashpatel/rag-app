import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import type { AdminPayment, AdminPlan, AdminUser } from "../../api/types";
import { formatInr } from "../../lib/billing";
import Modal from "../ui/Modal";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon } from "../ui/icons";
import { errorMessage, recordOfflinePayment } from "./adminApi";
import { istToday } from "./billingUi";
import { Field, SwitchField } from "./Field";
import { FormValidator, type FieldErrors } from "./form";
import { useResource } from "./hooks";
import { ErrorState, LoadingState } from "./StateViews";

type ErrorKey = "user" | "plan" | "amount" | "received" | "reference" | "note";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

type RecordTransferModalProps = { onClose: () => void; onRecorded: (payment: AdminPayment) => void };

/** A bank transfer the owner received: a receipt in the live series and, by default, a period of the plan (4.9). */
export default function RecordTransferModal({ onClose, onRecorded }: RecordTransferModalProps) {
  const users = useResource<AdminUser[]>("/admin/users", "Couldn't load the users.");
  const plans = useResource<AdminPlan[]>("/admin/plans", "Couldn't load the plans.");
  const formId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const today = istToday();
  const [userId, setUserId] = useState("");
  const [planId, setPlanId] = useState("");
  const [amount, setAmount] = useState("");
  const [received, setReceived] = useState(today);
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [apply, setApply] = useState(true);
  const [errors, setErrors] = useState<FieldErrors<ErrorKey>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [invalidSubmits, setInvalidSubmits] = useState(0);

  const members = useMemo(
    () => (users.data ?? []).filter((user) => !user.is_admin).sort((a, b) => a.username.localeCompare(b.username)),
    [users.data],
  );
  const planList = useMemo(() => [...(plans.data ?? [])].sort((a, b) => a.rank - b.rank), [plans.data]);
  const plan = planList.find((p) => String(p.id) === planId) ?? null;
  const rupees = Number(amount.replace(/[,\s]/g, ""));
  const amountPaise = Number.isInteger(rupees) ? rupees * 100 : Number.NaN;
  const belowPrice = plan !== null && amountPaise < plan.price_paise;

  useEffect(() => {
    if (invalidSubmits > 0) formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus();
  }, [invalidSubmits]);

  function clear(key: ErrorKey) {
    setErrors((current) => (current[key] ? { ...current, [key]: undefined } : current));
  }

  function choosePlan(id: string) {
    setPlanId(id);
    clear("plan");
    const chosen = planList.find((p) => String(p.id) === id);
    if (chosen && !amount) setAmount(String(chosen.price_paise / 100));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    const v = new FormValidator<ErrorKey>();
    if (!userId) v.fail("user", "Choose the user who paid.");
    if (!plan) v.fail("plan", "Choose the plan it pays for.");
    const paise = v.integer("amount", amount.replace(/[,\s]/g, ""), 1, 100_000) * 100;
    if (!DATE_PATTERN.test(received)) v.fail("received", "Enter the date the money arrived.");
    else if (received > today) v.fail("received", "The date can't be in the future.");
    const ref = v.text("reference", reference, 1, 64);
    const noteText = v.optionalText("note", note, 500);
    if (plan && paise < plan.price_paise && !noteText) {
      v.fail("note", `Say why the amount is below ${plan.name}'s price of ${formatInr(plan.price_paise)}.`);
    }
    setErrors(v.errors);
    if (!v.valid || !plan) {
      setInvalidSubmits((n) => n + 1);
      return;
    }
    setSaving(true);
    setServerError(null);
    try {
      const payment = await recordOfflinePayment({
        user_id: Number(userId),
        plan_id: plan.id,
        amount_paise: paise,
        received_on: received,
        reference: ref,
        note: noteText,
        apply,
      });
      onRecorded(payment);
    } catch (err) {
      setServerError(errorMessage(err, "Couldn't record the transfer. Please try again."));
      setSaving(false);
    }
  }

  const loading = users.data === null || plans.data === null;
  const loadError = users.error ?? plans.error;
  return (
    <Modal
      open
      title="Record bank transfer"
      onClose={onClose}
      width={560}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            type="submit"
            form={formId}
            className="btn btn-primary"
            disabled={saving || loading}
            aria-busy={saving || undefined}
          >
            {saving ? <ButtonSpinner size={14} /> : null}
            Record {Number.isNaN(amountPaise) || amountPaise < 100 ? "" : formatInr(amountPaise)}
          </button>
        </>
      }
    >
      {loading ? (
        loadError ? (
          <ErrorState
            message={loadError}
            onRetry={() => {
              void users.reload();
              void plans.reload();
            }}
          />
        ) : (
          <LoadingState label="Loading users and plans" />
        )
      ) : (
        <form id={formId} ref={formRef} className="admin-form" onSubmit={onSubmit} noValidate>
          <p className="admin-form-lede">
            For NEFT, RTGS or IMPS transfers to your account. The same as a Razorpay payment: a receipt and, unless you
            turn it off, a 30-day period from today or when the paid time ends.
          </p>
          <div className="admin-form-grid">
            <Field label="User" error={errors.user}>
              {(control) => (
                <select
                  {...control}
                  className="select"
                  value={userId}
                  onChange={(e) => {
                    setUserId(e.target.value);
                    clear("user");
                  }}
                >
                  <option value="">Choose a user</option>
                  {members.map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.username}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Plan" error={errors.plan}>
              {(control) => (
                <select {...control} className="select" value={planId} onChange={(e) => choosePlan(e.target.value)}>
                  <option value="">Choose a plan</option>
                  {planList.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} · {formatInr(p.price_paise)}
                      {p.status === "retired" ? " (retired)" : ""}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Amount received" error={errors.amount} hint="Whole rupees, at least ₹1.">
              {(control) => (
                <div className="admin-input-prefix">
                  <span aria-hidden="true">₹</span>
                  <input
                    {...control}
                    className="input"
                    inputMode="numeric"
                    autoComplete="off"
                    value={amount}
                    onChange={(e) => {
                      setAmount(e.target.value);
                      clear("amount");
                    }}
                  />
                </div>
              )}
            </Field>
            <Field label="Received on" error={errors.received}>
              {(control) => (
                <input
                  {...control}
                  type="date"
                  className="input"
                  max={today}
                  value={received}
                  onChange={(e) => {
                    setReceived(e.target.value);
                    clear("received");
                  }}
                />
              )}
            </Field>
            <Field
              label="Bank reference"
              error={errors.reference}
              hint="The UTR or reference on your bank statement."
              className="admin-span-2"
            >
              {(control) => (
                <input
                  {...control}
                  className="input"
                  autoComplete="off"
                  maxLength={64}
                  value={reference}
                  onChange={(e) => {
                    setReference(e.target.value);
                    clear("reference");
                  }}
                />
              )}
            </Field>
            <Field
              label="Note"
              error={errors.note}
              hint={
                belowPrice && plan
                  ? `Required: the amount is below ${plan.name}'s price of ${formatInr(plan.price_paise)}.`
                  : "Optional."
              }
              className="admin-span-2"
            >
              {(control) => (
                <textarea
                  {...control}
                  className="input admin-textarea"
                  rows={2}
                  maxLength={500}
                  value={note}
                  onChange={(e) => {
                    setNote(e.target.value);
                    clear("note");
                  }}
                />
              )}
            </Field>
          </div>
          <SwitchField
            label="Start a period"
            hint={
              apply
                ? "Adds a 30-day period of the plan."
                : "Receipt only: for a transfer that pays for a plan change you made."
            }
            checked={apply}
            onChange={setApply}
          />
          {serverError ? (
            <div className="ui-notice ui-notice-danger" role="alert">
              <AlertCircleIcon size={15} />
              <span className="ui-notice-text">{serverError}</span>
            </div>
          ) : null}
        </form>
      )}
    </Modal>
  );
}
