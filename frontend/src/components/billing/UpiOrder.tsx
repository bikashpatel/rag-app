import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";
import { Link } from "react-router-dom";
import { ApiError, billingApi, publicApi, upiQrUrl } from "../../api/client";
import type { OrderView, PaymentsInfo, UpiOrderInfo } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { formatInr, formatUtr, isUpiPayUri } from "../../lib/billing";
import { useMediaQuery } from "../chat/useMediaQuery";
import {
  AlertCircleIcon,
  ArrowRightIcon,
  ArrowUpRightIcon,
  CheckIcon,
  ChevronDownIcon,
  ClockIcon,
  CopyIcon,
  DownloadIcon,
  InfoIcon,
  RefreshIcon,
} from "../ui/icons";
import PaymentCheckMark from "../ui/marks/PaymentCheckMark";
import UpiPayMark from "../ui/marks/UpiPayMark";
import { ButtonSpinner } from "../ui/Spinner";
import { checkoutPath, istDayTime, istMoment } from "./planText";

/** While the owner checks a UTR: the order again every 30 s while the tab is visible, for 30 minutes (10.2). */
const POLL_MS = 30_000;
const WATCH_MS = 30 * 60_000;
const COPIED_MS = 2_000;
const UTR_DIGITS = 12;
/** Room for a pasted label ("UPI Ref No: 6305 1234 5678") and a longer wrong number, which the echo then names. */
const UTR_MAX_LENGTH = 40;
/** Phones and touch screens get "Open UPI app" first and the QR code behind a toggle (10). */
const PHONE_QUERY = "(max-width: 799.98px), (pointer: coarse)";

export type UpiState = "pending" | "submitted" | "rejected" | "expired" | "cancelled";

/** What the UPI order page shows for a direct-UPI order, or null when the shared result card does (paid, review). */
export function upiStateOf(order: OrderView, now: number = Date.now()): UpiState | null {
  switch (order.status) {
    case "pending":
      return Date.parse(order.expires_at) <= now ? "expired" : "pending";
    case "submitted":
    case "rejected":
    case "expired":
    case "cancelled":
      return order.status;
    default:
      return null;
  }
}

type Context = { payments: PaymentsInfo | null; business: string | null };

/** The contact email, mail and online payment (billing status) and the seller's name, for the page's copy. */
function usePageContext(): Context {
  const [context, setContext] = useState<Context>({ payments: null, business: null });
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      billingApi.status(controller.signal).catch(() => null),
      publicApi.site(controller.signal).catch(() => null),
    ]).then(([status, site]) => {
      if (!controller.signal.aborted)
        setContext({ payments: status?.payments ?? null, business: site?.business_name || null });
    });
    return () => controller.abort();
  }, []);
  return context;
}

type CopyProps = { value: string; what: string; target: RefObject<HTMLElement | null> };

/** Copies a value; without clipboard access it selects the text on screen instead, for the phone's own copy. */
function CopyValue({ value, what, target }: CopyProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      const node = target.current;
      if (node) window.getSelection()?.selectAllChildren(node);
    }
  }

  return (
    <>
      <button
        type="button"
        className="btn btn-ghost btn-xs plans-upi-copy"
        aria-label={`Copy ${what}`}
        onClick={() => void copy()}
      >
        {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
        {copied ? "Copied" : "Copy"}
      </button>
      <span className="sr-only" aria-live="polite">
        {copied ? `Copied ${what}` : ""}
      </span>
    </>
  );
}

function Facts({ order, upi }: { order: OrderView; upi: UpiOrderInfo }) {
  const idRef = useRef<HTMLElement>(null);
  const amountRef = useRef<HTMLSpanElement>(null);
  const noteRef = useRef<HTMLElement>(null);
  return (
    <dl className="plans-upi-facts">
      <div>
        <dt>Pay to</dt>
        <dd>
          <span className="plans-upi-value">
            <b>{upi.payee_name}</b>
            <code ref={idRef} className="plans-upi-id">
              {upi.upi_id}
            </code>
          </span>
          <CopyValue value={upi.upi_id} what="UPI ID" target={idRef} />
        </dd>
      </div>
      <div>
        <dt>Amount</dt>
        <dd>
          <span className="plans-upi-value">
            <b ref={amountRef}>{formatInr(order.amount_paise)}</b>
          </span>
          <CopyValue value={String(order.amount_paise / 100)} what="amount" target={amountRef} />
        </dd>
      </div>
      <div>
        <dt>Note</dt>
        <dd>
          <span className="plans-upi-value">
            <b>
              <code ref={noteRef} className="plans-upi-ref">
                {upi.reference}
              </code>
            </b>
            <small>Add it as the note if your app asks; it helps us find your payment.</small>
          </span>
          <CopyValue value={upi.reference} what="note" target={noteRef} />
        </dd>
      </div>
    </dl>
  );
}

type QrProps = { order: OrderView; upi: UpiOrderInfo; size: number };

function QrTile({ order, upi, size }: QrProps) {
  return (
    <span className="plans-upi-tile">
      <img
        src={upiQrUrl(order.public_id, "svg")}
        width={size}
        height={size}
        alt={`QR code: pay ${formatInr(order.amount_paise)} to ${upi.payee_name} by UPI`}
      />
    </span>
  );
}

function SaveQr({ order, upi }: { order: OrderView; upi: UpiOrderInfo }) {
  return (
    <a className="btn btn-ghost btn-xs" href={upiQrUrl(order.public_id, "png")} download={`upi-${upi.reference}.png`}>
      <DownloadIcon size={14} />
      Save QR image
    </a>
  );
}

/** A label copied with the number: "UTR:", "UPI Ref No.", "Ref No:", "RRN", "UPI transaction ID". */
const UTR_LABEL = /^(?:upi[\s.]*)?(?:utr|rrn|ref(?:erence)?|(?:transaction|txn)[\s.]*id)(?:[\s.]*(?:no|number|id))?[\s.:#-]*/i;

type Utr = { kind: "short" | "done" | "long" | "letters" | "other"; digits: string };

/** What was entered, without a leading label, spaces and dashes: every digit is kept, so a wrong number shows. */
function readUtr(raw: string): Utr {
  const digits = raw.trim().replace(UTR_LABEL, "").replace(/[\s\-‐-―−]/g, "");
  if (!/^[0-9]*$/.test(digits)) return { kind: /\p{L}/u.test(digits) ? "letters" : "other", digits };
  return { kind: digits.length < UTR_DIGITS ? "short" : digits.length === UTR_DIGITS ? "done" : "long", digits };
}

function utrProblem({ kind, digits }: Utr): string | null {
  switch (kind) {
    case "long":
      return `That's ${digits.length} digits. A UTR has exactly 12: look for UPI Ref No. or UTR in the payment's details.`;
    case "letters":
      return "This looks like your app's own transaction ID. Look for the 12-digit UTR or UPI reference number.";
    case "other":
      return "A UTR has only the digits 0 to 9.";
    default:
      return null;
  }
}

type UtrFormProps = {
  order: OrderView;
  online: boolean;
  onChange: (order: OrderView) => void;
};

/** The UTR field, "I've paid · Send UTR", and Cancel this request / Pay online instead. */
function UtrForm({ order, online, onChange }: UtrFormProps) {
  const id = useId();
  const { refresh } = useAuth();
  const errorRef = useRef<HTMLParagraphElement>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [errors, setErrors] = useState(0);
  const [busy, setBusy] = useState<"send" | "cancel" | null>(null);
  // Announced only at 12 digits and when the field is left, never on each key.
  const [said, setSaid] = useState("");
  const utr = readUtr(value);
  const problem = utrProblem(utr);
  const complete = utr.kind === "done";
  const count = `${utr.digits.length} of 12 digits`;
  // After Send, the error under the field says what the echo would.
  const echo = complete ? "done" : !problem ? "count" : error ? null : "issue";

  useEffect(() => {
    if (errors > 0) errorRef.current?.focus();
  }, [errors]);

  function fail(message: string) {
    setError(message);
    setErrors((n) => n + 1);
    setSaid("");
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!complete) {
      fail(problem ?? "Enter all 12 digits.");
      return;
    }
    setBusy("send");
    setError(null);
    try {
      onChange(await billingApi.submitUtr(order.public_id, { utr: utr.digits }));
      void refresh().catch(() => undefined);
    } catch (err) {
      fail(err instanceof ApiError ? err.message : "Couldn't send the UTR. Please try again.");
      if (err instanceof ApiError && err.code === "order_not_open") {
        billingApi.order(order.public_id).then(onChange, () => undefined);
      }
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    if (busy) return;
    setBusy("cancel");
    setError(null);
    try {
      onChange(await billingApi.cancel(order.public_id));
      void refresh().catch(() => undefined);
    } catch (err) {
      fail(err instanceof ApiError ? err.message : "Couldn't cancel the request. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <form className="plans-upi-utr" onSubmit={(event) => void send(event)} noValidate>
      <label className="plans-upi-utr-label" htmlFor={`${id}-utr`}>
        UTR (12-digit UPI reference number)
      </label>
      <input
        id={`${id}-utr`}
        className="input plans-upi-utr-input"
        value={value}
        maxLength={UTR_MAX_LENGTH}
        inputMode="numeric"
        autoComplete="off"
        enterKeyHint="send"
        spellCheck={false}
        placeholder="6305 1234 5678"
        aria-invalid={error ? true : undefined}
        aria-describedby={[error ? `${id}-error` : null, `${id}-hint`, echo ? `${id}-echo` : null]
          .filter(Boolean)
          .join(" ")}
        onChange={(event) => {
          setValue(event.target.value);
          setError(null);
          setSaid(readUtr(event.target.value).kind === "done" ? "12 digits entered" : "");
        }}
        onBlur={() => {
          if (!complete && !error && value.trim()) setSaid(problem ?? count);
        }}
      />
      {error ? (
        <p ref={errorRef} id={`${id}-error`} className="error-text plans-upi-error" role="alert" tabIndex={-1}>
          <AlertCircleIcon size={13} />
          {error}
        </p>
      ) : null}
      <p id={`${id}-hint`} className="field-hint">
        12 digits, numbers only. Your UPI app shows it in the payment's details as UTR, UPI Ref No. or UPI transaction
        ID.
      </p>
      {echo ? (
        <p id={`${id}-echo`} className={echo === "count" ? "plans-upi-echo" : `plans-upi-echo plans-upi-echo-${echo}`}>
          {echo === "done" ? (
            <>
              {formatUtr(utr.digits)} <CheckIcon size={14} />
              <span className="sr-only">(all 12 digits)</span>
            </>
          ) : echo === "issue" ? (
            <>
              <AlertCircleIcon size={13} />
              {problem}
            </>
          ) : (
            count
          )}
        </p>
      ) : null}
      <span className="sr-only" aria-live="polite">
        {said}
      </span>
      <button
        type="submit"
        className="btn btn-primary plans-upi-send"
        aria-disabled={!complete || busy !== null || undefined}
        aria-busy={busy === "send" || undefined}
      >
        {busy === "send" ? <ButtonSpinner size={14} /> : null}
        I've paid · Send UTR
      </button>
      <div className="plans-upi-more">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-disabled={busy !== null || undefined}
          onClick={() => void cancel()}
        >
          {busy === "cancel" ? <ButtonSpinner size={12} /> : null}
          Cancel this request
        </button>
        {online ? (
          <Link className="plans-upi-online" to={checkoutPath(order.plan.code, order.kind)}>
            Pay online instead
          </Link>
        ) : null}
      </div>
    </form>
  );
}

/** The open request: the QR code (or Open UPI app on phones), who and what to pay, and the UTR field (10.2). */
function PendingPanel({ order, upi, context, onChange, titleRef }: PanelProps & { upi: UpiOrderInfo }) {
  const phone = useMediaQuery(PHONE_QUERY);
  const [showQr, setShowQr] = useState(false);
  const qrId = useId();
  const amount = formatInr(order.amount_paise);
  const uri = isUpiPayUri(upi.uri) ? upi.uri : null;
  const plan = `${order.plan.name} plan, 30 days${order.kind === "now" ? " from when it's confirmed" : ""}`;
  const steps = [
    <>Open any UPI app (Google Pay, PhonePe, Paytm, BHIM or your bank's) and scan the code.</>,
    <>
      Check that it shows <b>{upi.payee_name}</b> and <b>{amount}</b>, then pay with your UPI PIN.
    </>,
    <>Enter the 12-digit UTR from the payment's details below.</>,
  ];

  return (
    <>
      <div className="plans-upi-head">
        <UpiPayMark size={48} />
        <h1 className="plans-upi-title" ref={titleRef} tabIndex={-1}>
          Pay {amount} by UPI
        </h1>
        <p className="plans-upi-sub">
          {plan} · this request is open until {istDayTime(order.expires_at)} IST
        </p>
      </div>
      <section className="plans-upi" aria-label="Pay by UPI">
        {phone ? (
          <div className="plans-upi-phone">
            {uri ? (
              <>
                <a className="btn btn-primary plans-upi-open" href={uri}>
                  Open UPI app
                  <ArrowUpRightIcon size={17} />
                </a>
                <p className="plans-upi-small">Choose your UPI app, check the name and {amount}, and pay.</p>
                {/iPhone|iPod/.test(navigator.userAgent) ? (
                  <p className="plans-upi-small">
                    On iPhone this opens one UPI app. If it isn't yours, copy the UPI ID below or save the QR image.
                  </p>
                ) : null}
              </>
            ) : null}
            <h2 className="plans-upi-or">Or pay to this UPI ID</h2>
            <Facts order={order} upi={upi} />
            <button
              type="button"
              className="plans-upi-toggle"
              aria-expanded={showQr}
              aria-controls={qrId}
              onClick={() => setShowQr((open) => !open)}
            >
              {showQr ? "Hide QR code" : "Show QR code"}
              <ChevronDownIcon size={16} />
            </button>
            {showQr ? (
              <div className="plans-upi-qr" id={qrId}>
                <QrTile order={order} upi={upi} size={200} />
                <SaveQr order={order} upi={upi} />
                <p className="plans-upi-small">
                  To pay from this phone, save the image and open it from your UPI app's scanner (Upload from gallery).
                </p>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="plans-upi-grid">
            <div className="plans-upi-qr">
              <QrTile order={order} upi={upi} size={212} />
              <p className="plans-upi-small">Scan with any UPI app</p>
              <SaveQr order={order} upi={upi} />
            </div>
            <div>
              <ol className="plans-upi-steps">
                {steps.map((step, index) => (
                  <li key={index}>
                    <span className="plans-step-num" aria-hidden="true">
                      {index + 1}
                    </span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
              <Facts order={order} upi={upi} />
            </div>
          </div>
        )}
        <UtrForm order={order} online={context.payments?.available ?? false} onChange={onChange} />
      </section>
      <p className="plans-result-note">
        <InfoIcon size={16} />
        <span>
          Pay only once. If your app says the payment failed or is pending, don't pay again straight away: check your
          bank balance. Money taken for a failed payment comes back by itself, usually within a day. If the app later
          shows it as successful, enter its UTR here.
        </span>
      </p>
    </>
  );
}

type PanelProps = {
  order: OrderView;
  context: Context;
  onChange: (order: OrderView) => void;
  titleRef: RefObject<HTMLHeadingElement | null>;
};

type Tone = "info" | "danger" | "neutral";

function ResultCard({
  tone,
  mark,
  title,
  titleRef,
  children,
}: {
  tone: Tone;
  mark: ReactNode;
  title: string;
  titleRef: RefObject<HTMLHeadingElement | null>;
  children: ReactNode;
}) {
  return (
    <section className={`plans-result plans-result-${tone} plans-result-final`} aria-labelledby="plans-result-title">
      <div className="plans-result-mark" aria-hidden="true">
        {mark}
      </div>
      <h1 className="plans-result-title" id="plans-result-title" ref={titleRef} tabIndex={-1}>
        {title}
      </h1>
      {children}
    </section>
  );
}

function Slip({ order, upi, plan = false }: { order: OrderView; upi: UpiOrderInfo; plan?: boolean }) {
  return (
    <dl className="plans-receipt">
      {plan ? (
        <div>
          <dt>Plan</dt>
          <dd>
            {order.plan.name}, 30 days
            {order.kind === "now" ? <small>Starts when confirmed, in place of your current period</small> : null}
          </dd>
        </div>
      ) : null}
      <div>
        <dt>Amount</dt>
        <dd>{formatInr(order.amount_paise)}</dd>
      </div>
      <div>
        <dt>Reference</dt>
        <dd className="plans-upi-mono">{upi.reference}</dd>
      </div>
      {upi.utr ? (
        <div>
          <dt>UTR</dt>
          <dd className="plans-upi-mono">{formatUtr(upi.utr)}</dd>
        </div>
      ) : null}
      {plan && upi.submitted_at ? (
        <div>
          <dt>Sent</dt>
          <dd>{istMoment(upi.submitted_at)}</dd>
        </div>
      ) : null}
    </dl>
  );
}

function Contact({ context, children }: { context: Context; children: (email: string) => ReactNode }) {
  const email = context.payments?.contact_email;
  return email ? <p className="plans-result-foot">{children(email)}</p> : null;
}

/** "We're checking your payment" while the owner looks for the credit; the page checks again every 30 s. */
function SubmittedCard({ order, upi, context, onChange, titleRef }: PanelProps & { upi: UpiOrderInfo }) {
  const { user } = useAuth();
  const [stopped, setStopped] = useState(false);
  const [round, setRound] = useState(0);
  const emailed = Boolean(user?.email && context.payments?.email_reminders);

  useEffect(() => {
    const controller = new AbortController();
    const started = Date.now();
    const check = () => billingApi.order(order.public_id, false, controller.signal).then(onChange, () => undefined);
    if (round > 0) void check();
    const timer = window.setInterval(() => {
      if (Date.now() - started >= WATCH_MS) {
        window.clearInterval(timer);
        setStopped(true);
      } else if (!document.hidden) void check();
    }, POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [order.public_id, onChange, round]);

  return (
    <>
      <ResultCard tone="info" mark={<PaymentCheckMark />} title="We're checking your payment" titleRef={titleRef}>
        <p className="plans-result-text">
          We'll match your UTR with the {formatInr(order.amount_paise)} that reached <b>{upi.payee_name}</b>'s account.
          This usually takes a few hours during the day (Indian time). Your {order.plan.name} plan starts as soon as
          it's confirmed.
        </p>
        <Slip order={order} upi={upi} plan />
        <p className="plans-result-note">
          <InfoIcon size={16} />
          <span>
            Please don't pay again.{" "}
            {emailed
              ? "We'll email you when it's confirmed."
              : "You can close this page: you'll see it here and in Settings → Plan & billing."}
          </span>
        </p>
        <div className="plans-result-actions">
          {stopped ? (
            <button
              type="button"
              className="btn"
              onClick={() => {
                setStopped(false);
                setRound((n) => n + 1);
              }}
            >
              <RefreshIcon size={16} />
              Check again
            </button>
          ) : null}
          {user?.plan.access ? (
            <>
              <Link className="btn" to="/plans">
                Back to plans
              </Link>
              <Link className="btn btn-primary" to="/">
                Back to chat
                <ArrowRightIcon size={16} />
              </Link>
            </>
          ) : (
            <Link className="btn btn-primary" to="/plans">
              Done
            </Link>
          )}
        </div>
      </ResultCard>
      <Contact context={context}>
        {(email) => (
          <>
            Wrong UTR? Write to <a href={`mailto:${email}`}>{email}</a> with the right one.
          </>
        )}
      </Contact>
    </>
  );
}

function StartAgain({ order }: { order: OrderView }) {
  return (
    <div className="plans-result-actions">
      <Link className="btn" to="/plans">
        Back to plans
      </Link>
      <Link className="btn btn-primary" to={checkoutPath(order.plan.code, order.kind)}>
        <RefreshIcon size={16} />
        Start a new payment
      </Link>
    </div>
  );
}

type UpiOrderProps = {
  order: OrderView;
  state: UpiState;
  onChange: (order: OrderView) => void;
};

/** The order page of a direct-UPI order (docs/briefs/upi-direct.md 10.2): pay, send the UTR, then the owner's check. */
export default function UpiOrder({ order, state, onChange }: UpiOrderProps) {
  const context = usePageContext();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const upi = order.upi as UpiOrderInfo;

  useEffect(() => {
    titleRef.current?.focus();
  }, [state]);

  const panel = { order, context, onChange, titleRef };
  switch (state) {
    case "pending":
      return <PendingPanel {...panel} upi={upi} />;
    case "submitted":
      return <SubmittedCard {...panel} upi={upi} />;
    case "rejected":
      return (
        <>
          <ResultCard
            tone="danger"
            mark={<AlertCircleIcon size={32} />}
            title="We couldn't confirm your payment"
            titleRef={titleRef}
          >
            {upi.rejection ? <p className="plans-result-text">{upi.rejection.message}</p> : null}
            {upi.rejection?.note ? (
              <p className="plans-result-note">
                <InfoIcon size={16} />
                <span>
                  Note from {context.business ?? "us"}: {upi.rejection.note}
                </span>
              </p>
            ) : null}
            <Slip order={order} upi={upi} />
            <StartAgain order={order} />
          </ResultCard>
          <Contact context={context}>
            {(email) => (
              <>
                Questions? Write to <a href={`mailto:${email}`}>{email}</a>.
              </>
            )}
          </Contact>
        </>
      );
    default:
      return (
        <ResultCard
          tone="neutral"
          mark={<ClockIcon size={32} />}
          title={state === "cancelled" ? "This payment request was cancelled" : "This payment request has expired"}
          titleRef={titleRef}
        >
          <p className="plans-result-text">
            No UTR was sent, so your plan hasn't changed. If you already paid, start a new payment for the same plan and
            enter that payment's UTR there.
          </p>
          <StartAgain order={order} />
        </ResultCard>
      );
  }
}
