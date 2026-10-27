import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, billingApi, receiptUrl } from "../../api/client";
import type { BillingProfile, BillingStatus, Me, PaymentView, PlanAccess, PlanOffer } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { formatInr, formatIstDate, formatIstRange } from "../../lib/billing";
import { formatNumber } from "../../lib/format";
import "../billing/billing.css";
import { PendingCheckoutNotice } from "../billing/PendingCheckoutBanner";
import {
  expiringDays,
  isStacked,
  istDayMonth,
  istDaysUntil,
  lastDayOf,
  methodLabel,
  renewalOf,
  renewPath,
} from "../billing/planText";
import PolicyLinks from "../billing/PolicyLinks";
import { AlertIcon, CalendarIcon, CheckCircleIcon, DownloadIcon, FlaskIcon, GiftIcon, TagIcon } from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";
import { PanelError, PanelHead } from "./PanelParts";
import { useLoad } from "./useLoad";

/** How long "✓ Saved" stays after the receipt details are saved. */
const SAVED_MS = 2000;

type Loaded = { status: BillingStatus; payments: PaymentView[]; offers: PlanOffer[] | null };

const DAY_MS = 86_400_000;

/** "A, B and C". */
function listOf(items: string[]): string {
  return items.length <= 1 ? (items[0] ?? "") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** "Files from chat · Claude Haiku 5.5, GPT-5.4 mini and Claude Sonnet 5.5" over "About 260 answers …, 200 documents …". */
function includes(plan: PlanAccess, offer: PlanOffer | null, limits: Me["limits"]): { main: string; detail: string } {
  const features = [
    ...(plan.features.files ? ["Files from chat"] : []),
    ...(plan.features.images ? ["Images from chat"] : []),
    ...(offer?.plugins.filter((plugin) => plan.features.plugins.includes(plugin.id)).map((plugin) => plugin.name) ??
      plan.features.plugins),
  ];
  const models = offer
    ? offer.all_chat_models
      ? "every chat model"
      : listOf(offer.chat_models.map((m) => m.display_name))
    : "";
  const main = [features.length > 0 ? features.join(" · ") : "Chat with your documents", models]
    .filter(Boolean)
    .join(" · ");
  const answers =
    offer && offer.answers_estimate !== null && offer.default_chat_model && limits.monthly_token_limit > 0
      ? `About ${formatNumber(offer.answers_estimate)} answers with ${offer.default_chat_model.display_name}`
      : null;
  const documents =
    limits.max_documents > 0 ? `${formatNumber(limits.max_documents)} documents` : "unlimited documents";
  const storage = limits.max_storage_mb > 0 ? `${formatNumber(limits.max_storage_mb)} MB` : "unlimited storage";
  return {
    main,
    detail: `${listOf([answers, documents, storage].filter((part): part is string => part !== null))} per period`,
  };
}

/** "Your plan ends in 5 days" / "… tomorrow" / "… today". */
function endsIn(days: number): string {
  if (days <= 0) return "ends today";
  return days === 1 ? "ends tomorrow" : `ends in ${days} days`;
}

type CurrentPlanProps = {
  user: Me;
  status: BillingStatus;
  /** The plan's offer in the catalog; null when the catalog couldn't load. */
  offer: PlanOffer | null;
  /** Another plan offers an upgrade now, for a credit. */
  upgradable: boolean;
};

/** The foot's sentence for a plan by a period: what renewing does, or why it can't be renewed now. */
function renewalText(status: BillingStatus, offer: PlanOffer | null, upgradable: boolean): string {
  const plan = status.plan;
  const period = offer?.options.find((option) => option.kind === "period");
  let renewal: string | null;
  if (offer?.status === "retired") {
    renewal = `${offer.name} is no longer offered, so it can't be renewed.`;
  } else if (period?.unavailable_reason === null && plan.valid_through) {
    const length = status.payments.mode === "test" ? "1 day" : "30 days";
    renewal = `Renewing adds ${length} after ${istDayMonth(plan.valid_through)}.`;
  } else if (isStacked(plan) && plan.period_end) {
    renewal = `One more period is already paid; you can renew from ${istDayMonth(plan.period_end)}.`;
  } else {
    renewal = period?.unavailable_reason ?? null;
  }
  const upgrade = upgradable ? "Upgrading starts a new period now, with credit for what's left." : null;
  return [renewal, upgrade].filter(Boolean).join(" ");
}

/** The plan card (visual 8.2): head, the period bar, facts and the actions, for each state. */
function CurrentPlan({ user, status, offer, upgradable }: CurrentPlanProps) {
  const plan = status.plan;
  const titleId = useId();
  const renewable = renewalOf(offer) !== null;
  const retired = offer?.status === "retired";
  const changeLink = (
    <Link className="btn btn-sm" to="/plans">
      Change plan
    </Link>
  );

  if (!plan.access || plan.plan === null) {
    const expired = plan.state === "expired" && plan.plan !== null;
    const ended = plan.valid_through ?? plan.ended_at;
    return (
      <section className="billing-current" aria-labelledby={titleId}>
        <div className="billing-current-head">
          <span
            className={expired ? "billing-plan-tile" : "billing-plan-tile billing-plan-tile-none"}
            aria-hidden="true"
          >
            <TagIcon size={24} />
          </span>
          <div className="billing-current-main">
            <h3 className="billing-current-name" id={titleId}>
              {expired ? plan.plan?.name : "No active plan"}
            </h3>
            {expired && ended ? (
              <div className="billing-current-meta">
                <span className="badge badge-warning">Ended {istDayMonth(ended)}</span>
              </div>
            ) : null}
          </div>
        </div>
        <p className="billing-current-body">
          Choose a plan to start chatting with your documents. Your chats and documents are kept.
        </p>
        <div className="billing-current-foot">
          <div className="billing-foot-actions">
            {expired && plan.plan ? (
              <>
                <Link className="btn btn-sm btn-primary" to={renewable ? renewPath(plan.plan.code) : "/plans"}>
                  Renew {plan.plan.name}
                </Link>
                {changeLink}
              </>
            ) : (
              <Link className="btn btn-sm btn-primary" to="/plans">
                Choose a plan
              </Link>
            )}
          </div>
        </div>
      </section>
    );
  }

  const days = expiringDays(plan);
  const byPeriod = !plan.open_ended && plan.period_start !== null && plan.period_end !== null;
  const lastDay = plan.period_end ? lastDayOf(plan.period_end) : null;
  const left = lastDay ? Math.max(0, istDaysUntil(lastDay)) : null;
  const facts = includes(plan, offer, user.limits);
  const currentPeriod = status.periods.find((period) => period.current);
  const compReason = plan.open_ended ? status.complimentary?.reason : currentPeriod?.reason;
  const foot = byPeriod
    ? renewalText(status, offer, upgradable)
    : "You can still choose a paid plan; your complimentary plan continues after it.";

  let timeline: ReactNode = null;
  if (byPeriod && plan.period_start && lastDay) {
    const start = Date.parse(plan.period_start);
    const total = Math.max(1, Math.round((Date.parse(`${lastDay}T18:30:00Z`) - start) / DAY_MS));
    const day = Math.min(total, Math.max(1, Math.ceil((Date.now() - start) / DAY_MS)));
    timeline = (
      <div className="billing-timeline">
        <span>
          <b>{istDayMonth(plan.period_start)}</b>
        </span>
        <div
          className={days !== null ? "ui-meter ui-meter-warning" : "ui-meter"}
          role="meter"
          aria-label="This period"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={day}
          aria-valuetext={`Day ${day} of ${total}`}
        >
          <span style={{ width: `${(day / total) * 100}%` }} />
        </div>
        <span>
          <b>{formatIstDate(lastDay)}</b>
        </span>
      </div>
    );
  }

  return (
    <section className="billing-current" aria-labelledby={titleId}>
      <div className="billing-current-head">
        <span className="billing-plan-tile" aria-hidden="true">
          <TagIcon size={24} />
        </span>
        <div className="billing-current-main">
          <h3 className="billing-current-name" id={titleId}>
            {plan.plan.name}
          </h3>
          <div className="billing-current-meta">
            <span className="ui-status ui-status-connected">Active</span>
            {offer ? <span>{formatInr(offer.price_paise)} a month</span> : null}
            {plan.complimentary ? (
              <span className="badge">
                <GiftIcon size={13} />
                {plan.open_ended ? "Complimentary · no end date" : "Complimentary"}
              </span>
            ) : null}
            {plan.test_mode ? (
              <span className="badge badge-warning">
                <FlaskIcon size={13} />
                Test mode
              </span>
            ) : null}
          </div>
        </div>
        {byPeriod && left !== null ? (
          <div className="billing-days">
            {left > 0 ? (
              <>
                <b>{left}</b>
                <span>{left === 1 ? "day left in this period" : "days left in this period"}</span>
              </>
            ) : (
              <span>Ends today</span>
            )}
          </div>
        ) : null}
      </div>
      {timeline}
      {days !== null ? (
        <p className="ui-notice ui-notice-warning billing-current-notice">
          <AlertIcon size={15} />
          <span className="ui-notice-text">
            Your plan {endsIn(days)}.
            {renewable
              ? " Renew to keep using the assistant without a break."
              : retired
                ? " Choose another plan to keep using the assistant without a break."
                : ""}
          </span>
        </p>
      ) : null}
      <dl className="billing-facts">
        {byPeriod && isStacked(plan) && plan.valid_through ? (
          <>
            <dt>Paid through</dt>
            <dd>
              {formatIstDate(plan.valid_through)}
              <small>One more period is already paid</small>
            </dd>
          </>
        ) : null}
        {plan.next_plan && plan.period_end ? (
          <>
            <dt>Then</dt>
            <dd>
              {plan.next_plan.name} from {istDayMonth(plan.period_end)}
            </dd>
          </>
        ) : null}
        <dt>Includes</dt>
        <dd>
          {facts.main}
          <small>{facts.detail}</small>
        </dd>
        {plan.complimentary && compReason ? (
          <>
            <dt>Complimentary</dt>
            <dd>{compReason}</dd>
          </>
        ) : null}
      </dl>
      <div className="billing-current-foot">
        {foot ? <p>{foot}</p> : null}
        <div className="billing-foot-actions">
          {byPeriod && retired ? (
            <Link className="btn btn-sm btn-primary" to="/plans">
              Choose a plan
            </Link>
          ) : (
            changeLink
          )}
          {byPeriod && renewable ? (
            <Link className="btn btn-sm btn-primary" to={renewPath(plan.plan.code)}>
              Renew
            </Link>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function ComingUp({ status }: { status: BillingStatus }) {
  const id = useId();
  const scheduled = status.periods.filter((period) => !period.current);
  if (scheduled.length === 0) return null;
  return (
    <>
      <h3 className="settings-sub" id={id}>
        Coming up
      </h3>
      <ul className="billing-list" aria-labelledby={id}>
        {scheduled.map((period) => (
          <li key={period.id} className="billing-list-row">
            <span className="billing-list-icon" aria-hidden="true">
              {period.source === "comp" ? <GiftIcon size={18} /> : <CalendarIcon size={18} />}
            </span>
            <div className="billing-list-text">
              <b>{period.plan_name}</b>
              <span>{formatIstRange(period.starts_at, period.ends_at)}</span>
            </div>
            <span className="badge">{period.source === "comp" ? "Complimentary" : "Paid"}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

type DetailsProps = { status: BillingStatus; username: string; onSaved: () => void };

/** Name on receipts and, when reminder emails can be sent, the email for them (visual 8.3). */
function ReceiptDetails({ status, username, onSaved }: DetailsProps) {
  const id = useId();
  const reminders = status.payments.email_reminders;
  const [saved, setSaved] = useState<BillingProfile>(status.profile);
  const [name, setName] = useState(status.profile.name ?? "");
  const [email, setEmail] = useState(status.profile.email ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const next: BillingProfile = { name: name.trim() || null, email: reminders ? email.trim() || null : saved.email };
  const changed = next.name !== saved.name || next.email !== saved.email;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!changed || saving) return;
    setSaving(true);
    setError(null);
    try {
      const result = await billingApi.saveProfile(next);
      setSaved(result);
      setName(result.name ?? "");
      setEmail(result.email ?? "");
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save your receipt details. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  const channel = !reminders
    ? "Plan reminders: in the app."
    : saved.email
      ? "Plan reminders: in the app and by email."
      : "Plan reminders: in the app. Add an email to get them by email too.";

  return (
    <>
      <h3 className="settings-sub" id={`${id}-title`}>
        Receipt details
      </h3>
      <p className="settings-desc">
        {reminders
          ? "Optional. Shown on your receipts; the email is used only for reminders before your plan ends."
          : "Optional. Shown on your receipts."}
      </p>
      <form className="billing-form" aria-labelledby={`${id}-title`} onSubmit={(event) => void save(event)} noValidate>
        <div className="field">
          <label className="field-label" htmlFor={`${id}-name`}>
            Name on receipts
          </label>
          <input
            id={`${id}-name`}
            className="input"
            value={name}
            maxLength={100}
            autoComplete="name"
            placeholder={username}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        {reminders ? (
          <div className="field">
            <label className="field-label" htmlFor={`${id}-email`}>
              Email for reminders
            </label>
            <input
              id={`${id}-email`}
              className="input"
              type="email"
              value={email}
              maxLength={254}
              autoComplete="email"
              placeholder="you@example.com"
              aria-invalid={error ? true : undefined}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
        ) : null}
        <div className="billing-form-actions">
          {/* aria-disabled, not disabled: once saved the button keeps the focus instead of dropping it to the page. */}
          <button
            type="submit"
            className="btn btn-sm"
            aria-disabled={!changed || undefined}
            aria-busy={saving || undefined}
          >
            {saving ? <ButtonSpinner size={12} /> : null}
            Save
          </button>
          <p>{channel}</p>
        </div>
        {error ? (
          <p className="error-text settings-error" role="alert">
            <AlertIcon size={13} />
            {error}
          </p>
        ) : null}
      </form>
    </>
  );
}

function statusBadge(payment: PaymentView): ReactNode {
  switch (payment.status) {
    case "captured":
      return <span className="badge badge-success">Paid</span>;
    case "refunded":
      return <span className="badge">Refunded</span>;
    case "partially_refunded":
      return <span className="badge">Refunded {formatInr(payment.refunded_paise)}</span>;
    case "disputed":
      return <span className="badge badge-danger">Disputed</span>;
  }
}

function History({ payments }: { payments: PaymentView[] }) {
  if (payments.length === 0) return <p className="settings-desc">No payments yet.</p>;
  return (
    <div className="table-wrap">
      <table className="table billing-table">
        <caption className="sr-only">Your payments</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Description</th>
            <th scope="col" className="num">
              Amount
            </th>
            <th scope="col">Status</th>
            <th scope="col">Receipt</th>
          </tr>
        </thead>
        <tbody>
          {payments.map((payment) => {
            const sub = [
              payment.period_start && payment.period_end
                ? formatIstRange(payment.period_start, payment.period_end)
                : null,
              methodLabel(payment.method),
            ].filter(Boolean);
            return (
              <tr key={payment.id}>
                <td className="billing-table-date">{formatIstDate(payment.paid_at)}</td>
                <td className="billing-table-desc">
                  <b>{payment.description.split(": ")[0]}</b>
                  {sub.length > 0 ? <small>{sub.join(" · ")}</small> : null}
                </td>
                <td className="num billing-table-amount">{formatInr(payment.amount_paise)}</td>
                <td className="billing-table-status">
                  {statusBadge(payment)}
                  {payment.test_mode ? <span className="badge badge-warning">Test</span> : null}
                </td>
                <td className="billing-table-receipt">
                  {payment.receipt ? (
                    <a
                      className="btn btn-ghost btn-xs billing-receipt-link"
                      href={receiptUrl(payment.receipt.id)}
                      aria-label={`Download receipt ${payment.receipt.number}`}
                    >
                      <DownloadIcon size={14} />
                      {payment.receipt.number}
                    </a>
                  ) : (
                    "—"
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

function Skeleton() {
  return (
    <div className="settings-skeleton">
      <span className="sr-only">Loading your plan…</span>
      <div className="settings-skeleton-card settings-skeleton-block" aria-hidden="true" />
      {[0, 1, 2].map((row) => (
        <span key={row} className="ui-skeleton billing-skeleton-row" aria-hidden="true" />
      ))}
    </div>
  );
}

/** Settings → Plan & billing (functional 8.5, visual 8): the plan, what comes next, receipt details and payments. */
export default function PlanBillingTab({ user, onClose }: { user: Me; onClose: () => void }) {
  const { refresh } = useAuth();
  const [savedAt, setSavedAt] = useState(0);
  const live = useRef(true);
  const data = useLoad<Loaded | null>(async (signal) => {
    if (user.is_admin) return null;
    const [status, payments, catalog] = await Promise.all([
      billingApi.status(signal),
      billingApi.payments(signal),
      billingApi.plans(signal).catch(() => null),
    ]);
    return { status, payments, offers: catalog?.plans ?? null };
  }, "Couldn't load your plan.");

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    if (!savedAt) return;
    const timer = window.setTimeout(() => setSavedAt(0), SAVED_MS);
    return () => window.clearTimeout(timer);
  }, [savedAt]);

  let body: ReactNode;
  if (user.is_admin) {
    body = (
      <p className="ui-notice" role="status">
        <TagIcon size={15} />
        <span className="ui-notice-text">Plans don't apply to the admin.</span>
      </p>
    );
  } else if (data.data) {
    const { status, payments, offers } = data.data;
    const code = status.plan.plan?.code;
    const offer = offers?.find((item) => item.code === code) ?? null;
    const upgradable =
      offers?.some(
        (item) =>
          item.code !== code &&
          item.options.some((option) => option.kind === "now" && option.unavailable_reason === null),
      ) ?? false;
    const pending = status.plan.pending_order ?? status.open_order;
    const contact = status.payments.contact_email;
    body = (
      <>
        {pending ? (
          <div className="billing-pending">
            <PendingCheckoutNotice
              order={pending}
              onCancelled={() => {
                data.reload();
                void refresh().catch(() => undefined);
              }}
            />
          </div>
        ) : null}
        <CurrentPlan user={user} status={status} offer={offer} upgradable={upgradable} />
        <ComingUp status={status} />
        <ReceiptDetails
          status={status}
          username={user.username}
          onSaved={() => {
            if (live.current) setSavedAt(Date.now());
          }}
        />
        <h3 className="settings-sub">Payment history</h3>
        <History payments={payments} />
        <p className="billing-help">
          {contact ? (
            <>
              Questions about a payment? Write to <a href={`mailto:${contact}`}>{contact}</a>.
            </>
          ) : (
            "Questions about a payment? Ask the admin."
          )}
        </p>
        <PolicyLinks />
      </>
    );
  } else if (data.error) {
    body = <PanelError message={data.error} onRetry={data.reload} />;
  } else {
    body = <Skeleton />;
  }

  return (
    <>
      <PanelHead
        title="Plan & billing"
        onClose={onClose}
        actions={
          savedAt ? (
            <span key={savedAt} className="settings-saved" role="status">
              <CheckCircleIcon size={15} />
              Saved
            </span>
          ) : null
        }
      />
      <div className="settings-body" aria-busy={data.loading && !data.data && !user.is_admin}>
        {body}
      </div>
    </>
  );
}
