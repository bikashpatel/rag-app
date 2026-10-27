// Words and dates about the user's plan, shared by the chat, Settings and the plan pages (docs/briefs/plans-billing.md
// 8; plans-billing-visual.md 3.3). Billing dates are India dates whatever the browser's zone, as the server writes them.

import type { CheckoutKind, CheckoutOption, PlanAccess, PlanOffer } from "../../api/types";
import { formatIstDate } from "../../lib/billing";

const DAY_MS = 86_400_000;
/** India has no daylight saving time: IST is always UTC+05:30. */
const IST_OFFSET_MS = 330 * 60_000;
const IST_ZONE = "Asia/Kolkata";

/** The IST calendar day of an instant (or of a "YYYY-MM-DD"), as whole days since 1970-01-01; NaN when malformed. */
function istDay(value: string | number): number {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value))
    return Math.floor(Date.parse(`${value}T00:00:00Z`) / DAY_MS);
  const instant = typeof value === "number" ? value : Date.parse(value);
  return Math.floor((instant + IST_OFFSET_MS) / DAY_MS);
}

/** Whole IST days from today to `value` (a "YYYY-MM-DD" or an instant): 0 today, 1 tomorrow, negative in the past. */
export function istDaysUntil(value: string, now: number = Date.now()): number {
  return istDay(value) - istDay(now);
}

/** "31 Oct" (IST). */
export function istDayMonth(value: string): string {
  return formatIstDate(value).replace(/ \d{4}$/, "");
}

/** "Sat 7 November" (IST). */
export function istLongDay(value: string): string {
  const instant = /^\d{4}-\d{2}-\d{2}$/.test(value) ? Date.parse(`${value}T12:00:00+05:30`) : Date.parse(value);
  if (Number.isNaN(instant)) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "long",
    timeZone: IST_ZONE,
  }).format(instant);
}

/** "Today, 15:02" or "26 Oct, 15:02" (IST). */
export function istMoment(iso: string, now: number = Date.now()): string {
  const instant = Date.parse(iso);
  if (Number.isNaN(instant)) return "—";
  const time = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: IST_ZONE,
  }).format(instant);
  return `${istDay(instant) === istDay(now) ? "Today" : istDayMonth(iso)}, ${time}`;
}

/** The IST date of the last day of a period that ends at `endsAt` (an exclusive instant, midnight IST). */
export function lastDayOf(endsAt: string): string {
  const instant = Date.parse(endsAt) - 1;
  const day = new Date(Math.floor((instant + IST_OFFSET_MS) / DAY_MS) * DAY_MS);
  return day.toISOString().slice(0, 10);
}

/** One more period is already paid after the current one. */
export function isStacked(plan: PlanAccess): boolean {
  return (
    plan.period_end !== null &&
    plan.paid_through !== null &&
    Date.parse(plan.paid_through) > Date.parse(plan.period_end)
  );
}

/** The avatar menu's line: "Starter · until 31 Oct", "Complimentary Professional", "No active plan", "Administrator". */
export function planLine(plan: PlanAccess): string {
  if (plan.state === "exempt") return "Administrator";
  if (!plan.access || plan.plan === null) return "No active plan";
  const name = plan.complimentary ? `Complimentary ${plan.plan.name}` : plan.plan.name;
  if (plan.open_ended || plan.valid_through === null) return name;
  return `${name} · ${isStacked(plan) ? "paid through" : "until"} ${istDayMonth(plan.valid_through)}`;
}

/** Days until the paid time ends while it is ending soon (0: the last day), else null. */
export function expiringDays(plan: PlanAccess, now: number = Date.now()): number | null {
  if (!plan.access || !plan.expiring_soon || plan.valid_through === null) return null;
  return Math.max(0, istDaysUntil(plan.valid_through, now));
}

/** The state badge: "Ends in 5 days", "Ends tomorrow", "Ends today". */
export function endsBadge(days: number): string {
  if (days <= 0) return "Ends today";
  return days === 1 ? "Ends tomorrow" : `Ends in ${days} days`;
}

/** The avatar menu's "Plan & billing" badge: "5 days left", "1 day left", "Last day". */
export function daysLeftBadge(days: number): string {
  if (days <= 0) return "Last day";
  return days === 1 ? "1 day left" : `${days} days left`;
}

/** The checkout of one option of a plan. */
export function checkoutPath(code: string, kind: CheckoutKind): string {
  return `/plans/checkout/${encodeURIComponent(code)}?kind=${kind}`;
}

/** The plan to renew from a banner, Settings or the ended panel. */
export function renewPath(code: string): string {
  return checkoutPath(code, "period");
}

/** The plan's `period` option when it can be bought now (not retired, nothing paid ahead, payments set up), else null. */
export function renewalOf(offer: PlanOffer | null | undefined): CheckoutOption | null {
  return offer?.options.find((option) => option.kind === "period" && option.unavailable_reason === null) ?? null;
}

/** The plan can't be renewed because it is no longer offered: retired, or missing from the catalog. */
export function isRetired(plans: PlanOffer[], code: string): boolean {
  return plans.find((offer) => offer.code === code)?.status !== "active";
}

/** Where a payment in progress is followed. */
export function returnPath(orderId: string): string {
  return `/billing/return?order=${encodeURIComponent(orderId)}`;
}

/** "UPI", "Card", "Netbanking", "Bank transfer", … for an order's or a payment's method. */
export function methodLabel(method: string | null): string | null {
  if (!method) return null;
  const known: Record<string, string> = {
    upi: "UPI",
    card: "Card",
    netbanking: "Netbanking",
    wallet: "Wallet",
    emi: "EMI",
    bank_transfer: "Bank transfer",
  };
  return known[method] ?? method.charAt(0).toUpperCase() + method.slice(1).replace(/_/g, " ");
}

/** A higher plan offers something this one doesn't: a locked feature, or more models. */
export function canUpgrade(plan: PlanAccess): boolean {
  const { features, unlock } = plan;
  return (
    (!features.files && unlock.files !== null) ||
    (!features.images && unlock.images !== null) ||
    unlock.models !== null ||
    Object.entries(unlock.plugins).some(([id, name]) => name !== null && !features.plugins.includes(id))
  );
}
