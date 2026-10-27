import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { billingApi } from "../../api/client";
import type { PlanAccess } from "../../api/types";
import { ClockIcon, CloseIcon } from "../ui/icons";
import "./billing.css";
import { expiringDays, isRetired, istDayMonth, renewPath } from "./planText";

/** The period end and the IST date the banner was dismissed on: it stays away for the rest of that day (8.7). */
const DISMISS_KEY = "rag.planBanner";

type Dismissal = { end: string | null; date: string };

function today(): string {
  return new Date(Date.now() + 330 * 60_000).toISOString().slice(0, 10);
}

function dismissedFor(end: string | null): boolean {
  try {
    const stored = JSON.parse(localStorage.getItem(DISMISS_KEY) ?? "null") as Dismissal | null;
    return stored !== null && stored.end === end && stored.date === today();
  } catch {
    return false;
  }
}

function storeDismissal(end: string | null): void {
  try {
    localStorage.setItem(DISMISS_KEY, JSON.stringify({ end, date: today() } satisfies Dismissal));
  } catch {
    /* storage unavailable: dismissed until the page reloads */
  }
}

/**
 * "Your Starter plan ends in 5 days, on 31 Oct." under the chat's top bar while the plan ends soon, with Renew, or
 * Choose a plan once the plan is retired (8.7). The action waits for the catalog, so its label never flips.
 */
export default function PlanBanner({ plan }: { plan: PlanAccess }) {
  const [dismissed, setDismissed] = useState(() => dismissedFor(plan.paid_through));
  const [retired, setRetired] = useState<boolean | null>(null);
  const days = expiringDays(plan);
  const code = days !== null && !dismissed ? (plan.plan?.code ?? null) : null;

  useEffect(() => {
    if (code === null) return;
    const controller = new AbortController();
    billingApi.plans(controller.signal).then(
      (catalog) => setRetired(isRetired(catalog.plans, code)),
      () => {
        if (!controller.signal.aborted) setRetired(false);
      },
    );
    return () => controller.abort();
  }, [code]);

  if (days === null || plan.plan === null || plan.valid_through === null || dismissed) return null;
  const when =
    days <= 0
      ? "ends tonight at midnight."
      : days === 1
        ? `ends tomorrow, on ${istDayMonth(plan.valid_through)}.`
        : `ends in ${days} days, on ${istDayMonth(plan.valid_through)}.`;
  return (
    <div className={`ui-notice ui-notice-${days <= 1 ? "warning" : "info"} chat-notice billing-banner`} role="status">
      <ClockIcon size={15} />
      <span className="ui-notice-text">
        Your {plan.complimentary ? "complimentary " : ""}
        <b>{plan.plan.name}</b> plan {when}
      </span>
      {retired === null ? null : retired ? (
        <Link className="btn btn-sm" to="/plans">
          Choose a plan
        </Link>
      ) : (
        <Link className="btn btn-sm" to={renewPath(plan.plan.code)}>
          Renew
        </Link>
      )}
      <button
        type="button"
        className="btn btn-ghost btn-icon btn-sm billing-banner-dismiss"
        aria-label="Dismiss until tomorrow"
        title="Dismiss until tomorrow"
        onClick={() => {
          storeDismissal(plan.paid_through);
          setDismissed(true);
        }}
      >
        <CloseIcon size={15} />
      </button>
    </div>
  );
}
