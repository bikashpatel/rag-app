// Small pieces shared by Admin → Plans, Billing and the plan parts of Users (plans-billing-visual.md 3.3, 9).

import { useEffect, useState, type ReactNode } from "react";
import type { AdminUserPlan, CheckoutKind, OrderStatus, PaymentView, UserRef } from "../../api/types";
import { formatInr, formatIstDate } from "../../lib/billing";
import { formatNumber } from "../../lib/format";
import Avatar from "../ui/Avatar";
import { ButtonSpinner } from "../ui/Spinner";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, ClockIcon, CopyIcon, FlaskIcon, GiftIcon } from "../ui/icons";

const IST_OFFSET_MS = 330 * 60_000;
/** Razorpay allows about this many Payment Links per business in test mode (plans-billing.md 14.3). */
export const TEST_LINK_CAP = 30;

/** "YYYY-MM" of the current month in India (billing months are IST months). */
export function currentIstMonth(now: number = Date.now()): string {
  return new Date(now + IST_OFFSET_MS).toISOString().slice(0, 7);
}

/** "YYYY-MM-DD" of today in India. */
export function istToday(now: number = Date.now()): string {
  return new Date(now + IST_OFFSET_MS).toISOString().slice(0, 10);
}

/** "25 Nov" in the current IST year, else "25 Nov 2025". */
export function shortIstDate(value: string, now: number = Date.now()): string {
  const full = formatIstDate(value);
  const thisYear = ` ${currentIstMonth(now).slice(0, 4)}`;
  return full.endsWith(thisYear) ? full.slice(0, -thisYear.length) : full;
}

/** "26 Oct 2026, 15:02 IST". */
export function istDateTime(iso: string): string {
  const instant = Date.parse(iso);
  if (Number.isNaN(instant)) return "—";
  return `${formatIstDate(iso)}, ${new Date(instant + IST_OFFSET_MS).toISOString().slice(11, 16)} IST`;
}

/** Whole rupees for figures that are estimates ("₹666"). */
export function roundedInr(paise: number): string {
  return formatInr(Math.round(paise / 100) * 100);
}

export function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

const METHODS: Record<string, string> = {
  upi: "UPI",
  card: "Card",
  netbanking: "Netbanking",
  wallet: "Wallet",
  emi: "EMI",
  bank_transfer: "Bank transfer",
};

export function methodLabel(method: string | null): string {
  if (!method) return "—";
  return METHODS[method] ?? method;
}

export function kindLabel(kind: CheckoutKind): string {
  return kind === "now" ? "Now" : "Period";
}

export function plural(count: number, noun: string, many = `${noun}s`): string {
  return `${formatNumber(count)} ${count === 1 ? noun : many}`;
}

export function PaymentStatusBadge({ payment }: { payment: Pick<PaymentView, "status" | "refunded_paise"> }) {
  switch (payment.status) {
    case "captured":
      return <span className="badge badge-success">Paid</span>;
    case "refunded":
      return <span className="badge badge-neutral">Refunded</span>;
    case "partially_refunded":
      return <span className="badge badge-neutral">Refunded {formatInr(payment.refunded_paise)}</span>;
    case "disputed":
      return <span className="badge badge-danger">Disputed</span>;
  }
}

const ORDER_STATUS: Record<OrderStatus, { label: string; className: string }> = {
  pending: { label: "Pending", className: "badge badge-info" },
  paid: { label: "Paid", className: "badge badge-success" },
  expired: { label: "Expired", className: "badge badge-neutral" },
  cancelled: { label: "Cancelled", className: "badge badge-neutral" },
  review: { label: "Review", className: "badge badge-warning" },
};

export function OrderStatusBadge({ status }: { status: OrderStatus }) {
  const { label, className } = ORDER_STATUS[status];
  return (
    <span className={className}>
      {status === "pending" ? <ButtonSpinner size={11} /> : null}
      {label}
    </span>
  );
}

export function TestBadge() {
  return (
    <span className="badge badge-warning">
      <FlaskIcon size={13} />
      Test
    </span>
  );
}

/** The avatar and username of a billing record's user; a deleted user keeps the recorded name. */
export function UserCell({ user }: { user: UserRef }) {
  return (
    <div className="admin-user-cell">
      <Avatar name={user.username} />
      <span className="admin-user-name">{user.username}</span>
      {user.id === null ? <span className="badge badge-neutral">deleted</span> : null}
    </div>
  );
}

/** The IST day access ended: the last valid-through date, else the day before the instant it ended. */
export function endedOn(plan: Pick<AdminUserPlan, "valid_through" | "ended_at">): string | null {
  if (plan.valid_through) return plan.valid_through;
  if (!plan.ended_at) return null;
  const instant = Date.parse(plan.ended_at);
  return Number.isNaN(instant) ? null : new Date(instant - 1).toISOString();
}

/** A term and its value in a `.admin-billing-facts` list. */
export function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** A user's plan in words and badges (Users column, the user form): "Standard · until 25 Nov", "Ended 25 Nov", … */
export function PlanLine({ plan, isAdmin }: { plan: AdminUserPlan; isAdmin: boolean }) {
  if (isAdmin || plan.state === "exempt") return <span className="badge badge-accent">Exempt</span>;
  if (plan.state === "none") {
    return (
      <span className="badge badge-neutral">
        <span className="ui-dashed" aria-hidden="true" />
        No plan
      </span>
    );
  }
  if (plan.state === "expired") {
    const ended = endedOn(plan);
    return (
      <span className="admin-plan-line">
        <span className="badge badge-warning">{ended ? `Ended ${shortIstDate(ended)}` : "Ended"}</span>
        {plan.plan ? <span className="admin-subtext">{plan.plan.name}</span> : null}
      </span>
    );
  }
  return (
    <span className="admin-plan-line">
      <b>{plan.complimentary ? `Complimentary ${plan.plan?.name ?? ""}`.trim() : plan.plan?.name}</b>
      <span className="admin-subtext">
        {plan.open_ended || !plan.valid_through ? "No end date" : `until ${shortIstDate(plan.valid_through)}`}
      </span>
      {plan.complimentary || plan.test_mode || plan.expiring_soon ? (
        <span className="admin-badges">
          {plan.expiring_soon ? (
            <span className="badge badge-warning">
              <ClockIcon size={13} />
              Ends soon
            </span>
          ) : null}
          {plan.complimentary ? (
            <span className="badge badge-neutral">
              <GiftIcon size={13} />
              Complimentary
            </span>
          ) : null}
          {plan.test_mode ? <TestBadge /> : null}
        </span>
      ) : null}
    </span>
  );
}

type PaginationProps = { page: number; pageSize: number; total: number; label: string; onPage: (page: number) => void };

/** "1–50 of 213" with previous and next buttons, at the foot of a paged card. */
export function Pagination({ page, pageSize, total, label, onPage }: PaginationProps) {
  if (total <= pageSize && page === 1) return null;
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  return (
    <nav className="admin-pagination" aria-label={label}>
      <span className="admin-subtext">
        {formatNumber(first)}–{formatNumber(last)} of {formatNumber(total)}
      </span>
      <button
        type="button"
        className="btn btn-ghost btn-icon"
        aria-label="Previous page"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        <ChevronLeftIcon />
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-icon"
        aria-label="Next page"
        disabled={last >= total}
        onClick={() => onPage(page + 1)}
      >
        <ChevronRightIcon />
      </button>
    </nav>
  );
}

const COPIED_MS = 1500;

/** Copies a value an admin pastes elsewhere (a Razorpay id, the webhook URL). */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [result, setResult] = useState<"copied" | "failed" | null>(null);

  useEffect(() => {
    if (!result) return;
    const timer = window.setTimeout(() => setResult(null), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [result]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setResult("copied");
    } catch {
      setResult("failed");
    }
  }

  return (
    <>
      <button type="button" className="btn btn-ghost btn-xs" aria-label={label} onClick={() => void copy()}>
        {result === "copied" ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
        {result === "copied" ? "Copied" : result === "failed" ? "Couldn't copy" : "Copy"}
      </button>
      <span className="sr-only" aria-live="polite">
        {result === "copied" ? "Copied to the clipboard" : result === "failed" ? "Couldn't copy" : ""}
      </span>
    </>
  );
}
