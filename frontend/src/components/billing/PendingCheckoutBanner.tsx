import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, billingApi } from "../../api/client";
import type { PendingOrder } from "../../api/types";
import { isRazorpayUrl } from "../../lib/billing";
import { ArrowUpRightIcon, InfoIcon } from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";
import "./billing.css";
import { returnPath } from "./planText";

/** The chat's banner while a checkout is open (8.7): it goes when the order settles, so it has no dismiss. */
export default function PendingCheckoutBanner({ order }: { order: PendingOrder }) {
  return (
    <div className="ui-notice ui-notice-info chat-notice billing-banner" role="status">
      <InfoIcon size={15} />
      <span className="ui-notice-text">
        A payment for <b>{order.plan.name}</b> is in progress.
      </span>
      <Link className="btn btn-sm" to={returnPath(order.public_id)}>
        Check status
      </Link>
    </div>
  );
}

type NoticeProps = {
  order: PendingOrder;
  /** The checkout was cancelled: reload what shows it. */
  onCancelled: () => void;
};

/** The same, on the plan page and in Settings → Plan & billing: Check status, Resume payment and Cancel (3.4). */
export function PendingCheckoutNotice({ order, onCancelled }: NoticeProps) {
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resume = order.pay_url && isRazorpayUrl(order.pay_url, true) ? order.pay_url : null;

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
      <InfoIcon size={15} />
      <span className="ui-notice-text">
        A payment for <b>{order.plan.name}</b> is in progress.
        {error ? <span className="billing-notice-error">{error}</span> : null}
      </span>
      <span className="billing-notice-actions">
        <Link className="btn btn-sm" to={returnPath(order.public_id)}>
          Check status
        </Link>
        {resume ? (
          <a className="btn btn-sm btn-ghost" href={resume}>
            Resume payment
            <ArrowUpRightIcon size={15} />
          </a>
        ) : null}
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          aria-disabled={cancelling || undefined}
          onClick={() => void cancel()}
        >
          {cancelling ? <ButtonSpinner size={12} /> : null}
          Cancel
        </button>
      </span>
    </div>
  );
}
