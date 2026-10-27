import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { AdminPlan, AdminUser, PeriodView, PlanAccess, UserBilling } from "../../api/types";
import { formatIstDate, formatIstRange } from "../../lib/billing";
import { formatNumber } from "../../lib/format";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, ClockIcon, GiftIcon } from "../ui/icons";
import { changePlan, endComplimentary, errorMessage, grantPlan, revokePeriod, userBillingPath } from "./adminApi";
import { endedOn, istDateTime, shortIstDate, TestBadge } from "./billingUi";
import { SegmentedField, SwitchField } from "./Field";
import { useResource } from "./hooks";
import ReasonAction from "./ReasonAction";
import { ErrorState, LoadingState } from "./StateViews";

const EVENTS_SHOWN = 5;
const MAX_GRANT_DAYS = 90;

/** "Active · Standard · valid 26 Oct to 25 Nov 2026", with badges. */
function StateLine({ plan }: { plan: PlanAccess }) {
  let text: string;
  if (plan.state === "none") text = "No plan";
  else if (plan.state === "expired") {
    const ended = endedOn(plan);
    text = `${plan.plan?.name ?? "The plan"} ended${ended ? ` on ${formatIstDate(ended)}` : ""}`;
  } else if (plan.open_ended || !plan.period_start || !plan.period_end) {
    text = `${plan.complimentary ? "Complimentary " : ""}${plan.plan?.name ?? ""}, no end date`;
  } else {
    text = `${plan.plan?.name ?? ""}, valid ${formatIstRange(plan.period_start, plan.period_end)}`;
    if (plan.valid_through && plan.paid_through && plan.paid_through !== plan.period_end) {
      text += `; paid through ${formatIstDate(plan.valid_through)}`;
    }
  }
  return (
    <p className="admin-plan-state">
      {plan.access ? (
        <span className="ui-status-connected">Active</span>
      ) : (
        <span className="admin-plan-state-off">{plan.state === "expired" ? "Ended" : "No access"}</span>
      )}
      <span>{text}</span>
      {plan.expiring_soon ? (
        <span className="badge badge-warning">
          <ClockIcon size={13} />
          Ends soon
        </span>
      ) : null}
      {plan.complimentary && !plan.open_ended ? (
        <span className="badge badge-neutral">
          <GiftIcon size={13} />
          Complimentary
        </span>
      ) : null}
      {plan.test_mode ? <TestBadge /> : null}
      {plan.next_plan ? <span className="admin-subtext">Then {plan.next_plan.name}.</span> : null}
    </p>
  );
}

type UserPlanSectionProps = {
  user: AdminUser;
  plans: AdminPlan[];
  /** Open the grant form at once (Billing → an order in review → Grant instead). */
  startGrant?: boolean;
  onChanged: (message: string) => void;
  onNavigate: (hash: string) => void;
};

/** The plan part of the user form: state, periods (revoke), the open-ended plan (end), grant and change plan (8.12). */
export default function UserPlanSection({
  user,
  plans,
  startGrant = false,
  onChanged,
  onNavigate,
}: UserPlanSectionProps) {
  const billing = useResource<UserBilling>(userBillingPath(user.id), "Couldn't load this user's plan.");
  const [form, setForm] = useState<"grant" | "change" | null>(startGrant ? "grant" : null);
  const now = Date.now();

  if (billing.data === null) {
    return billing.error ? (
      <ErrorState message={billing.error} onRetry={() => void billing.reload()} />
    ) : (
      <LoadingState label="Loading the plan" />
    );
  }
  const data = billing.data;
  const unexpired = data.periods.filter((period) => Date.parse(period.ends_at) > now);
  const hasCurrent = unexpired.some((period) => period.current);

  function applied(result: UserBilling, message: string) {
    billing.setData(result);
    setForm(null);
    onChanged(message);
  }

  return (
    <div className="admin-plan-section">
      <StateLine plan={data.plan} />
      {data.complimentary ? (
        <div className="admin-plan-comp">
          <span>
            <GiftIcon size={15} />
            Complimentary {data.complimentary.plan.name}, no end date · granted by {data.complimentary.granted_by} on{" "}
            {shortIstDate(data.complimentary.granted_at)}
            <span className="admin-subtext"> · {data.complimentary.reason}</span>
          </span>
          <ReasonAction
            label="End…"
            ariaLabel={`End ${user.username}'s complimentary ${data.complimentary.plan.name} plan`}
            message={
              <>
                End <strong>{user.username}</strong>'s complimentary {data.complimentary.plan.name} plan now?
              </>
            }
            confirmLabel="End plan"
            onConfirm={async (reason) =>
              applied(await endComplimentary(user.id, { reason }), `Ended ${user.username}'s complimentary plan.`)
            }
          />
        </div>
      ) : null}
      {data.periods.length ? <PeriodsTable user={user} periods={data.periods} now={now} onRevoked={applied} /> : null}
      {form === null ? (
        <div className="admin-plan-actions">
          <button type="button" className="btn btn-sm" onClick={() => setForm("grant")}>
            <GiftIcon size={15} />
            Grant a complimentary plan…
          </button>
          {unexpired.length ? (
            <button type="button" className="btn btn-sm" onClick={() => setForm("change")}>
              Change plan…
            </button>
          ) : null}
          <button
            type="button"
            className="admin-link-button"
            onClick={() => onNavigate(`billing/payments?user=${user.id}`)}
          >
            View payments
          </button>
        </div>
      ) : form === "grant" ? (
        <GrantForm
          user={user}
          plans={plans}
          hasCurrent={hasCurrent}
          onCancel={() => setForm(null)}
          onDone={(result, message) => applied(result, message)}
        />
      ) : (
        <ChangePlanForm user={user} plans={plans} onCancel={() => setForm(null)} onDone={applied} />
      )}
      {data.events.length ? (
        <div className="admin-plan-events">
          <span className="admin-plan-events-title">Recent billing events</span>
          <ul>
            {data.events.slice(0, EVENTS_SHOWN).map((event, index) => (
              <li key={`${event.at}-${index}`}>
                <time dateTime={event.at}>{istDateTime(event.at)}</time> · {event.type}
                {event.actor ? ` · ${event.actor}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

type PeriodsTableProps = {
  user: AdminUser;
  periods: PeriodView[];
  now: number;
  onRevoked: (result: UserBilling, message: string) => void;
};

function PeriodsTable({ user, periods, now, onRevoked }: PeriodsTableProps) {
  return (
    <div className="table-wrap admin-periods">
      <table className="table admin-table">
        <caption className="sr-only">Periods of {user.username}, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Plan</th>
            <th scope="col">Dates</th>
            <th scope="col">Source</th>
            <th scope="col" className="admin-actions-cell">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {periods.map((period) => {
            const dates = formatIstRange(period.starts_at, period.ends_at);
            const ended = Date.parse(period.ends_at) <= now;
            return (
              <tr key={period.id} className={ended ? "admin-period-past" : undefined}>
                <td>
                  {period.plan_name}
                  {period.current ? <span className="badge badge-success admin-period-current">Current</span> : null}
                </td>
                <td className="admin-nowrap">{dates}</td>
                <td>
                  <span className="admin-badges">
                    {period.source === "comp" ? (
                      <span className="badge badge-neutral" title={period.reason ?? undefined}>
                        <GiftIcon size={13} />
                        Complimentary
                      </span>
                    ) : (
                      <span className="badge badge-neutral">Paid</span>
                    )}
                    {period.test_mode ? <TestBadge /> : null}
                  </span>
                </td>
                <td className="admin-actions-cell">
                  {ended ? null : (
                    <ReasonAction
                      label="Revoke…"
                      ariaLabel={`Revoke the ${period.plan_name} period ${dates}`}
                      message={
                        <>
                          Revoke <strong>{user.username}</strong>'s {period.plan_name} period, {dates}? Later periods
                          move up.
                          {period.source === "payment" ? " No refund is made: refund the payment in Billing." : ""}
                        </>
                      }
                      confirmLabel="Revoke"
                      onConfirm={async (reason) =>
                        onRevoked(
                          await revokePeriod(period.id, { reason }),
                          `Revoked the ${period.plan_name} period, ${dates}.`,
                        )
                      }
                    />
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Plans for a grant or a change: every plan by rank (a retired plan can still be granted). */
function PlanSelect({
  plans,
  value,
  onChange,
  id,
}: {
  plans: AdminPlan[];
  value: string;
  onChange: (id: string) => void;
  id: string;
}) {
  return (
    <select id={id} className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {[...plans]
        .sort((a, b) => a.rank - b.rank)
        .map((plan) => (
          <option key={plan.id} value={plan.id}>
            {plan.name}
            {plan.status === "retired" ? " (retired)" : ""}
          </option>
        ))}
    </select>
  );
}

/** Enter in a field of an inline form inside the user form runs this form, not the user form. */
function onEnter(run: () => void) {
  return (e: KeyboardEvent<HTMLElement>) => {
    const { target } = e;
    if (
      e.key === "Enter" &&
      target instanceof HTMLInputElement &&
      target.type !== "checkbox" &&
      target.type !== "radio"
    ) {
      e.preventDefault();
      run();
    }
  };
}

type GrantFormProps = {
  user: AdminUser;
  plans: AdminPlan[];
  hasCurrent: boolean;
  onCancel: () => void;
  onDone: (result: UserBilling, message: string) => void;
};

function GrantForm({ user, plans, hasCurrent, onCancel, onDone }: GrantFormProps) {
  const id = useId();
  const firstRef = useRef<HTMLDivElement>(null);
  const fallback = plans.find((plan) => plan.code === "professional") ?? [...plans].sort((a, b) => b.rank - a.rank)[0];
  const [planId, setPlanId] = useState(fallback ? String(fallback.id) : "");
  const [days, setDays] = useState("30");
  const [openEnded, setOpenEnded] = useState(false);
  const [reason, setReason] = useState("");
  const [start, setStart] = useState<"now" | "after_current">(hasCurrent ? "after_current" : "now");
  const [errors, setErrors] = useState<{ days?: string; reason?: string }>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [invalidSubmits, setInvalidSubmits] = useState(0);
  const plan = plans.find((p) => String(p.id) === planId);

  useEffect(() => {
    const selector = invalidSubmits > 0 ? "[aria-invalid='true']" : "select";
    firstRef.current?.querySelector<HTMLElement>(selector)?.focus();
  }, [invalidSubmits]);

  async function submit() {
    if (busy || !plan) return;
    const count = Number(days);
    const next: typeof errors = {};
    if (!openEnded && !(Number.isInteger(count) && count >= 1 && count <= MAX_GRANT_DAYS)) {
      next.days = `Enter 1 to ${MAX_GRANT_DAYS} days.`;
    }
    if (!reason.trim()) next.reason = "Give a reason: it's kept in the billing record.";
    else if (reason.trim().length > 200) next.reason = "Use at most 200 characters.";
    setErrors(next);
    if (next.days || next.reason) {
      setInvalidSubmits((n) => n + 1);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      const result = await grantPlan(user.id, {
        plan_id: plan.id,
        days: openEnded ? null : count,
        reason: reason.trim(),
        start,
      });
      const what = openEnded
        ? `${plan.name} with no end date`
        : `${plan.name} for ${formatNumber(count)} day${count === 1 ? "" : "s"}`;
      onDone(result, `Granted ${user.username} ${what}.`);
    } catch (err) {
      setServerError(errorMessage(err, "Couldn't grant the plan. Please try again."));
      setBusy(false);
    }
  }

  return (
    <div
      ref={firstRef}
      className="admin-inline-form"
      role="group"
      aria-labelledby={`${id}-title`}
      onKeyDown={onEnter(() => void submit())}
    >
      <h4 id={`${id}-title`} className="admin-inline-form-title">
        Grant a complimentary plan
      </h4>
      <div className="admin-form-grid">
        <div className="field">
          <label className="field-label" htmlFor={`${id}-plan`}>
            Plan
          </label>
          <PlanSelect id={`${id}-plan`} plans={plans} value={planId} onChange={setPlanId} />
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${id}-days`}>
            Days
          </label>
          <input
            id={`${id}-days`}
            className="input"
            inputMode="numeric"
            disabled={openEnded}
            value={openEnded ? "" : days}
            placeholder={openEnded ? "No end date" : undefined}
            aria-invalid={errors.days ? true : undefined}
            aria-describedby={`${id}-days-hint`}
            onChange={(e) => setDays(e.target.value)}
          />
          {errors.days ? (
            <span className="error-text">
              <AlertCircleIcon size={13} />
              {errors.days}
            </span>
          ) : null}
          <span id={`${id}-days-hint`} className="field-hint">
            1 to {MAX_GRANT_DAYS}. Rows of up to 30 days; a shorter last one gets allowances for its length.
          </span>
        </div>
      </div>
      <SwitchField
        label="No end date"
        hint="The plan's current values, allowances per calendar month, until you end it."
        checked={openEnded}
        onChange={setOpenEnded}
      />
      <div className="field">
        <label className="field-label" htmlFor={`${id}-reason`}>
          Reason
        </label>
        <input
          id={`${id}-reason`}
          className="input"
          maxLength={200}
          autoComplete="off"
          value={reason}
          aria-invalid={errors.reason ? true : undefined}
          onChange={(e) => setReason(e.target.value)}
        />
        {errors.reason ? (
          <span className="error-text">
            <AlertCircleIcon size={13} />
            {errors.reason}
          </span>
        ) : null}
      </div>
      {openEnded ? null : (
        <SegmentedField
          label="Start"
          value={start}
          options={[
            { value: "now", label: "Now" },
            { value: "after_current", label: "After the current plan" },
          ]}
          onChange={setStart}
          hint={
            start === "now" && hasCurrent
              ? "The user has a current period: revoke it first, or start after it."
              : undefined
          }
        />
      )}
      {serverError ? (
        <div className="ui-notice ui-notice-danger" role="alert">
          <AlertCircleIcon size={15} />
          <span className="ui-notice-text">{serverError}</span>
        </div>
      ) : null}
      <div className="admin-form-actions">
        <button type="button" className="btn btn-sm" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={busy || !plan}
          aria-busy={busy || undefined}
          onClick={() => void submit()}
        >
          {busy ? <ButtonSpinner size={12} /> : null}
          Grant
        </button>
      </div>
    </div>
  );
}

type ChangePlanFormProps = {
  user: AdminUser;
  plans: AdminPlan[];
  onCancel: () => void;
  onDone: (result: UserBilling, message: string) => void;
};

function ChangePlanForm({ user, plans, onCancel, onDone }: ChangePlanFormProps) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [planId, setPlanId] = useState(() => String([...plans].sort((a, b) => b.rank - a.rank)[0]?.id ?? ""));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const plan = plans.find((p) => String(p.id) === planId);

  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("select")?.focus();
  }, []);

  async function submit() {
    if (busy || !plan) return;
    const text = reason.trim();
    if (!text) {
      setError("Give a reason: it's kept in the billing record.");
      ref.current?.querySelector<HTMLElement>("input")?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    setServerError(null);
    try {
      onDone(
        await changePlan(user.id, { plan_id: plan.id, reason: text }),
        `Moved ${user.username}'s periods to ${plan.name}.`,
      );
    } catch (err) {
      setServerError(errorMessage(err, "Couldn't change the plan. Please try again."));
      setBusy(false);
    }
  }

  return (
    <div
      ref={ref}
      className="admin-inline-form"
      role="group"
      aria-labelledby={`${id}-title`}
      onKeyDown={onEnter(() => void submit())}
    >
      <h4 id={`${id}-title`} className="admin-inline-form-title">
        Change plan at no charge
      </h4>
      <p className="field-hint">
        Every period that hasn't ended gets this plan's features and limits now; dates and what was paid stay. For
        mistakes and goodwill upgrades.
      </p>
      <div className="admin-form-grid">
        <div className="field">
          <label className="field-label" htmlFor={`${id}-plan`}>
            Plan
          </label>
          <PlanSelect id={`${id}-plan`} plans={plans} value={planId} onChange={setPlanId} />
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${id}-reason`}>
            Reason
          </label>
          <input
            id={`${id}-reason`}
            className="input"
            maxLength={200}
            autoComplete="off"
            value={reason}
            aria-invalid={error ? true : undefined}
            onChange={(e) => setReason(e.target.value)}
          />
          {error ? (
            <span className="error-text">
              <AlertCircleIcon size={13} />
              {error}
            </span>
          ) : null}
        </div>
      </div>
      {serverError ? (
        <div className="ui-notice ui-notice-danger" role="alert">
          <AlertCircleIcon size={15} />
          <span className="ui-notice-text">{serverError}</span>
        </div>
      ) : null}
      <div className="admin-form-actions">
        <button type="button" className="btn btn-sm" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={busy || !plan}
          aria-busy={busy || undefined}
          onClick={() => void submit()}
        >
          {busy ? <ButtonSpinner size={12} /> : null}
          Change plan
        </button>
      </div>
    </div>
  );
}
