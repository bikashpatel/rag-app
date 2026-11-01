import { useState } from "react";
import type { AdminOrder, BillingAdminConfig, Page } from "../../api/types";
import { formatInr, formatUtr } from "../../lib/billing";
import { formatNumber, formatRelativeTime } from "../../lib/format";
import { istDayTime } from "../billing/planText";
import { useMediaQuery } from "../chat/useMediaQuery";
import { ShieldIcon } from "../ui/icons";
import { ordersPath } from "./adminApi";
import { OrderDialog } from "./BillingOrders";
import { CopyButton, OrderStatusBadge, shortIstDate, UserCell } from "./billingUi";
import FlashMessage from "./FlashMessage";
import { useFlash, useResource, type Resource } from "./hooks";
import { EmptyState, ResourceGate } from "./StateViews";
import { decidable, REJECT_REASONS, UpiDecisionDialog, type UpiDecision, type UpiOrder } from "./UpiDecision";

const DAY_MS = 86_400_000;
const RECENT = 20;
const PHONE_QUERY = "(max-width: 799.98px)";

function Sent({ at }: { at: string }) {
  const late = Date.now() - Date.parse(at) > DAY_MS;
  return (
    <span className="admin-upi-stack">
      <time dateTime={at}>{istDayTime(at)}</time>
      {late ? (
        <span className="admin-upi-late">Waiting over a day</span>
      ) : (
        <span className="admin-subtext">{formatRelativeTime(at)}</span>
      )}
    </span>
  );
}

type RowActionsProps = { order: UpiOrder; onDecide: (decision: UpiDecision) => void };

function RowActions({ order, onDecide }: RowActionsProps) {
  const what = `${formatInr(order.amount_paise)} from ${order.user.username}`;
  return (
    <>
      <button
        type="button"
        className="btn btn-sm btn-primary"
        aria-label={`Confirm ${what}`}
        onClick={() => onDecide({ order, action: "confirm" })}
      >
        Confirm…
      </button>
      <button
        type="button"
        className="btn btn-sm"
        aria-label={`Reject ${what}`}
        onClick={() => onDecide({ order, action: "reject" })}
      >
        Reject…
      </button>
    </>
  );
}

type ListProps = { orders: UpiOrder[]; currentUpiId: string | null; onDecide: (decision: UpiDecision) => void };

function ToCheckTable({ orders, currentUpiId, onDecide }: ListProps) {
  return (
    <div className="table-wrap">
      <table className="table admin-table">
        <thead>
          <tr>
            <th scope="col">Sent</th>
            <th scope="col">User</th>
            <th scope="col">Plan</th>
            <th scope="col" className="num">
              Amount
            </th>
            <th scope="col">Reference</th>
            <th scope="col">UTR</th>
            <th scope="col" className="admin-actions-cell">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.public_id}>
              <td className="admin-nowrap">
                {order.upi.submitted_at ? <Sent at={order.upi.submitted_at} /> : "—"}
              </td>
              <td>
                <UserCell user={order.user} />
              </td>
              <td>
                <span className="admin-upi-stack">
                  <b className="admin-strong">{order.plan.name}</b>
                  <span className="admin-subtext">{order.kind === "now" ? "Starts when confirmed" : "30 days"}</span>
                </span>
              </td>
              <td className="num admin-nowrap admin-strong">{formatInr(order.amount_paise)}</td>
              <td>
                <span className="admin-upi-stack">
                  <code className="admin-code">{order.upi.reference}</code>
                  {currentUpiId !== null && order.upi.upi_id !== currentUpiId ? (
                    <span className="admin-subtext">to {order.upi.upi_id}</span>
                  ) : null}
                </span>
              </td>
              <td>
                <span className="admin-copy-row admin-nowrap">
                  <code className="admin-code">{formatUtr(order.upi.utr ?? "")}</code>
                  <CopyButton value={order.upi.utr ?? ""} label={`Copy the UTR of ${order.user.username}`} />
                </span>
              </td>
              <td className="admin-actions-cell">
                <span className="admin-actions admin-upi-actions">
                  <RowActions order={order} onDecide={onDecide} />
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ToCheckCards({ orders, currentUpiId, onDecide }: ListProps) {
  return (
    <ul className="admin-upi-cards">
      {orders.map((order) => (
        <li key={order.public_id} className="admin-upi-row">
          <div className="admin-upi-row-head">
            <span className="admin-upi-row-amount">{formatInr(order.amount_paise)}</span>
            <span className="admin-subtext">
              {order.plan.name} · {order.kind === "now" ? "Starts when confirmed" : "30 days"}
            </span>
          </div>
          <UserCell user={order.user} />
          <dl className="admin-upi-row-facts">
            <div>
              <dt>Reference</dt>
              <dd>
                <span className="admin-upi-stack">
                  <code className="admin-code">{order.upi.reference}</code>
                  {currentUpiId !== null && order.upi.upi_id !== currentUpiId ? (
                    <span className="admin-subtext">to {order.upi.upi_id}</span>
                  ) : null}
                </span>
                <CopyButton value={order.upi.reference} label={`Copy the reference of ${order.user.username}`} />
              </dd>
            </div>
            <div>
              <dt>UTR</dt>
              <dd>
                <code className="admin-code">{formatUtr(order.upi.utr ?? "")}</code>
                <CopyButton value={order.upi.utr ?? ""} label={`Copy the UTR of ${order.user.username}`} />
              </dd>
            </div>
            <div>
              <dt>Sent</dt>
              <dd>{order.upi.submitted_at ? <Sent at={order.upi.submitted_at} /> : "—"}</dd>
            </div>
          </dl>
          <div className="admin-upi-row-actions">
            <RowActions order={order} onDecide={onDecide} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function RecentStatus({ order }: { order: AdminOrder }) {
  const rejection = order.upi?.rejection;
  return (
    <span className="admin-upi-stack">
      <span className="admin-badges">
        <OrderStatusBadge status={order.status} channel={order.channel} />
      </span>
      {order.status === "rejected" && rejection ? (
        <span className="admin-subtext">{REJECT_REASONS[rejection.reason].short}</span>
      ) : null}
    </span>
  );
}

type RecentProps = { orders: AdminOrder[]; onOpen: (publicId: string) => void };

function OpenOrder({ order, onOpen }: { order: AdminOrder; onOpen: (publicId: string) => void }) {
  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm"
      aria-label={`Open the ${order.plan.name} order of ${order.user.username}`}
      onClick={() => onOpen(order.public_id)}
    >
      Open
    </button>
  );
}

function RecentTable({ orders, onOpen }: RecentProps) {
  return (
    <div className="table-wrap">
      <table className="table admin-table">
        <thead>
          <tr>
            <th scope="col">Created</th>
            <th scope="col">User</th>
            <th scope="col">Plan</th>
            <th scope="col" className="num">
              Amount
            </th>
            <th scope="col">Status</th>
            <th scope="col" className="admin-actions-cell">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={order.public_id}>
              <td className="admin-nowrap">
                <time dateTime={order.created_at}>{shortIstDate(order.created_at)}</time>
              </td>
              <td>
                <UserCell user={order.user} />
              </td>
              <td>{order.plan.name}</td>
              <td className="num admin-nowrap">{formatInr(order.amount_paise)}</td>
              <td>
                <RecentStatus order={order} />
              </td>
              <td className="admin-actions-cell">
                <span className="admin-actions">
                  <OpenOrder order={order} onOpen={onOpen} />
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Phones: the to-check list's cards, so the status is never under the Open column. */
function RecentCards({ orders, onOpen }: RecentProps) {
  return (
    <ul className="admin-upi-cards">
      {orders.map((order) => (
        <li key={order.public_id} className="admin-upi-row">
          <div className="admin-upi-row-head">
            <span className="admin-upi-row-amount">{formatInr(order.amount_paise)}</span>
            <span className="admin-subtext">
              {order.plan.name} · <time dateTime={order.created_at}>{shortIstDate(order.created_at)}</time>
            </span>
          </div>
          <UserCell user={order.user} />
          <div className="admin-upi-row-foot">
            <RecentStatus order={order} />
            <OpenOrder order={order} onOpen={onOpen} />
          </div>
        </li>
      ))}
    </ul>
  );
}

type BillingUpiProps = {
  /** GET /admin/billing/orders?status=submitted: the UTRs to check, the oldest first (also the section's count). */
  toCheck: Resource<Page<AdminOrder>>;
  config: Resource<BillingAdminConfig>;
  onNavigate: (hash: string) => void;
};

/** Admin → Billing → UPI (docs/briefs/upi-direct.md 10.5): the UTRs to check with Confirm and Reject, the latest. */
export default function BillingUpi({ toCheck, config, onNavigate }: BillingUpiProps) {
  const recent = useResource<Page<AdminOrder>>(ordersPath(null, 1, "upi_direct"), "Couldn't load the UPI payments.");
  const [deciding, setDeciding] = useState<UpiDecision | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const flash = useFlash();
  const phone = useMediaQuery(PHONE_QUERY);
  const settings = config.data?.settings ?? null;
  const count = toCheck.data?.total ?? 0;

  function changed(message: string) {
    flash.success(message);
    void toCheck.reload();
    void recent.reload();
  }

  return (
    <div className="admin-stack">
      <section className="card admin-card" aria-labelledby="admin-upi-title">
        <div className="admin-card-header">
          <div className="admin-card-heading">
            <h2 id="admin-upi-title" className="admin-card-title">
              UPI payments to check
              {count > 0 ? (
                <span className="badge badge-warning admin-title-count">
                  {formatNumber(count)}
                  <span className="sr-only"> waiting</span>
                </span>
              ) : null}
            </h2>
            <p className="admin-card-subtitle">Customers who paid your UPI ID and sent the payment's UTR.</p>
          </div>
        </div>
        <div className="ui-notice ui-notice-warning admin-inline-notice admin-upi-notice">
          <ShieldIcon size={15} />
          <span className="ui-notice-text">
            Before you confirm, open your bank or UPI app and find a credit of <b>exactly the amount</b> with{" "}
            <b>this UTR</b>. If your app shows the payment's note, it should be the reference. Confirming starts the
            plan and issues a receipt.
          </span>
        </div>
        <FlashMessage flash={flash} />
        <ResourceGate resource={toCheck} loadingLabel="Loading the UPI payments to check">
          {(data) => {
            const orders = data.items.filter(decidable);
            if (orders.length === 0) {
              return settings && !settings.upi_enabled ? (
                <p className="admin-upi-empty">
                  Direct UPI is off.{" "}
                  <button type="button" className="admin-link-button" onClick={() => onNavigate("billing/business")}>
                    Turn it on in Business & payments.
                  </button>
                </p>
              ) : (
                <p className="admin-upi-empty">
                  Nothing to check. New UPI payments show up here and in Needs attention.
                </p>
              );
            }
            const list = { orders, currentUpiId: settings?.upi_id ?? null, onDecide: setDeciding };
            return phone ? <ToCheckCards {...list} /> : <ToCheckTable {...list} />;
          }}
        </ResourceGate>
      </section>

      <section className="card admin-card" aria-labelledby="admin-upi-recent-title">
        <div className="admin-card-header">
          <div className="admin-card-heading">
            <h2 id="admin-upi-recent-title" className="admin-card-title">
              Recent UPI payments
            </h2>
            <p className="admin-card-subtitle">The {RECENT} newest direct-UPI orders, whatever their status.</p>
          </div>
        </div>
        <ResourceGate resource={recent} loadingLabel="Loading the recent UPI payments">
          {(data) => {
            if (data.items.length === 0) return <EmptyState title="No UPI payments yet" />;
            const list = { orders: data.items.slice(0, RECENT), onOpen: setOpen };
            return phone ? <RecentCards {...list} /> : <RecentTable {...list} />;
          }}
        </ResourceGate>
      </section>

      {open ? (
        <OrderDialog
          publicId={open}
          onClose={() => setOpen(null)}
          onChanged={(_, message) => changed(message)}
          onGrant={(userId) => {
            setOpen(null);
            onNavigate(`users?user=${userId}&grant=1`);
          }}
          onDecide={(decision) => {
            setOpen(null);
            setDeciding(decision);
          }}
        />
      ) : null}
      {deciding ? (
        <UpiDecisionDialog
          decision={deciding}
          contact={settings?.contact_email ?? null}
          onClose={() => setDeciding(null)}
          onDone={(_, message) => {
            setDeciding(null);
            changed(message);
          }}
        />
      ) : null}
    </div>
  );
}
