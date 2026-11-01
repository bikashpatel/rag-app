import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, billingApi } from "../../api/client";
import type { OrderView, PendingOrder, RejectedOrder } from "../../api/types";
import { formatInr, isRazorpayUrl } from "../../lib/billing";
import { AlertCircleIcon, ArrowUpRightIcon, ClockIcon, CloseIcon, InfoIcon } from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";
import "./billing.css";
import { returnPath } from "./planText";

type Open = PendingOrder | OrderView;

/** A direct-UPI order whose UTR waits for the admin's check (docs/briefs/upi-direct.md 10.3). */
const isChecking = (order: Open) => order.channel === "upi_direct" && order.status === "submitted";

function PendingText({ order }: { order: Open }) {
  if (order.channel !== "upi_direct") {
    return (
      <>
        A payment for <b>{order.plan.name}</b> is in progress.
      </>
    );
  }
  if (isChecking(order)) {
    return (
      <>
        We're checking your UPI payment of {formatInr(order.amount_paise)} for <b>{order.plan.name}</b>.
      </>
    );
  }
  return (
    <>
      Finish your UPI payment for <b>{order.plan.name}</b>: send its UTR.
    </>
  );
}

/** "Check status" for a Razorpay checkout; "Continue" to the UPI panel, or "Details" while the owner checks it. */
function OpenLink({ order }: { order: Open }) {
  const label = order.channel !== "upi_direct" ? "Check status" : isChecking(order) ? "Details" : "Continue";
  return (
    <Link className="btn btn-sm" to={returnPath(order.public_id)}>
      {label}
    </Link>
  );
}

/** The chat's banner while a checkout is open (8.7): it goes when the order settles, so it has no dismiss. */
export default function PendingCheckoutBanner({ order }: { order: Open }) {
  return (
    <div className="ui-notice ui-notice-info chat-notice billing-banner" role="status">
      {isChecking(order) ? <ClockIcon size={15} /> : <InfoIcon size={15} />}
      <span className="ui-notice-text">
        <PendingText order={order} />
      </span>
      <OpenLink order={order} />
    </div>
  );
}

type NoticeProps = {
  order: Open;
  /** The checkout was cancelled: reload what shows it. */
  onCancelled: () => void;
};

/**
 * The same, on the plan page and in Settings → Plan & billing: Check status, Resume payment and Cancel (3.4); a
 * direct-UPI order has Continue and Cancel, and only Details once its UTR waits for the owner (it can't be cancelled).
 */
export function PendingCheckoutNotice({ order, onCancelled }: NoticeProps) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resume = order.pay_url && isRazorpayUrl(order.pay_url, true) ? order.pay_url : null;
  const checking = isChecking(order);

  async function cancel() {
    if (cancelling) return;
    setCancelling(true);
    setError(null);
    try {
      await billingApi.cancel(order.public_id);
      onCancelled();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't cancel the payment. Please try again.");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="ui-notice ui-notice-info" role="status">
      {checking ? <ClockIcon size={15} /> : <InfoIcon size={15} />}
      <span className="ui-notice-text">
        <PendingText order={order} />
        {error ? <span className="billing-notice-error">{error}</span> : null}
      </span>
      <span className="billing-notice-actions">
        <OpenLink order={order} />
        {resume ? (
          <a className="btn btn-sm btn-ghost" href={resume}>
            Resume payment
            <ArrowUpRightIcon size={15} />
          </a>
        ) : null}
        {checking ? null : (
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            aria-disabled={cancelling || undefined}
            onClick={() => void cancel()}
          >
            {cancelling ? <ButtonSpinner size={12} /> : null}
            Cancel
          </button>
        )}
      </span>
    </div>
  );
}

/** The chat banner's dismissal lasts for the browser session, per rejected order. */
const SEEN_KEY = "rag.upiRejectedSeen";

function seenId(): string | null {
  try {
    return sessionStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

function RejectedText({ order }: { order: RejectedOrder }) {
  return (
    <>
      We couldn't confirm your UPI payment of {formatInr(order.amount_paise)} for <b>{order.plan.name}</b>.
    </>
  );
}

/** Me.plan.rejected_order on /plans and in Settings: always shown while the server reports it (10.3). */
export function RejectedOrderNotice({ order }: { order: RejectedOrder }) {
  return (
    <div className="ui-notice ui-notice-danger" role="status">
      <AlertCircleIcon size={15} />
      <span className="ui-notice-text">
        <RejectedText order={order} />
      </span>
      <span className="billing-notice-actions">
        <Link className="btn btn-sm" to={returnPath(order.public_id)}>
          See why
        </Link>
      </span>
    </div>
  );
}

/** The same under the chat's top bar, dismissible for the session. */
export function RejectedOrderBanner({ order }: { order: RejectedOrder }) {
  const [seen, setSeen] = useState(() => seenId() === order.public_id);
  if (seen) return null;
  return (
    <div className="ui-notice ui-notice-danger chat-notice billing-banner" role="status">
      <AlertCircleIcon size={15} />
      <span className="ui-notice-text">
        <RejectedText order={order} />
      </span>
      <Link className="btn btn-sm" to={returnPath(order.public_id)}>
        See why
      </Link>
      <button
        type="button"
        className="btn btn-ghost btn-icon btn-sm billing-banner-dismiss"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={() => {
          try {
            sessionStorage.setItem(SEEN_KEY, order.public_id);
          } catch {
            /* storage unavailable: dismissed until the page reloads */
          }
          setSeen(true);
        }}
      >
        <CloseIcon size={15} />
      </button>
    </div>
  );
}
