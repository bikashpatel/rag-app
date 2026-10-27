import { useCallback, useId, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { billingApi } from "../api/client";
import type { PlanAccess, PlanOffer } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { PaymentsUnavailable, TestModeNotice } from "../components/billing/BillingNotices";
import { PendingCheckoutNotice } from "../components/billing/PendingCheckoutBanner";
import PlanCard, { PlanGridSkeleton, type PlanEmphasis } from "../components/billing/PlanCard";
import PlanCompareTable from "../components/billing/PlanCompareTable";
import { checkoutPath, endsBadge, expiringDays, isStacked, istDayMonth } from "../components/billing/planText";
import PlansFrame from "../components/billing/PlansFrame";
import PolicyLinks from "../components/billing/PolicyLinks";
import { useLoad } from "../components/settings/useLoad";
import {
  AlertCircleIcon,
  CalendarIcon,
  ClockIcon,
  DocsIcon,
  FlaskIcon,
  GiftIcon,
  InfoIcon,
  ShieldIcon,
} from "../components/ui/icons";
import { formatIstDate } from "../lib/billing";
import "./plans.css";

const ADMIN_TITLE = "The admin doesn't need a plan.";

/** The title, its key word in italics, the lede and the state badges (visual 4.2). */
function Intro({ plan }: { plan: PlanAccess }) {
  const days = expiringDays(plan);
  let title: ReactNode;
  let lede: string;
  if (plan.state === "exempt") {
    title = "Plans";
    lede = "This is the page users see.";
  } else if (plan.state === "active" && plan.plan) {
    title = (
      <>
        Your plan: <em>{plan.plan.name}</em>
      </>
    );
    if (plan.open_ended || plan.valid_through === null) {
      lede =
        "Complimentary, with no end date. You can still choose a paid plan; your complimentary plan continues after it.";
    } else {
      const next =
        plan.next_plan && plan.period_end ? ` From ${istDayMonth(plan.period_end)}: ${plan.next_plan.name}.` : "";
      lede = `Valid through ${formatIstDate(plan.valid_through)}${isStacked(plan) ? ", paid ahead" : ""}. Upgrade now with credit for what's left, or switch plans when this one ends.${next}`;
    }
  } else if (plan.state === "expired" && plan.plan) {
    title = (
      <>
        Your <em>{plan.plan.name}</em> plan has ended
      </>
    );
    const ended = plan.valid_through ?? plan.ended_at;
    lede = `${ended ? `It ended on ${formatIstDate(ended)}. ` : ""}Your chats and documents are kept: renew or choose another plan to pick up where you left off.`;
  } else {
    title = (
      <>
        Choose a <em>plan</em>
      </>
    );
    lede = "Your account is ready. Pick a plan to start chatting with your documents.";
  }
  const badges: ReactNode[] = [];
  if (plan.state === "exempt")
    badges.push(
      <span key="admin" className="badge badge-accent">
        Admin
      </span>,
    );
  if (days !== null) {
    badges.push(
      <span key="ends" className="badge badge-warning">
        <ClockIcon size={13} />
        {endsBadge(days)}
      </span>,
    );
  }
  if (plan.access && plan.complimentary) {
    badges.push(
      <span key="comp" className="badge">
        <GiftIcon size={13} />
        {plan.open_ended ? "Complimentary · no end date" : "Complimentary"}
      </span>,
    );
  }
  if (plan.access && plan.test_mode) {
    badges.push(
      <span key="test" className="badge badge-warning">
        <FlaskIcon size={13} />
        Test mode
      </span>,
    );
  }
  return (
    <div className="plans-intro">
      <h1 className="plans-title">{title}</h1>
      <p className="plans-lede">{lede}</p>
      {badges.length > 0 ? <div className="plans-intro-meta">{badges}</div> : null}
    </div>
  );
}

/** The one emphasised card (V3): the current plan, else the last one when it can be renewed, else the recommended. */
function emphasisOf(plans: PlanOffer[], plan: PlanAccess): { code: string; emphasis: PlanEmphasis } | null {
  const current = plan.state === "active" ? plans.find((offer) => offer.current) : undefined;
  if (current) return { code: current.code, emphasis: "current" };
  const lastCode = plan.state === "expired" ? plan.plan?.code : undefined;
  const last = plans.find(
    (offer) =>
      offer.code === lastCode &&
      offer.options.some((option) => option.kind === "period" && option.unavailable_reason === null),
  );
  if (last) return { code: last.code, emphasis: "last" };
  const recommended = plans.find((offer) => offer.recommended);
  return recommended ? { code: recommended.code, emphasis: "recommended" } : null;
}

/**
 * The card whose main action is the page's one primary button (8.2): the emphasised one without a plan, the current
 * plan's renewal while it ends soon, or, when that plan is retired, the recommended plan (else the cheapest one that
 * can be bought: plans come by rank, and a higher rank costs more).
 */
function primaryOf(plans: PlanOffer[], plan: PlanAccess, emphasised: string | null): string | null {
  if (plan.state !== "active") return emphasised;
  if (!plan.expiring_soon || plan.open_ended) return null;
  const current = plans.find((offer) => offer.current);
  if (current?.status === "active") return current.code;
  const buyable = plans.filter(
    (offer) => offer.status === "active" && offer.options.some((option) => option.unavailable_reason === null),
  );
  return (buyable.find((offer) => offer.recommended) ?? buyable[0])?.code ?? null;
}

/** The visible label, with the plan's name for screen readers where the label doesn't say it ("Upgrade now to …"). */
function OptionLabel({ label, plan }: { label: string; plan: string }) {
  if (label === "Renew") return <>Renew {plan}</>;
  if (label.includes(plan)) return <>{label}</>;
  const dot = label.indexOf(" · ");
  const [before, after] = dot === -1 ? [label, ""] : [label.slice(0, dot), label.slice(dot)];
  // One span: the button is a flex box, whose gap would otherwise open around the hidden words.
  return (
    <span>
      {before}
      <span className="sr-only"> to {plan}</span>
      {after}
    </span>
  );
}

type CardActionsProps = {
  offer: PlanOffer;
  primary: boolean;
  admin: boolean;
  /** "30 days", or "1 day" in test mode. */
  length: string;
};

/** The main option as a button, the alternative as a link, an unavailable one disabled with its reason (visual 4.3). */
function CardActions({ offer, primary, admin, length }: CardActionsProps) {
  const reasonId = useId();
  const buttonClass = primary ? "btn btn-primary plans-cta" : "btn plans-cta";
  if (admin) {
    return (
      <button type="button" className={buttonClass} aria-disabled="true" title={ADMIN_TITLE}>
        Choose {offer.name}
      </button>
    );
  }
  const usable = offer.options.filter((option) => option.unavailable_reason === null);
  const main =
    usable.find((option) => option.kind === "now") ??
    usable.find((option) => option.kind === "period") ??
    offer.options[0];
  if (!main) {
    return (
      <p className="plans-reason">
        <InfoIcon size={15} />
        {offer.status === "retired" ? "This plan is no longer offered." : "Not available right now."}
      </p>
    );
  }
  if (main.unavailable_reason !== null) {
    return (
      <>
        <button type="button" className="btn plans-cta" aria-disabled="true" aria-describedby={reasonId}>
          <OptionLabel label={main.label} plan={offer.name} />
        </button>
        <p className="plans-reason" id={reasonId}>
          <InfoIcon size={15} />
          {main.unavailable_reason}
        </p>
      </>
    );
  }
  const alt = main.kind === "now" ? usable.find((option) => option.kind === "period") : undefined;
  const later = Date.parse(main.starts_at) > Date.now() + 60_000;
  return (
    <>
      <Link className={buttonClass} to={checkoutPath(offer.code, main.kind)}>
        <OptionLabel label={main.label} plan={offer.name} />
      </Link>
      {alt ? (
        <p className="plans-alt">
          <Link to={checkoutPath(offer.code, alt.kind)}>
            {offer.current ? "or renew from" : "or switch on"} {istDayMonth(alt.starts_at)}
            {offer.current ? null : <span className="sr-only"> to {offer.name}</span>}
          </Link>
        </p>
      ) : offer.current && main.kind === "period" && later ? (
        <p className="plans-alt">
          Adds {length} from {istDayMonth(main.starts_at)}
        </p>
      ) : null}
    </>
  );
}

const HOW = [
  {
    icon: <ShieldIcon size={20} />,
    title: "Pay on Razorpay's secure page",
    text: "UPI, cards, netbanking or wallets. We never see your card or bank details.",
  },
  {
    icon: <CalendarIcon size={20} />,
    title: "30 days, no automatic renewal",
    text: "Nothing is charged again. We remind you before your plan ends.",
  },
  {
    icon: <DocsIcon size={20} />,
    title: "Your data stays yours",
    text: "Chats and documents are kept, even if your plan ends, and come back when you renew.",
  },
];

/** /plans (functional brief 8.2, visual brief 4): the plans with what the user can do now, and how paying works. */
export default function PlansPage() {
  const { user, refresh } = useAuth();
  const [params] = useSearchParams();
  const catalog = useLoad((signal) => billingApi.plans(signal), "Couldn't load the plans.");
  const { reload } = catalog;
  const onCancelled = useCallback(() => {
    void refresh().catch(() => undefined);
    reload();
  }, [refresh, reload]);

  if (!user) return null;
  const plan = user.plan;
  const admin = user.is_admin;
  const plans = catalog.data?.plans ?? null;
  const payments = catalog.data?.payments ?? null;
  const emphasis = plans ? emphasisOf(plans, plan) : null;
  const primaryCode = plans ? primaryOf(plans, plan, emphasis?.code ?? null) : null;
  const length = payments?.mode === "test" ? "1 day" : "30 days";

  let cards: ReactNode;
  if (plans === null) {
    cards = catalog.error ? (
      <div className="ui-notice ui-notice-danger plans-error" role="alert">
        <AlertCircleIcon size={15} />
        <span className="ui-notice-text">Couldn't load the plans.</span>
        <button type="button" className="btn btn-sm" onClick={reload}>
          Try again
        </button>
      </div>
    ) : (
      <PlanGridSkeleton />
    );
  } else if (plans.length === 0) {
    cards = (
      <div className="card plans-empty">
        <h2 className="plans-empty-title">No plans are available yet</h2>
        {admin ? (
          <p>
            <Link to="/admin#plans">Create plans in Admin → Plans</Link>
          </p>
        ) : payments?.contact_email ? (
          <p>
            Questions? Write to <a href={`mailto:${payments.contact_email}`}>{payments.contact_email}</a>.
          </p>
        ) : null}
      </div>
    );
  } else {
    cards = (
      <ul className="plans-grid" aria-label="Plans">
        {plans.map((offer, index) => {
          const emphasised = emphasis?.code === offer.code;
          return (
            <PlanCard
              key={offer.code}
              plan={offer}
              emphasis={emphasised ? emphasis.emphasis : null}
              lower={plans[index - 1] ?? null}
              actions={
                <CardActions offer={offer} primary={primaryCode === offer.code} admin={admin} length={length} />
              }
            />
          );
        })}
      </ul>
    );
  }

  const notices: ReactNode[] = [];
  if (admin) {
    notices.push(
      <div key="admin" className="ui-notice" role="status">
        <ShieldIcon size={15} />
        <span className="ui-notice-text">You're the admin, so plans don't apply to you. This is what users see.</span>
      </div>,
    );
  }
  if (payments && !payments.available)
    notices.push(<PaymentsUnavailable key="payments" payments={payments} admin={admin} />);
  if (payments?.available && payments.mode === "test") notices.push(<TestModeNotice key="test" />);
  if (plan.pending_order) {
    notices.push(<PendingCheckoutNotice key="pending" order={plan.pending_order} onCancelled={onCancelled} />);
  }
  if (params.get("payment") === "unknown") {
    notices.push(
      <div key="unknown" className="ui-notice ui-notice-info" role="status">
        <InfoIcon size={15} />
        <span className="ui-notice-text">
          We couldn't match that return from Razorpay. If you paid, it will be confirmed automatically within a few
          minutes.
        </span>
      </div>,
    );
  }

  return (
    <PlansFrame title="Plans" mainClassName="plans-main">
      <Intro plan={plan} />
      {notices.length > 0 ? <div className="plans-notices">{notices}</div> : null}
      {cards}
      {plans && plans.length > 0 ? (
        <>
          <PlanCompareTable plans={plans} emphasisedCode={emphasis?.code ?? null} />
          <ul className="plans-how" aria-label="How payment works">
            {HOW.map((item) => (
              <li key={item.title}>
                <span className="plans-how-icon" aria-hidden="true">
                  {item.icon}
                </span>
                <b>{item.title}</b>
                <span>{item.text}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <footer className="plans-foot">
        <PolicyLinks />
        <p>Payments are processed by Razorpay.</p>
      </footer>
    </PlansFrame>
  );
}
