import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type {
  AdminPayment,
  AttentionItem,
  AttentionKind,
  BillingAdminConfig,
  BillingSummary,
  Page,
  UserPlanRow,
} from "../../api/types";
import { formatInr } from "../../lib/billing";
import { formatNumber } from "../../lib/format";
import Avatar from "../ui/Avatar";
import { AlertCircleIcon, AlertIcon, BankIcon, DownloadIcon, FlaskIcon, InfoIcon } from "../ui/icons";
import { billingExportUrl, billingSummaryPath, paymentsPath } from "./adminApi";
import { PaymentDialog, PaymentsTable } from "./BillingPayments";
import { plural, shortIstDate, TEST_LINK_CAP } from "./billingUi";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource, type Resource } from "./hooks";
import { monthLabel } from "./MonthPicker";
import { EmptyState, ResourceGate } from "./StateViews";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const RECENT = 5;

const DANGER: ReadonlySet<AttentionKind> = new Set(["refund_failed", "keys_rejected", "dispute"]);
const INFO: ReadonlySet<AttentionKind> = new Set(["test_mode", "payments_not_configured", "refund_pending"]);
/** Shown as notices above the list when the payments setup has loaded. */
const NOTICE_KINDS: ReadonlySet<AttentionKind> = new Set(["test_mode", "payments_not_configured"]);

function attentionAction(item: AttentionItem): { label: string; target: string } {
  const order = item.order_public_id ? encodeURIComponent(item.order_public_id) : null;
  if (order && (item.kind === "review" || item.kind === "duplicate"))
    return { label: "Review", target: `billing/orders?order=${order}` };
  if (item.payment_id !== null) return { label: "Open payment", target: `billing/payments?payment=${item.payment_id}` };
  if (order) return { label: "Open order", target: `billing/orders?order=${order}` };
  if (item.kind === "paused_orders") return { label: "Open orders", target: "billing/orders" };
  return { label: "Set up payments", target: "billing/business" };
}

/** Plan colours: shades of the accent from light to full, by rank (plans-billing-visual.md 2.1). */
function shade(index: number, count: number): CSSProperties {
  const mix = count <= 1 ? 100 : Math.round(45 + (55 * index) / (count - 1));
  return { "--admin-shade": `${mix}%` } as CSSProperties;
}

type BillingOverviewProps = {
  month: string;
  config: Resource<BillingAdminConfig>;
  onNavigate: (hash: string) => void;
  onRecord: () => void;
  /** Bumped by the parent after a bank transfer is recorded. */
  version: number;
};

export default function BillingOverview({ month, config, onNavigate, onRecord, version }: BillingOverviewProps) {
  const summary = useResource<BillingSummary>(billingSummaryPath(month), "Couldn't load this month's billing.");
  const recent = useResource<Page<AdminPayment>>(
    paymentsPath({ month, userId: null, status: null, page: 1 }),
    "Couldn't load the recent payments.",
  );
  const [open, setOpen] = useState<AdminPayment | null>(null);
  const flash = useFlash();
  const firstVersion = useRef(version);
  const { reload: reloadSummary } = summary;
  const { reload: reloadRecent } = recent;

  useEffect(() => {
    if (version === firstVersion.current) return;
    void reloadSummary();
    void reloadRecent();
  }, [version, reloadSummary, reloadRecent]);

  const setup = config.data?.payments ?? null;
  return (
    <div className="admin-stack">
      {setup?.mode === "test" ? (
        <div className="ui-notice ui-notice-warning">
          <FlaskIcon size={15} />
          <span className="ui-notice-text">
            <b>Test mode.</b> Payments use Razorpay's test keys; no real money moves.{" "}
            {formatNumber(setup.test_links_created)} of about {TEST_LINK_CAP} test payment links used.
          </span>
          <button type="button" className="btn btn-sm" onClick={() => onNavigate("billing/business")}>
            Going-live checklist
          </button>
        </div>
      ) : null}
      {setup && !setup.configured ? (
        <div className="ui-notice">
          <InfoIcon size={15} />
          <span className="ui-notice-text">
            Online payment isn't set up yet: users can't pay until the Razorpay keys are in .env.
          </span>
          <button type="button" className="btn btn-sm" onClick={() => onNavigate("billing/business")}>
            Set up payments
          </button>
        </div>
      ) : null}
      <FlashMessage flash={flash} />
      <ResourceGate resource={summary} loadingLabel="Loading this month">
        {(data) => (
          <>
            <Attention
              items={setup ? data.attention.filter((item) => !NOTICE_KINDS.has(item.kind)) : data.attention}
              onNavigate={onNavigate}
            />
            <Tiles summary={data} feeBp={config.data?.estimate.fee_bp ?? null} />
            <div className="admin-billing-grid">
              <ByPlan summary={data} />
              <Months summary={data} />
            </div>
            <div className="admin-billing-grid admin-billing-grid-even">
              <UserList
                title="Expiring in 7 days"
                subtitle="Valid through the next 7 days, with nothing after."
                rows={data.expiring}
                onNavigate={onNavigate}
              />
              <UserList
                title="Lapsed in the last 30 days"
                subtitle="Their plan ended; their data is kept."
                rows={data.lapsed}
                onNavigate={onNavigate}
              />
            </div>
          </>
        )}
      </ResourceGate>
      <section className="card admin-card" aria-labelledby="admin-recent-payments-title">
        <div className="admin-card-header">
          <div className="admin-card-heading">
            <h2 id="admin-recent-payments-title" className="admin-card-title">
              Recent payments
            </h2>
            <p className="admin-card-subtitle">
              The latest five this month. Payments has every one, with refunds and receipts.
            </p>
          </div>
          <span className="spacer" />
          <button type="button" className="btn" onClick={onRecord}>
            <BankIcon size={16} />
            Record bank transfer
          </button>
          <a className="btn admin-export" href={billingExportUrl(month)} download>
            <DownloadIcon size={16} />
            Export CSV
          </a>
        </div>
        <ResourceGate resource={recent} loadingLabel="Loading payments">
          {(page) =>
            page.items.length === 0 ? (
              <EmptyState title="No payments this month" />
            ) : (
              <PaymentsTable payments={page.items.slice(0, RECENT)} onOpen={setOpen} />
            )
          }
        </ResourceGate>
      </section>
      {open ? (
        <PaymentDialog
          payment={open}
          onClose={() => setOpen(null)}
          onChanged={(updated, message) => {
            setOpen(updated);
            recent.setData(
              (page) => page && { ...page, items: page.items.map((p) => (p.id === updated.id ? updated : p)) },
            );
            void summary.reload();
            flash.success(message);
          }}
        />
      ) : null}
    </div>
  );
}

function Attention({ items, onNavigate }: { items: AttentionItem[]; onNavigate: (hash: string) => void }) {
  if (items.length === 0) return null;
  return (
    <section className="card admin-card" aria-labelledby="admin-attention-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-attention-title" className="admin-card-title">
            Needs attention
            <span className="badge badge-warning admin-title-count">
              {formatNumber(items.length)}
              <span className="sr-only"> {items.length === 1 ? "item" : "items"}</span>
            </span>
          </h2>
          <p className="admin-card-subtitle">
            Payments and settings to look at. Each item clears once it's dealt with.
          </p>
        </div>
      </div>
      <ul className="admin-attention-list">
        {items.map((item, index) => {
          const tone = DANGER.has(item.kind) ? "danger" : INFO.has(item.kind) ? "info" : "warning";
          const action = attentionAction(item);
          return (
            <li
              key={`${item.kind}-${item.order_public_id ?? ""}-${item.payment_id ?? ""}-${index}`}
              className={`admin-attention-row admin-attention-${tone}`}
            >
              {tone === "danger" ? (
                <AlertCircleIcon size={18} />
              ) : tone === "info" ? (
                <InfoIcon size={18} />
              ) : (
                <AlertIcon size={18} />
              )}
              <span className="admin-attention-text">
                {item.message}
                {item.detail ? <small>{item.detail}</small> : null}
              </span>
              <button type="button" className="btn btn-sm" onClick={() => onNavigate(action.target)}>
                {action.label}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Tile({ label, value, sub, className }: { label: string; value: string; sub: string; className?: string }) {
  return (
    <div className={className ? `admin-tile ${className}` : "admin-tile"}>
      <dt className="admin-tile-label">{label}</dt>
      <dd className="admin-tile-value">{value}</dd>
      <dd className="admin-tile-sub">{sub}</dd>
    </div>
  );
}

function Tiles({ summary, feeBp }: { summary: BillingSummary; feeBp: number | null }) {
  return (
    <dl className="admin-tiles admin-tiles-billing" aria-label={`Totals for ${monthLabel(summary.month)}`}>
      <Tile label="Gross" value={formatInr(summary.gross_paise)} sub={plural(summary.payments, "payment")} />
      <Tile
        label="Razorpay fees"
        value={formatInr(summary.fee_paise)}
        sub={feeBp === null ? "with GST on the fee" : `${(feeBp / 100).toFixed(2)}% with GST on the fee`}
      />
      <Tile
        label="Refunds"
        value={formatInr(summary.refunded_paise)}
        sub={summary.refunds ? plural(summary.refunds, "refund") : "None"}
      />
      <Tile
        label="Disputes lost"
        value={formatInr(summary.disputed_paise)}
        sub={summary.disputed_paise ? "Lost this month" : "None"}
      />
      <Tile label="Net" value={formatInr(summary.net_paise)} sub="what you keep" className="admin-tile-net" />
    </dl>
  );
}

function ByPlan({ summary }: { summary: BillingSummary }) {
  const names = [
    ...new Set([...summary.by_plan.map((row) => row.plan), ...summary.current_users.map((row) => row.plan)]),
  ];
  const rows = names.map((plan) => ({
    plan,
    sold: summary.by_plan.find((row) => row.plan === plan),
    users: summary.current_users.find((row) => row.plan === plan),
  }));
  const counts = rows.map(({ users }) => (users ? users.paid + users.complimentary : 0));
  const total = counts.reduce((sum, count) => sum + count, 0) + summary.users_without_plan;
  return (
    <section className="card admin-card" aria-labelledby="admin-by-plan-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-by-plan-title" className="admin-card-title">
            By plan
          </h2>
          <p className="admin-card-subtitle">Payments this month and who is on each plan now.</p>
        </div>
      </div>
      {rows.length === 0 ? (
        <EmptyState title="No plans sold or held yet" />
      ) : (
        <div className="table-wrap">
          <table className="table admin-table">
            <thead>
              <tr>
                <th scope="col">Plan</th>
                <th scope="col" className="num">
                  Payments
                </th>
                <th scope="col" className="num">
                  Gross
                </th>
                <th scope="col" className="num">
                  Users now
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ plan, sold, users }, index) => (
                <tr key={plan}>
                  <td>
                    <span className="admin-plan-dot" style={shade(index, rows.length)} aria-hidden="true" />
                    <b className="admin-strong">{plan}</b>
                  </td>
                  <td className="num">{formatNumber(sold?.payments ?? 0)}</td>
                  <td className="num">{formatInr(sold?.gross_paise ?? 0)}</td>
                  <td className="num admin-nowrap">
                    {formatNumber(users?.paid ?? 0)} <span className="admin-subtext">paid</span>
                    {users?.complimentary ? (
                      <>
                        {" "}
                        · {formatNumber(users.complimentary)} <span className="admin-subtext">free</span>
                      </>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="admin-plan-mix">
        <div className="admin-plan-mix-head">
          <span>Users now</span>
          <b>{formatNumber(total)}</b>
        </div>
        {total > 0 ? (
          <div className="admin-plan-mix-bar" aria-hidden="true">
            {rows.map(({ plan }, index) =>
              counts[index] ? (
                <span key={plan} style={{ ...shade(index, rows.length), width: `${(counts[index] / total) * 100}%` }} />
              ) : null,
            )}
            {summary.users_without_plan ? (
              <span
                className="admin-plan-mix-free"
                style={{ width: `${(summary.users_without_plan / total) * 100}%` }}
              />
            ) : null}
          </div>
        ) : null}
        <ul className="admin-plan-mix-legend">
          {rows.map(({ plan }, index) => (
            <li key={plan}>
              <span className="admin-plan-dot" style={shade(index, rows.length)} aria-hidden="true" />
              {plan} {formatNumber(counts[index])}
            </li>
          ))}
          <li>
            <span className="admin-plan-dot admin-plan-dot-free" aria-hidden="true" />
            No plan {formatNumber(summary.users_without_plan)}
          </li>
        </ul>
      </div>
      <p className="admin-card-footnote">
        “Free” counts complimentary plans you granted. Months are in Indian time (IST).
      </p>
    </section>
  );
}

function Months({ summary }: { summary: BillingSummary }) {
  const largest = Math.max(1, ...summary.months.map((row) => row.gross_paise));
  return (
    <section className="card admin-card" aria-labelledby="admin-months-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-months-title" className="admin-card-title">
            Last 12 months
          </h2>
          <p className="admin-card-subtitle">Gross per month.</p>
        </div>
      </div>
      <ol className="admin-months">
        {summary.months.map((row, index) => {
          const [year, monthNumber] = row.month.split("-");
          const short = MONTHS[Number(monthNumber) - 1] ?? row.month;
          const label = index === 0 || monthNumber === "01" ? `${short} ${year}` : short;
          return (
            <li key={row.month} className={row.month === summary.month ? "admin-month-now" : undefined}>
              <span className="admin-month-label" aria-hidden="true">
                {label}
              </span>
              <span className="sr-only">{monthLabel(row.month)}:</span>
              <span className="admin-bar" aria-hidden="true">
                <span style={{ width: `${(row.gross_paise / largest) * 100}%` }} />
              </span>
              <span className="admin-bar-value">{formatInr(row.gross_paise)}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

type UserListProps = { title: string; subtitle: string; rows: UserPlanRow[]; onNavigate: (hash: string) => void };

function UserList({ title, subtitle, rows, onNavigate }: UserListProps) {
  const titleId = `admin-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section className="card admin-card" aria-labelledby={titleId}>
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id={titleId} className="admin-card-title">
            {title}
          </h2>
          <p className="admin-card-subtitle">{subtitle}</p>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="admin-expiring-empty">Nobody</p>
      ) : (
        <ul className="admin-expiring">
          {rows.map((row) => (
            <UserListRow key={`${row.user.id ?? row.user.username}-${row.date}`} row={row} onNavigate={onNavigate} />
          ))}
        </ul>
      )}
    </section>
  );
}

function UserListRow({ row, onNavigate }: { row: UserPlanRow; onNavigate: (hash: string) => void }): ReactNode {
  const { user } = row;
  return (
    <li className="admin-expiring-row">
      <Avatar name={user.username} />
      <span className="admin-expiring-text">
        <b>{user.username}</b>
        <span className="admin-subtext">
          {row.plan ?? "No plan"} · {shortIstDate(row.date)} ·{" "}
          {row.last_payment_at ? `last paid ${shortIstDate(row.last_payment_at)}` : "never paid"}
        </span>
      </span>
      {user.id !== null ? (
        <button
          type="button"
          className="btn btn-ghost btn-xs"
          aria-label={`Open ${user.username}`}
          onClick={() => onNavigate(`users?user=${user.id}`)}
        >
          Open
        </button>
      ) : null}
    </li>
  );
}
