import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { AdminOrder, AdminOrderDetail, OrderStatus, Page } from "../../api/types";
import { formatInr, formatIstDate } from "../../lib/billing";
import { formatNumber, formatRelativeTime } from "../../lib/format";
import Modal from "../ui/Modal";
import SegmentedControl from "../ui/SegmentedControl";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, RefreshIcon } from "../ui/icons";
import { checkOrder, errorMessage, getOrder, ordersPath, PAGE_SIZE, resolveOrder } from "./adminApi";
import {
  Fact,
  istDateTime,
  kindLabel,
  OrderStatusBadge,
  Pagination,
  plural,
  shortIstDate,
  TestBadge,
  UserCell,
} from "./billingUi";
import { Field } from "./Field";
import FlashMessage from "./FlashMessage";
import { FormValidator } from "./form";
import { useFlash, useResource } from "./hooks";
import { EmptyState, ErrorState, LoadingState, ResourceGate } from "./StateViews";

type StatusFilter = "all" | OrderStatus;

/** "3 checks · next in 30 seconds" */
function checksText(order: AdminOrder): string {
  const next = order.status === "pending" || order.status === "expired" ? order.next_check_at : null;
  return [plural(order.checks, "check"), next ? `next ${formatRelativeTime(next)}` : null].filter(Boolean).join(" · ");
}

type BillingOrdersProps = {
  /** From the hash: an order to open. */
  orderParam: string | null;
  onParamsRead: () => void;
  onGrant: (userId: number) => void;
};

export default function BillingOrders({ orderParam, onParamsRead, onGrant }: BillingOrdersProps) {
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const flash = useFlash();
  const orders = useResource<Page<AdminOrder>>(
    ordersPath(status === "all" ? null : status, page),
    "Couldn't load the orders.",
  );
  // `status=review` lists only the orders still to review: a resolved one keeps its status but leaves the count.
  const review = useResource<Page<AdminOrder>>(ordersPath("review", 1), "Couldn't count the orders to review.");
  const reviewCount = review.data?.total ?? 0;

  useEffect(() => {
    if (orderParam === null) return;
    setOpen(orderParam);
    onParamsRead();
  }, [orderParam, onParamsRead]);

  const filters = [
    { value: "all", label: "All" },
    { value: "pending", label: "Pending" },
    { value: "review", label: reviewCount ? `Review ${formatNumber(reviewCount)}` : "Review" },
    { value: "paid", label: "Paid" },
    { value: "expired", label: "Expired" },
    { value: "cancelled", label: "Cancelled" },
  ] as const satisfies readonly { value: StatusFilter; label: string }[];

  function changed(order: AdminOrder, message: string) {
    orders.setData(
      (data) => data && { ...data, items: data.items.map((o) => (o.public_id === order.public_id ? order : o)) },
    );
    void review.reload();
    flash.success(message);
  }

  return (
    <section className="card admin-card" aria-labelledby="admin-orders-title">
      <div className="admin-card-header">
        <div className="admin-card-heading">
          <h2 id="admin-orders-title" className="admin-card-title">
            Orders
          </h2>
          <p className="admin-card-subtitle">
            Every checkout, with how the app checks it with Razorpay. Orders paid with the other kind of keys are paused
            until those keys run again.
          </p>
        </div>
      </div>
      <div className="admin-toolbar">
        <SegmentedControl
          label="Order status"
          size="sm"
          value={status}
          options={filters}
          onChange={(value) => {
            setStatus(value);
            setPage(1);
          }}
        />
      </div>
      <FlashMessage flash={flash} />
      <ResourceGate resource={orders} loadingLabel="Loading orders">
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState title="No orders">
              <p>Checkouts appear here once users start paying.</p>
            </EmptyState>
          ) : (
            <>
              <div className="table-wrap">
                <table className="table admin-table">
                  <thead>
                    <tr>
                      <th scope="col">Created</th>
                      <th scope="col">User</th>
                      <th scope="col">Plan</th>
                      <th scope="col">Kind</th>
                      <th scope="col" className="num">
                        Amount
                      </th>
                      <th scope="col">Status</th>
                      <th scope="col">Checks</th>
                      <th scope="col">Last error</th>
                      <th scope="col" className="admin-actions-cell">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((order) => (
                      <tr key={order.public_id}>
                        <td className="admin-nowrap">
                          <time dateTime={order.created_at}>{shortIstDate(order.created_at)}</time>
                        </td>
                        <td>
                          <UserCell user={order.user} />
                        </td>
                        <td>{order.plan.name}</td>
                        <td>{kindLabel(order.kind)}</td>
                        <td className="num admin-nowrap">
                          {formatInr(order.amount_paise)}
                          {order.test_mode ? (
                            <>
                              {" "}
                              <TestBadge />
                            </>
                          ) : null}
                        </td>
                        <td>
                          <span className="admin-badges">
                            <OrderStatusBadge status={order.status} />
                            {order.status === "review" && order.review_note ? (
                              <span className="badge badge-neutral">Resolved</span>
                            ) : null}
                            {order.paused ? <span className="badge badge-neutral">Paused</span> : null}
                          </span>
                        </td>
                        <td className="admin-subtext admin-nowrap">
                          {order.mode === "offline" ? "Bank transfer" : checksText(order)}
                        </td>
                        <td className="admin-last-error" title={order.last_error ?? undefined}>
                          {order.last_error ?? ""}
                        </td>
                        <td className="admin-actions-cell">
                          <span className="admin-actions">
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              aria-label={`Open the ${order.plan.name} order of ${order.user.username}`}
                              onClick={() => setOpen(order.public_id)}
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
              <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} label="Orders pages" onPage={setPage} />
            </>
          )
        }
      </ResourceGate>
      {open ? (
        <OrderDialog
          publicId={open}
          onClose={() => setOpen(null)}
          onChanged={changed}
          onGrant={(userId) => {
            setOpen(null);
            onGrant(userId);
          }}
        />
      ) : null}
    </section>
  );
}

const MODES: Record<AdminOrder["mode"], string> = { live: "Live", test: "Test keys", offline: "Bank transfer" };

/** A detail value as a short "key=value" chip. */
function chip(key: string, value: unknown): string {
  return `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`;
}

type OrderDialogProps = {
  publicId: string;
  onClose: () => void;
  onChanged: (order: AdminOrder, message: string) => void;
  onGrant: (userId: number) => void;
};

/** One order: its facts, the events timeline, Check now, and for a review Mark resolved or Grant instead. */
export function OrderDialog({ publicId, onClose, onChanged, onGrant }: OrderDialogProps) {
  const [detail, setDetail] = useState<AdminOrderDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"check" | "resolve" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setDetail(await getOrder(publicId, signal));
        setLoadError(null);
      } catch (err) {
        if (signal?.aborted) return;
        setLoadError(errorMessage(err, "Couldn't load the order."));
      }
    },
    [publicId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function check() {
    setBusy("check");
    setActionError(null);
    try {
      const order = await checkOrder(publicId);
      await load();
      onChanged(order, "Checked with Razorpay.");
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't check the order. Please try again."));
    } finally {
      setBusy(null);
    }
  }

  async function resolve(note: string) {
    setBusy("resolve");
    setActionError(null);
    try {
      const order = await resolveOrder(publicId, { note });
      setDetail((current) => current && { ...current, ...order });
      setResolving(false);
      onChanged(order, "Marked the order as resolved.");
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't resolve the order. Please try again."));
    } finally {
      setBusy(null);
    }
  }

  const order = detail;
  const review = order?.status === "review" && !order.review_note;
  const footer =
    order && !resolving ? (
      <>
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
        {order.mode !== "offline" ? (
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            aria-busy={busy === "check" || undefined}
            onClick={() => void check()}
          >
            {busy === "check" ? <ButtonSpinner size={14} /> : <RefreshIcon size={16} />}
            Check now
          </button>
        ) : null}
        {review ? (
          <>
            <button
              type="button"
              className="btn"
              disabled={order.user.id === null}
              onClick={() => order.user.id !== null && onGrant(order.user.id)}
            >
              Grant instead
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setResolving(true)}>
              Mark resolved…
            </button>
          </>
        ) : null}
      </>
    ) : null;

  return (
    <Modal
      open
      title={order ? `${order.plan.name} order of ${order.user.username}` : "Order"}
      onClose={onClose}
      width={680}
      footer={footer}
    >
      {!order ? (
        loadError ? (
          <ErrorState message={loadError} onRetry={() => void load()} />
        ) : (
          <LoadingState label="Loading the order" />
        )
      ) : (
        <>
          <dl className="admin-billing-facts">
            <Fact term="Order">
              <code className="admin-code">{order.public_id}</code>
            </Fact>
            <Fact term="Status">
              <span className="admin-badges">
                <OrderStatusBadge status={order.status} />
                {order.paused ? <span className="badge badge-neutral">Paused: the other kind of keys</span> : null}
              </span>
            </Fact>
            <Fact term="Amount">
              {formatInr(order.amount_paise)} · {kindLabel(order.kind)}
              {order.credit_paise > 0 ? ` (after a ${formatInr(order.credit_paise)} credit)` : ""}
            </Fact>
            <Fact term="Mode">{MODES[order.mode]}</Fact>
            <Fact term="Created">{istDateTime(order.created_at)}</Fact>
            {order.paid_at ? (
              <Fact term="Paid">{istDateTime(order.paid_at)}</Fact>
            ) : (
              <Fact term="Link expires">{istDateTime(order.expires_at)}</Fact>
            )}
            {order.valid_through ? <Fact term="Bought">Valid through {formatIstDate(order.valid_through)}</Fact> : null}
            {order.razorpay_link_id ? (
              <Fact term="Razorpay link">
                <code className="admin-code">{order.razorpay_link_id}</code>
              </Fact>
            ) : null}
            {order.mode !== "offline" ? <Fact term="Checks">{checksText(order)}</Fact> : null}
            {order.receipt ? <Fact term="Receipt">{order.receipt.number}</Fact> : null}
            {order.review_reason ? <Fact term="Review">{order.review_reason}</Fact> : null}
            {order.review_note ? <Fact term="Resolved">{order.review_note}</Fact> : null}
            {order.last_payment_error ? <Fact term="Payment error">{order.last_payment_error}</Fact> : null}
            {order.last_error ? (
              <Fact term="Last error">
                <span className="admin-error-text">{order.last_error}</span>
              </Fact>
            ) : null}
          </dl>
          {order.events.length ? (
            <section className="admin-billing-sub" aria-label="Events">
              <h3 className="admin-billing-sub-title">Events</h3>
              <ol className="admin-timeline">
                {order.events.map((event, index) => (
                  <li key={`${event.at}-${index}`}>
                    <time className="admin-timeline-time" dateTime={event.at}>
                      {istDateTime(event.at)}
                    </time>
                    <span className="badge badge-neutral">{event.source}</span>
                    <b>{event.type}</b>
                    {Object.keys(event.detail).length ? (
                      <span className="admin-timeline-detail">
                        {Object.entries(event.detail).map(([key, value]) => (
                          <code key={key} className="admin-code">
                            {chip(key, value)}
                          </code>
                        ))}
                      </span>
                    ) : null}
                    {event.error ? <span className="admin-error-text">{event.error}</span> : null}
                  </li>
                ))}
              </ol>
            </section>
          ) : null}
          {actionError ? (
            <div className="ui-notice ui-notice-danger" role="alert">
              <AlertCircleIcon size={15} />
              <span className="ui-notice-text">{actionError}</span>
            </div>
          ) : null}
          {resolving ? (
            <ResolveForm
              busy={busy === "resolve"}
              onCancel={() => setResolving(false)}
              onResolve={(note) => void resolve(note)}
            />
          ) : null}
        </>
      )}
    </Modal>
  );
}

function ResolveForm({
  busy,
  onCancel,
  onResolve,
}: {
  busy: boolean;
  onCancel: () => void;
  onResolve: (note: string) => void;
}) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | undefined>();
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const v = new FormValidator<"note">();
    const text = v.text("note", note, 1, 500);
    setError(v.errors.note);
    if (v.valid) onResolve(text);
    else ref.current?.focus();
  }

  return (
    <form className="admin-form admin-billing-sub" onSubmit={onSubmit} noValidate aria-label="Mark resolved">
      <Field
        label="Note"
        hint="What you did about it, for the record (a refund, a grant, nothing needed)."
        error={error}
      >
        {(control) => (
          <textarea
            {...control}
            ref={ref}
            className="input admin-textarea"
            rows={3}
            maxLength={500}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        )}
      </Field>
      <div className="admin-form-actions">
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
          {busy ? <ButtonSpinner size={14} /> : null}
          Mark resolved
        </button>
      </div>
    </form>
  );
}
