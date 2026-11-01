import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, billingApi, publicApi } from "../api/client";
import type { BillingProfile, CheckoutKind, CheckoutOption, Me, PaymentsInfo, PlanOffer } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { PaymentsUnavailable, TestModeNotice } from "../components/billing/BillingNotices";
import { leaveFor } from "../components/billing/leave";
import { istDayMonth, returnPath } from "../components/billing/planText";
import PlansFrame from "../components/billing/PlansFrame";
import { useLoad } from "../components/settings/useLoad";
import {
  AlertCircleIcon,
  AlertIcon,
  ArrowLeftIcon,
  CalendarIcon,
  CheckIcon,
  ChevronDownIcon,
  InfoIcon,
  LockIcon,
  MinusIcon,
  QrIcon,
  ShieldIcon,
  TagIcon,
} from "../components/ui/icons";
import { ButtonSpinner } from "../components/ui/Spinner";
import { answersLabel, formatInr, formatIstDate, formatIstRange, isRazorpayUrl } from "../lib/billing";
import { useOpenSettings } from "../lib/settingsLink";
import { EmailNeededNotice } from "./PlansPage";
import "./plans.css";

/** "Continue to Razorpay" appears when the browser hasn't left after this long (visual 5.5). */
const CONTINUE_AFTER_MS = 3_000;
const NO_PROFILE: BillingProfile = { name: null };

/** Direct UPI to the owner's UPI ID, or online on Razorpay's page (docs/briefs/upi-direct.md 10.1). */
type Channel = "upi" | "online";
type PayError = { message: string; code: string | null; channel: Channel; orderId?: string };

function Steps() {
  return (
    <ol className="plans-steps" aria-label="Checkout steps">
      <li className="plans-step plans-step-done">
        <span className="plans-step-num">
          <CheckIcon size={13} />
        </span>
        <span className="plans-step-text">
          Choose a plan<span className="sr-only"> (done)</span>
        </span>
      </li>
      <li className="plans-step" aria-current="step">
        <span className="plans-step-num">2</span>
        <span className="plans-step-text">Review and pay</span>
      </li>
      <li className="plans-step">
        <span className="plans-step-num">3</span>
        <span className="plans-step-text">Confirmation</span>
      </li>
    </ol>
  );
}

/** Up to four facts as chips: the features beyond chat, the default model and the answers figure. */
function chipsOf(offer: PlanOffer): { label: string; check: boolean }[] {
  const features = [
    "Chat with documents",
    ...(offer.features.files ? ["Files from chat"] : []),
    ...(offer.features.images ? ["Images from chat"] : []),
    ...offer.plugins.filter((plugin) => offer.features.plugins.includes(plugin.id)).map((plugin) => plugin.name),
  ];
  const chips = features.slice(0, 2).map((label) => ({ label, check: true }));
  const model = offer.default_chat_model?.display_name;
  if (model) chips.push({ label: model, check: true });
  if (offer.limits.monthly_token_limit > 0 && offer.answers_estimate !== null) {
    chips.push({ label: answersLabel(offer.answers_estimate, null), check: false });
  }
  return chips.slice(0, 4);
}

function Unavailable({ reason }: { reason: string }) {
  return (
    <section className="plans-panel plans-unavailable" aria-labelledby="checkout-unavailable">
      <h2 className="plans-panel-title" id="checkout-unavailable">
        This checkout isn't available
      </h2>
      <p>{reason}</p>
      <Link className="btn btn-primary btn-sm" to="/plans">
        Back to plans
      </Link>
    </section>
  );
}

type ReviewProps = {
  user: Me;
  offer: PlanOffer;
  option: CheckoutOption;
  payments: PaymentsInfo;
  profile: BillingProfile;
  /** The seller's name from the public site details; null when it couldn't load. */
  business: string | null;
};

function Review({ user, offer, option, payments, profile, business }: ReviewProps) {
  const id = useId();
  const openSettings = useOpenSettings();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const errorRef = useRef<HTMLParagraphElement>(null);
  const addEmailRef = useRef<HTMLButtonElement>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [name, setName] = useState(profile.name ?? "");
  const [paying, setPaying] = useState<Channel | null>(null);
  const [error, setError] = useState<PayError | null>(null);
  const [payUrl, setPayUrl] = useState<string | null>(null);
  const [showContinue, setShowContinue] = useState(false);
  const testMode = payments.mode === "test";
  const upi = payments.upi_direct;
  const online = payments.available;
  const seller = business ?? "us";
  const length = testMode ? "1 day" : "30 days";
  const starts = Date.parse(option.starts_at);
  const later = starts > Date.now() + 60_000;
  const range = formatIstRange(option.starts_at, option.ends_at);
  const current = user.plan.plan?.name ?? null;
  // "Add your email" is on screen already: a refusal for the missing email brings it back instead of a second message.
  const shownError = error && !(error.code === "email_required" && !user.email) ? error : null;

  useEffect(() => {
    if (!error) return;
    if (shownError) {
      errorRef.current?.focus();
      return;
    }
    addEmailRef.current?.scrollIntoView({ block: "center" });
    addEmailRef.current?.focus();
  }, [error, shownError]);

  useEffect(() => {
    if (payUrl === null) return;
    const timer = window.setTimeout(() => setShowContinue(true), CONTINUE_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [payUrl]);

  // Back from Razorpay with the browser's Back button, the page may come from the back/forward cache as it was left.
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      setPayUrl(null);
      setShowContinue(false);
      setPaying(null);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  /** Saves a changed receipt name first; false (with the error shown) when that fails. */
  async function saveName(channel: Channel): Promise<boolean> {
    const next: BillingProfile = { name: name.trim() || null };
    if (next.name === profile.name) return true;
    try {
      await billingApi.saveProfile(next);
      return true;
    } catch (err) {
      setDetailsOpen(true);
      const reason = err instanceof ApiError ? err.message : "Please try again.";
      setError({ message: `Couldn't save your receipt details. ${reason}`, code: null, channel });
      return false;
    }
  }

  async function failed(err: unknown, channel: Channel) {
    if (!(err instanceof ApiError)) {
      setError({ message: "Couldn't start the payment. Please try again.", code: null, channel });
      return;
    }
    // A UPI payment waits for the owner's check: Me then has it as the pending order, for "Open it".
    if (err.code === "upi_waiting") await refresh().catch(() => undefined);
    setError({ message: err.message, code: err.code, channel });
  }

  async function pay() {
    if (paying) return;
    setPaying("online");
    setError(null);
    let leaving = false;
    try {
      if (!(await saveName("online"))) return;
      const started = await billingApi.checkout({ plan_id: offer.id, kind: option.kind, accept_terms: true });
      if (!isRazorpayUrl(started.pay_url, testMode)) {
        setError({
          message: "The payment page's address didn't look right, so it wasn't opened.",
          code: "bad_pay_url",
          channel: "online",
          orderId: started.order.public_id,
        });
        return;
      }
      leaving = true;
      setPayUrl(started.pay_url);
      leaveFor(started.pay_url);
    } catch (err) {
      await failed(err, "online");
    } finally {
      if (!leaving) setPaying(null);
    }
  }

  async function payUpi() {
    if (paying) return;
    setPaying("upi");
    setError(null);
    let leaving = false;
    try {
      if (!(await saveName("upi"))) return;
      const order = await billingApi.upiCheckout({ plan_id: offer.id, kind: option.kind, accept_terms: true });
      leaving = true;
      navigate(returnPath(order.public_id));
    } catch (err) {
      await failed(err, "upi");
    } finally {
      if (!leaving) setPaying(null);
    }
  }

  const amount = formatInr(option.amount_paise);
  const describedBy = (channel: Channel) =>
    shownError?.channel === channel ? `${id}-error-${channel}` : `${id}-consent`;
  const payButton = (className: string, label: string) => (
    <button
      type="button"
      className={className}
      onClick={() => void pay()}
      aria-disabled={paying !== null || undefined}
      aria-busy={paying === "online" || undefined}
      aria-describedby={describedBy("online")}
    >
      {paying === "online" ? <ButtonSpinner size={14} /> : <LockIcon size={17} />}
      {paying === "online" ? "Opening Razorpay…" : label}
    </button>
  );
  const upiButton = (className: string, label: ReactNode) => (
    <button
      type="button"
      className={className}
      onClick={() => void payUpi()}
      aria-disabled={paying !== null || undefined}
      aria-busy={paying === "upi" || undefined}
      aria-describedby={describedBy("upi")}
    >
      {paying === "upi" ? <ButtonSpinner size={14} /> : <QrIcon size={17} />}
      {paying === "upi" ? "Preparing your UPI payment…" : label}
    </button>
  );

  let errorAction: ReactNode = null;
  const waiting = user.plan.pending_order;
  if (error?.code === "upi_waiting") {
    errorAction = waiting ? (
      <Link className="btn btn-xs" to={returnPath(waiting.public_id)}>
        Open it
      </Link>
    ) : null;
  } else if (error?.code === "email_required") {
    errorAction = (
      <button type="button" className="btn btn-xs" onClick={() => openSettings("account")}>
        Add email
      </button>
    );
  } else if (error?.code === "checkout_unavailable") {
    errorAction = (
      <Link className="btn btn-xs" to="/plans">
        Back to plans
      </Link>
    );
  } else if (error?.code === "payment_provider_error") {
    errorAction = (
      <button type="button" className="btn btn-xs" onClick={() => void pay()}>
        Try again
      </button>
    );
  } else if (error?.orderId) {
    errorAction = (
      <Link className="btn btn-xs" to={returnPath(error.orderId)}>
        Check status
      </Link>
    );
  }

  const errorText = (channel: Channel) =>
    shownError?.channel === channel ? (
      <p
        ref={errorRef}
        id={`${id}-error-${channel}`}
        className="error-text plans-pay-error"
        role="alert"
        tabIndex={-1}
      >
        <AlertCircleIcon size={13} />
        <span>
          {shownError.message} {errorAction}
        </span>
      </p>
    ) : null;
  const upiLimitNote =
    payments.upi_limit_paise > 0 && option.amount_paise > payments.upi_limit_paise ? (
      <p className="ui-notice ui-notice-info plans-upi-note">
        <InfoIcon size={15} />
        <span className="ui-notice-text">
          UPI is limited to {formatInr(payments.upi_limit_paise)} per payment for now. Pay {amount} by netbanking,
          debit card or credit card on the next page.
        </span>
      </p>
    ) : null;
  const methods = (
    <ul className="plans-methods" aria-label="Ways to pay">
      {["UPI", "Cards", "Netbanking", "Wallets"].map((method) => (
        <li key={method} className="plans-method">
          {method}
        </li>
      ))}
    </ul>
  );

  let datesNote: string;
  if (option.kind === "now") {
    datesNote = current
      ? `Starts now. Your ${current} period ends today${option.credit ? ", and its unused part is credited below" : ""}.`
      : "Starts now.";
  } else if (later) {
    datesNote = `Starts ${formatIstDate(option.starts_at)}, when your current plan ends.`;
  } else {
    datesNote = "Starts as soon as your payment is confirmed. It doesn't renew by itself.";
  }

  return (
    <>
      {testMode ? (
        <div className="plans-notices plans-notices-wide">
          <TestModeNotice />
        </div>
      ) : null}
      <div className="plans-checkout">
        <div>
          <section className="plans-panel" aria-labelledby={`${id}-plan`}>
            <h2 className="plans-panel-title" id={`${id}-plan`}>
              Your plan
            </h2>
            <div className="plans-chosen">
              <span className="plans-chosen-tile" aria-hidden="true">
                <TagIcon size={22} />
              </span>
              <div className="plans-chosen-main">
                <h3 className="plans-chosen-name">{offer.name}</h3>
                {offer.description ? <p className="plans-chosen-desc">{offer.description}</p> : null}
                <ul className="plans-chips">
                  {chipsOf(offer).map((chip) => (
                    <li key={chip.label} className="badge">
                      {chip.check ? <CheckIcon size={13} /> : null}
                      {chip.label}
                    </li>
                  ))}
                </ul>
              </div>
              <Link className="btn btn-ghost btn-sm" to="/plans" aria-label="Change plan">
                Change
              </Link>
            </div>
            <div className="plans-dates">
              <CalendarIcon size={20} />
              <div>
                <b>
                  {length}: valid {range}
                </b>
                <small>{datesNote}</small>
              </div>
            </div>
            {option.notes.length > 0 ? (
              <ul className="plans-notes">
                {option.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
          </section>

          {option.changes.length > 0 ? (
            <section className="ui-notice ui-notice-warning plans-changes" aria-labelledby={`${id}-changes`}>
              <h2 className="plans-changes-title" id={`${id}-changes`}>
                <AlertIcon size={16} />
                What changes on {istDayMonth(option.starts_at)}
              </h2>
              <ul>
                {option.changes.map((change) => (
                  <li key={change}>
                    <MinusIcon size={15} />
                    <span>{change}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="plans-panel plans-details" aria-labelledby={`${id}-details`}>
            <h2 className="plans-details-heading" id={`${id}-details`}>
              <button
                type="button"
                className="plans-details-toggle"
                aria-expanded={detailsOpen}
                aria-controls={`${id}-details-body`}
                onClick={() => setDetailsOpen((open) => !open)}
              >
                <span className="plans-panel-title">
                  Receipt details <span className="plans-optional">(optional)</span>
                </span>
                {!detailsOpen && name.trim() ? (
                  <span className="plans-details-name" aria-hidden="true">
                    {name.trim()}
                  </span>
                ) : null}
                <ChevronDownIcon size={16} />
              </button>
            </h2>
            {detailsOpen ? (
              <div className="plans-details-body" id={`${id}-details-body`}>
                <p className="plans-panel-sub">Saved to your account for next time.</p>
                <div className="plans-form">
                  <div className="field">
                    <label className="field-label" htmlFor={`${id}-name`}>
                      Name on the receipt
                    </label>
                    <input
                      id={`${id}-name`}
                      className="input"
                      value={name}
                      maxLength={100}
                      autoComplete="name"
                      placeholder={user.username}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </div>
                </div>
              </div>
            ) : null}
          </section>
        </div>

        <aside className="plans-summary" aria-labelledby={`${id}-summary`}>
          {user.email || upi ? null : <EmailNeededNotice buttonRef={addEmailRef} />}
          <h2 className="plans-label" id={`${id}-summary`}>
            Summary
          </h2>
          <dl className="plans-lines">
            <div>
              <dt>
                {offer.name} plan
                <small>
                  {length}, {range.replace(/ \d{4}$/, "")}
                </small>
              </dt>
              <dd>{formatInr(option.plan_price_paise)}</dd>
            </div>
            {option.credit ? (
              <div className="plans-credit">
                <dt>{option.credit.description}</dt>
                <dd>
                  <span aria-hidden="true">−</span>
                  <span className="sr-only">minus </span>
                  {formatInr(option.credit.amount_paise)}
                </dd>
              </div>
            ) : null}
          </dl>
          <div className="plans-total">
            <span className="plans-total-label">Total</span>
            <span className="plans-total-value">{formatInr(option.amount_paise)}</span>
          </div>
          {upi ? (
            <>
              {upiButton("btn btn-primary plans-pay", `Pay ${amount} with UPI`)}
              <p className="plans-pay-note">
                From any UPI app, straight to {seller}. Your plan starts once we've checked the payment, usually
                within a few hours.
              </p>
              {errorText("upi")}
              {online ? (
                <>
                  <p className="plans-or">
                    <span>or</span>
                  </p>
                  {payButton("btn plans-pay-online", `Pay online · ${amount}`)}
                  <p className="plans-pay-note">
                    UPI, cards, netbanking or wallets on Razorpay's secure page. Starts as soon as you've paid.
                  </p>
                  {upiLimitNote}
                  {methods}
                  {user.email ? null : (
                    <EmailNeededNotice buttonRef={addEmailRef}>Paying online needs a confirmed email.</EmailNeededNotice>
                  )}
                  {errorText("online")}
                </>
              ) : null}
            </>
          ) : (
            <>
              {upiLimitNote}
              {payButton("btn btn-primary plans-pay", `Pay ${amount}`)}
              {errorText("online")}
            </>
          )}
          <p className="plans-consent" id={`${id}-consent`}>
            By paying you agree to the{" "}
            <a href="/terms" target="_blank" rel="noopener">
              Terms of service
            </a>{" "}
            and the{" "}
            <a href="/refunds" target="_blank" rel="noopener">
              Cancellation and refunds policy
            </a>
            .
          </p>
          <p className="plans-secure">
            <ShieldIcon size={16} />
            <span>
              {upi
                ? `Your money goes straight to ${business ? `${business}'s` : "our"} bank account. We never see your UPI PIN or bank details.`
                : "You'll pay on Razorpay's secure page, then come back here."}
            </span>
          </p>
          {upi ? null : methods}
        </aside>
      </div>
      <div className="plans-paybar">
        <div className="plans-paybar-total">
          <b>{amount}</b>
          <span>{length}, paid in advance</span>
        </div>
        {upi ? upiButton("btn btn-primary", "Pay with UPI") : payButton("btn btn-primary", `Pay ${amount}`)}
      </div>

      {payUrl ? (
        <div className="plans-redirect" role="status">
          <div className="plans-redirect-card">
            <div className="plans-result-mark" aria-hidden="true">
              <span className="plans-result-spinner" />
            </div>
            <h2 className="plans-redirect-title">Opening Razorpay…</h2>
            <p className="plans-redirect-text">
              You'll pay {amount} on Razorpay's secure page, then come back here automatically.
            </p>
            {showContinue ? (
              <p className="plans-redirect-later">
                Not moving? <a href={payUrl}>Continue to Razorpay</a>
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}

/** /plans/checkout/:code?kind= (functional brief 8.3, visual brief 5): review the plan and its charge, then pay. */
export default function CheckoutPage() {
  const { user } = useAuth();
  const { code = "" } = useParams();
  const [params] = useSearchParams();
  const kind: CheckoutKind = params.get("kind") === "now" ? "now" : "period";
  const data = useLoad(async (signal) => {
    const [catalog, profile, site] = await Promise.all([
      billingApi.plans(signal),
      billingApi.profile(signal).catch(() => NO_PROFILE),
      publicApi.site(signal).catch(() => null),
    ]);
    return { catalog, profile, business: site?.business_name || null };
  }, "Couldn't load this checkout.");

  if (!user) return null;
  let body: ReactNode;
  if (data.data) {
    const { catalog, profile, business } = data.data;
    const offer = catalog.plans.find((item) => item.code === code);
    const option = offer?.options.find((item) => item.kind === kind);
    if (user.is_admin) body = <Unavailable reason="The admin doesn't need a plan." />;
    else if (!offer) body = <Unavailable reason="This plan isn't offered." />;
    else if (!option) body = <Unavailable reason="This option isn't available right now." />;
    else if (option.unavailable_reason !== null) {
      body = (
        <>
          {catalog.payments.available || catalog.payments.upi_direct ? null : (
            <div className="plans-notices plans-notices-wide">
              <PaymentsUnavailable payments={catalog.payments} admin={false} />
            </div>
          )}
          <Unavailable reason={option.unavailable_reason} />
        </>
      );
    } else {
      body = (
        <Review
          user={user}
          offer={offer}
          option={option}
          payments={catalog.payments}
          profile={profile}
          business={business}
        />
      );
    }
  } else if (data.error) {
    body = (
      <div className="ui-notice ui-notice-danger plans-error" role="alert">
        <AlertCircleIcon size={15} />
        <span className="ui-notice-text">{data.error}</span>
        <button type="button" className="btn btn-sm" onClick={data.reload}>
          Try again
        </button>
      </div>
    );
  } else {
    body = (
      <p className="plans-panel-sub" role="status">
        Loading…
      </p>
    );
  }

  return (
    <PlansFrame title="Review and pay" mainClassName="plans-main plans-main-narrow">
      <Link className="plans-back" to="/plans">
        <ArrowLeftIcon size={16} />
        All plans
      </Link>
      <Steps />
      <h1 className="plans-checkout-title">Review and pay</h1>
      {body}
    </PlansFrame>
  );
}
