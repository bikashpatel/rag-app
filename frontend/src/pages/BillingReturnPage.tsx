import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, billingApi, receiptUrl } from "../api/client";
import type { OrderView } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { checkoutPath, istMoment, lastDayOf, methodLabel } from "../components/billing/planText";
import PlansFrame from "../components/billing/PlansFrame";
import UpiOrder, { upiStateOf } from "../components/billing/UpiOrder";
import {
  AlertCircleIcon,
  ArrowRightIcon,
  ArrowUpRightIcon,
  ClockIcon,
  DownloadIcon,
  FlaskIcon,
  InfoIcon,
  MailIcon,
  RefreshIcon,
} from "../components/ui/icons";
import AllSetMark from "../components/ui/marks/AllSetMark";
import PaymentCheckMark from "../components/ui/marks/PaymentCheckMark";
import { ButtonSpinner } from "../components/ui/Spinner";
import { formatInr, formatIstDate, formatIstRange, isRazorpayUrl } from "../lib/billing";
import { useOpenSettings } from "../lib/settingsLink";
import "./plans.css";

/** Polling (8.4): at once, every 3 s for 60 s, then every 15 s up to 10 minutes, then Check again. */
const FAST_MS = 3_000;
const SLOW_MS = 15_000;
const FAST_FOR_MS = 60_000;
const STOP_AFTER_MS = 600_000;

type Phase = "pending" | "waiting" | "stopped" | "paid" | "failed" | "review" | "missing";

const FINAL: ReadonlySet<Phase> = new Set(["paid", "failed", "review", "missing"]);

function phaseOf(order: OrderView | null, elapsed: number, stopped: boolean): Phase {
  switch (order?.status) {
    case "paid":
      return "paid";
    case "expired":
    case "cancelled":
      return "failed";
    case "review":
      return "review";
    default:
      if (stopped) return "stopped";
      return elapsed >= FAST_FOR_MS ? "waiting" : "pending";
  }
}

type Tone = "info" | "warning" | "success" | "danger";

/** The paid order's facts as a receipt slip. */
function PaidSlip({ order }: { order: OrderView }) {
  const length = order.test_mode ? "1 day" : "30 days";
  return (
    <dl className="plans-receipt">
      <div>
        <dt>Plan</dt>
        <dd>
          {order.test_mode ? (
            <span className="badge badge-warning">
              <FlaskIcon size={13} />
              Test mode
            </span>
          ) : null}
          {order.plan.name}, {length}
        </dd>
      </div>
      {order.period_start && order.period_end ? (
        <div>
          <dt>Period</dt>
          <dd>{formatIstRange(order.period_start, order.period_end)}</dd>
        </div>
      ) : null}
      {order.method || order.channel === "upi_direct" ? (
        <div>
          <dt>Paid with</dt>
          <dd>{methodLabel(order.method, order.channel)}</dd>
        </div>
      ) : null}
      {order.receipt ? (
        <div>
          <dt>Receipt</dt>
          <dd>{order.receipt.number}</dd>
        </div>
      ) : null}
      <div className="plans-receipt-total">
        <dt>Total paid</dt>
        <dd>
          {formatInr(order.amount_paise)}
          {order.credit_paise > 0 ? <small>after {formatInr(order.credit_paise)} credit</small> : null}
        </dd>
      </div>
    </dl>
  );
}

/** No verified email with the mailer set up: one line to add one, for receipts and reminders (functional 13.8). */
function EmailLine() {
  const { user } = useAuth();
  const openSettings = useOpenSettings();
  const [mailer, setMailer] = useState(false);
  const needed = user !== null && user.email === null;

  useEffect(() => {
    if (!needed) return;
    const controller = new AbortController();
    billingApi.status(controller.signal).then(
      (status) => setMailer(status.payments.email_reminders),
      () => undefined,
    );
    return () => controller.abort();
  }, [needed]);

  if (!needed || !mailer) return null;
  return (
    <p className="ui-notice plans-email-line" role="status">
      <MailIcon size={15} />
      <span className="ui-notice-text">
        Add an email in{" "}
        <button type="button" className="plans-link-button" onClick={() => openSettings("account")}>
          Settings → Account
        </button>{" "}
        to get receipts and reminders by email.
      </span>
    </p>
  );
}

/**
 * /billing/return?order=… (functional brief 8.4, visual brief 6): back from Razorpay, the order's outcome; for a
 * direct-UPI order also its payment request and the owner's check (docs/briefs/upi-direct.md 10.2).
 */
export default function BillingReturnPage() {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const orderId = params.get("order") ?? "";
  const checkFailed = params.get("check") === "failed";
  const [order, setOrder] = useState<OrderView | null>(null);
  const [missing, setMissing] = useState(orderId === "");
  // Until the first answer, which may be a direct-UPI order, the page shows a skeleton rather than "Confirming…".
  const [answered, setAnswered] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [stopped, setStopped] = useState(false);
  const [round, setRound] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const refreshed = useRef<Promise<void> | null>(null);

  const phase = missing ? "missing" : phaseOf(order, elapsed, stopped);
  const final = FINAL.has(phase);

  // Polls until a final status, then stops; "Check again" starts a new round.
  useEffect(() => {
    if (orderId === "") return;
    const controller = new AbortController();
    const started = Date.now();
    let timer: number | undefined;
    const check = async () => {
      let next: OrderView | null = null;
      try {
        next = await billingApi.order(orderId, true, controller.signal);
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) {
          setMissing(true);
          return;
        }
      }
      if (controller.signal.aborted) return;
      setAnswered(true);
      const spent = Date.now() - started;
      setElapsed(spent);
      if (next) setOrder(next);
      // Settled, or a direct-UPI order, which Razorpay never confirms: its own page takes over.
      if (next && (next.status !== "pending" || next.channel === "upi_direct")) return;
      if (spent >= STOP_AFTER_MS) {
        setStopped(true);
        return;
      }
      timer = window.setTimeout(() => void check(), spent < FAST_FOR_MS ? FAST_MS : SLOW_MS);
    };
    void check();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [orderId, round]);

  // A paid order: Me now has the plan, so "Start chatting" goes to the chat.
  useEffect(() => {
    if (phase === "paid" && refreshed.current === null) refreshed.current = refresh().catch(() => undefined);
  }, [phase, refresh]);

  useEffect(() => {
    if (final) titleRef.current?.focus();
  }, [final]);

  const checkAgain = useCallback(() => {
    setStopped(false);
    setElapsed(0);
    setRound((value) => value + 1);
  }, []);

  async function cancel() {
    if (cancelling || !order) return;
    setCancelling(true);
    setCancelError(null);
    try {
      setOrder(await billingApi.cancel(order.public_id));
      void refresh().catch(() => undefined);
    } catch (err) {
      setCancelError(err instanceof ApiError ? err.message : "Couldn't cancel the payment. Please try again.");
    } finally {
      setCancelling(false);
    }
  }

  async function startChatting() {
    await (refreshed.current ?? refresh().catch(() => undefined));
    navigate("/");
  }

  if (!answered && !missing) {
    return (
      <PlansFrame title="Your payment" mainClassName="plans-main plans-main-result" live>
        <section className="plans-result plans-result-loading" aria-busy="true">
          <span className="sr-only">Loading your payment…</span>
          <span className="ui-skeleton plans-result-skeleton-mark" aria-hidden="true" />
          <span className="ui-skeleton plans-result-skeleton-title" aria-hidden="true" />
          <span className="ui-skeleton plans-result-skeleton-line" aria-hidden="true" />
        </section>
      </PlansFrame>
    );
  }

  const upiState = order?.channel === "upi_direct" && order.upi && !missing ? upiStateOf(order) : null;
  if (order && upiState) {
    return (
      <PlansFrame
        title="Your payment"
        mainClassName={upiState === "pending" ? "plans-main plans-main-upi" : "plans-main plans-main-result"}
        live={upiState !== "pending"}
      >
        <UpiOrder order={order} state={upiState} onChange={setOrder} />
      </PlansFrame>
    );
  }

  const resume = order?.pay_url && isRazorpayUrl(order.pay_url, order.test_mode) ? order.pay_url : null;
  const failedCheck = checkFailed ? (
    <div className="ui-notice ui-notice-info" role="status">
      <InfoIcon size={15} />
      <span className="ui-notice-text">
        We couldn't verify the return from Razorpay. If you paid, it will be confirmed automatically within a few
        minutes.
      </span>
    </div>
  ) : null;

  let tone: Tone;
  let mark: ReactNode;
  let title: string;
  let text: ReactNode = null;
  let extra: ReactNode = null;
  let actions: ReactNode = null;
  let foot: string | null = null;
  switch (phase) {
    case "paid": {
      tone = "success";
      mark = <AllSetMark />;
      title = "Payment received";
      const stacked =
        order?.period_start && order.paid_at && Date.parse(order.period_start) > Date.parse(order.paid_at) + 60_000;
      const until = order?.valid_through ?? (order?.period_end ? lastDayOf(order.period_end) : null);
      text =
        stacked && order?.period_start && order.period_end ? (
          <>
            Your <b>{order.plan.name}</b> plan continues from {formatIstRange(order.period_start, order.period_end)}.
          </>
        ) : (
          <>
            You're on <b>{order?.plan.name}</b>
            {until ? (
              <>
                {" "}
                until <b>{formatIstDate(until)}</b>
              </>
            ) : null}
            .
          </>
        );
      extra = order ? (
        <>
          <PaidSlip order={order} />
          <EmailLine />
        </>
      ) : null;
      actions = (
        <>
          {order?.receipt ? (
            <a className="btn" href={receiptUrl(order.receipt.id)}>
              <DownloadIcon size={16} />
              Download receipt
            </a>
          ) : null}
          <button type="button" className="btn btn-primary" onClick={() => void startChatting()}>
            Start chatting
            <ArrowRightIcon size={16} />
          </button>
        </>
      );
      foot =
        order?.channel === "upi_direct"
          ? "Your receipt is in Settings → Plan & billing."
          : "Razorpay also emails a confirmation to the address you gave it.";
      break;
    }
    case "failed":
      tone = "danger";
      mark = <AlertCircleIcon size={32} />;
      title = "Payment not completed";
      text = "No payment was received for this checkout, so your plan hasn't changed.";
      extra = order ? (
        <>
          {order.last_payment_error ? (
            <div className="ui-notice ui-notice-danger" role="status">
              <AlertCircleIcon size={15} />
              <span className="ui-notice-text">
                <b>Last attempt:</b> {order.last_payment_error}
              </span>
            </div>
          ) : null}
          <dl className="plans-receipt">
            <div>
              <dt>Plan</dt>
              <dd>
                {order.plan.name}, {order.test_mode ? "1 day" : "30 days"}
              </dd>
            </div>
            <div>
              <dt>Amount</dt>
              <dd>{formatInr(order.amount_paise)}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{istMoment(order.created_at)}</dd>
            </div>
          </dl>
        </>
      ) : null;
      actions = order ? (
        <>
          <Link className="btn" to="/plans">
            Back to plans
          </Link>
          <Link className="btn btn-primary" to={checkoutPath(order.plan.code, order.kind)}>
            <RefreshIcon size={16} />
            Try again
          </Link>
        </>
      ) : null;
      break;
    case "review":
      tone = "warning";
      mark = <PaymentCheckMark />;
      title = "We're checking your payment";
      text =
        "We received a payment and are checking it. Your plan starts as soon as it's confirmed. Please don't pay again.";
      actions = (
        <Link className="btn" to="/plans">
          Back to plans
        </Link>
      );
      break;
    case "missing":
      tone = "danger";
      mark = <AlertCircleIcon size={32} />;
      title = "This payment isn't in your account";
      actions = (
        <Link className="btn btn-primary" to="/plans">
          Back to plans
        </Link>
      );
      break;
    case "stopped":
      tone = "info";
      mark = <RefreshIcon size={32} />;
      title = "No answer from Razorpay yet";
      text = "If you paid, your plan will start automatically within a few minutes. You can close this page.";
      extra = failedCheck;
      actions = (
        <>
          <Link className="btn" to="/plans">
            Back to plans
          </Link>
          <button type="button" className="btn btn-primary" onClick={checkAgain}>
            Check again
          </button>
        </>
      );
      break;
    case "waiting":
      tone = "warning";
      mark = <ClockIcon size={32} />;
      title = "Still waiting for Razorpay";
      text = "If you closed Razorpay's page before paying, you can go back to it. We keep checking either way.";
      extra = (
        <>
          {failedCheck}
          {cancelError ? (
            <p className="error-text" role="alert">
              <AlertCircleIcon size={13} />
              {cancelError}
            </p>
          ) : null}
        </>
      );
      actions = (
        <>
          <Link className="btn" to="/plans">
            Choose another plan
          </Link>
          {resume ? (
            <a className="btn btn-primary" href={resume}>
              Resume payment
              <ArrowUpRightIcon size={16} />
            </a>
          ) : null}
          {order ? (
            <button
              type="button"
              className="btn btn-ghost"
              aria-disabled={cancelling || undefined}
              onClick={() => void cancel()}
            >
              {cancelling ? <ButtonSpinner size={12} /> : null}
              Cancel this payment
            </button>
          ) : null}
        </>
      );
      break;
    default:
      tone = "info";
      mark = <PaymentCheckMark />;
      title = "Confirming your payment…";
      text = "This usually takes a few seconds. You can keep this page open.";
      extra = (
        <>
          {failedCheck}
          {order ? (
            <dl className="plans-receipt">
              <div>
                <dt>Plan</dt>
                <dd>
                  {order.plan.name} · {formatInr(order.amount_paise)}
                </dd>
              </div>
            </dl>
          ) : null}
        </>
      );
  }

  return (
    <PlansFrame title="Your payment" mainClassName="plans-main plans-main-result" live>
      <section
        className={`plans-result plans-result-${tone}${final ? " plans-result-final" : ""}`}
        aria-labelledby="plans-result-title"
      >
        <div className="plans-result-mark" aria-hidden="true">
          {mark}
        </div>
        <h1 className="plans-result-title" id="plans-result-title" ref={titleRef} tabIndex={-1}>
          {title}
        </h1>
        {text ? <p className="plans-result-text">{text}</p> : null}
        {extra}
        {actions ? <div className="plans-result-actions">{actions}</div> : null}
        {phase === "failed" ? (
          <p className="plans-result-note">
            <InfoIcon size={16} />
            <span>
              If money left your account, it will be matched automatically within a few days, or Razorpay returns it to
              you.
            </span>
          </p>
        ) : null}
      </section>
      {foot ? <p className="plans-result-foot">{foot}</p> : null}
    </PlansFrame>
  );
}
