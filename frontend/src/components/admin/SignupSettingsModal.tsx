import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import type { SignupSettings, SignupStatus } from "../../api/types";
import { formatNumber } from "../../lib/format";
import Modal from "../ui/Modal";
import { ButtonSpinner } from "../ui/Spinner";
import { AlertCircleIcon, AlertIcon, CheckCircleIcon } from "../ui/icons";
import { errorMessage, saveSignupSettings, sendTestEmail } from "./adminApi";
import { SwitchField } from "./Field";
import type { PageCheck } from "./policyPages";
import { GOOGLE_SETTINGS, listWords, missingEmailSettings, openingWarnings, policyWarning } from "./signupChecks";

type SignupSettingsModalProps = {
  status: SignupStatus;
  /** The privacy policy and terms (null while they are checked). */
  pages: PageCheck[] | null;
  onRecheckPages: () => void;
  onClose: () => void;
  onSaved: (status: SignupStatus) => void;
};

const notConfigured = (settings: readonly string[]) =>
  `Not configured: set ${listWords(settings)} in .env, then restart the backend.`;

/** Admin → Users → Sign-up → Edit…: open or close sign-up, the two methods, and the checks before opening. */
export default function SignupSettingsModal({
  status,
  pages,
  onRecheckPages,
  onClose,
  onSaved,
}: SignupSettingsModalProps) {
  const formId = useId();
  const openSwitch = useRef<HTMLDivElement>(null);
  const [form, setForm] = useState<SignupSettings>(status.settings);
  const [asking, setAsking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const warnings = openingWarnings(status, pages);
  const emailMissing = missingEmailSettings(status);

  function setOpen(open: boolean) {
    if (open && !status.settings.open && warnings.length > 0) {
      setAsking(true);
      return;
    }
    setAsking(false);
    setForm((current) => ({ ...current, open }));
  }

  function openAnyway() {
    setAsking(false);
    setForm((current) => ({ ...current, open: true }));
    openSwitch.current?.querySelector<HTMLInputElement>("input")?.focus();
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      onSaved(await saveSignupSettings(form));
    } catch (err) {
      setError(errorMessage(err, "Couldn't save the sign-up settings. Please try again."));
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      title="Sign-up"
      onClose={onClose}
      width={720}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            type="submit"
            form={formId}
            className="btn btn-primary"
            disabled={saving}
            aria-busy={saving || undefined}
          >
            {saving ? <ButtonSpinner size={14} /> : null}
            Save
          </button>
        </>
      }
    >
      <form id={formId} className="admin-form admin-signup-form" onSubmit={onSubmit} noValidate>
        <fieldset className="admin-form-section">
          <legend>Availability</legend>
          <div ref={openSwitch}>
            <SwitchField
              label="Open sign-up"
              hint="Anyone can create an account with the methods below. New accounts have no plan until they pay. Closing it never affects existing accounts."
              checked={form.open}
              onChange={setOpen}
            />
          </div>
          {asking ? (
            <div className="ui-notice ui-notice-warning admin-open-warning" role="alert">
              <AlertIcon size={15} />
              <span className="ui-notice-text">
                <b>
                  {warnings.length === 1
                    ? "1 thing isn't ready."
                    : `${formatNumber(warnings.length)} things aren't ready.`}
                </b>
                <ul>
                  {warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
                <button type="button" className="btn btn-sm" onClick={openAnyway}>
                  Open anyway
                </button>
              </span>
            </div>
          ) : null}
        </fieldset>

        <fieldset className="admin-form-section">
          <legend>Methods</legend>
          <SwitchField
            label="Email and password"
            hint={
              emailMissing.length
                ? notConfigured(emailMissing)
                : "People confirm their address from an email before the account exists."
            }
            checked={form.email && emailMissing.length === 0}
            disabled={emailMissing.length > 0}
            onChange={(email) => setForm((current) => ({ ...current, email }))}
          />
          <SwitchField
            label="Continue with Google"
            hint={
              status.google_configured
                ? "People sign up with their Google account; an address Google hasn't verified gets our confirmation email."
                : notConfigured(GOOGLE_SETTINGS)
            }
            checked={form.google && status.google_configured}
            disabled={!status.google_configured}
            onChange={(google) => setForm((current) => ({ ...current, google }))}
          />
        </fieldset>

        <fieldset className="admin-form-section">
          <legend>Checks</legend>
          <Checks status={status} pages={pages} onRecheckPages={onRecheckPages} />
        </fieldset>

        {error ? (
          <div className="ui-notice ui-notice-danger" role="alert">
            <AlertCircleIcon size={15} />
            <span className="ui-notice-text">{error}</span>
          </div>
        ) : null}
      </form>
    </Modal>
  );
}

type CheckState = "done" | "todo" | "warn";

const CHECK_PREFIX: Record<CheckState, string> = { done: "Done: ", todo: "Not set up: ", warn: "Warning: " };

type CheckRowProps = { state: CheckState; name: string; children: ReactNode; action?: ReactNode };

function CheckRow({ state, name, children, action }: CheckRowProps) {
  return (
    <li className="admin-check-row">
      {state === "done" ? (
        <CheckCircleIcon size={20} className="admin-check-done" />
      ) : state === "warn" ? (
        <AlertIcon size={20} className="admin-check-warn" />
      ) : (
        <span className="ui-dashed admin-check-todo" aria-hidden="true" />
      )}
      <span className="admin-check-text">
        <span className="sr-only">{CHECK_PREFIX[state]}</span>
        {name}
        {children}
      </span>
      {action}
    </li>
  );
}

function plural(count: number, one: string, many: string): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

type ChecksProps = { status: SignupStatus; pages: PageCheck[] | null; onRecheckPages: () => void };

/** The going-live rows: what sign-up needs (mail, Turnstile, Google) and what should be true before opening it. */
function Checks({ status, pages, onRecheckPages }: ChecksProps) {
  const [testing, setTesting] = useState(false);
  const testButton = useRef<HTMLButtonElement>(null);
  const wasTesting = useRef(false);
  useEffect(() => {
    if (wasTesting.current && !testing) testButton.current?.focus();
    wasTesting.current = testing;
  }, [testing]);
  const policy = policyWarning(pages);
  const mailLine = [
    "Configured",
    `${formatNumber(status.mail_sent_today)} of ${formatNumber(status.mail_daily_limit)} sent today`,
    status.mail_queued ? `${formatNumber(status.mail_queued)} queued` : "none queued",
    status.mail_last_error ? `last error: ${status.mail_last_error}` : "no errors",
  ].join(" · ");
  const bounceLine = [
    "Resend webhook set",
    `${plural(status.bounces_today, "bounce", "bounces")} today`,
    ...(status.complaints_7d ? [`${plural(status.complaints_7d, "complaint", "complaints")} in 7 days`] : []),
  ].join(" · ");
  return (
    <ul className="admin-checklist-rows">
      <CheckRow
        state={status.mail_configured ? (status.mail_last_error ? "warn" : "done") : "todo"}
        name="Email"
        action={
          status.mail_configured && !testing ? (
            <button ref={testButton} type="button" className="btn btn-sm" onClick={() => setTesting(true)}>
              Send a test email…
            </button>
          ) : null
        }
      >
        <small>
          {status.mail_configured
            ? mailLine
            : "Not configured: set MAIL_SMTP_HOST and MAIL_FROM in .env, then restart the backend and worker."}
        </small>
        {testing ? <TestEmail onClose={() => setTesting(false)} /> : null}
      </CheckRow>
      <CheckRow state={status.mail_webhook_configured && !status.public_paused ? "done" : "warn"} name="Bounce reports">
        <small>
          {status.mail_webhook_configured ? bounceLine : "Not set up: add the webhook and MAIL_WEBHOOK_SECRET."}
          {status.public_paused ? " Sign-up emails are paused until 00:00 UTC: too many bounces today." : null}
        </small>
      </CheckRow>
      <CheckRow state={status.turnstile_configured ? "done" : "todo"} name="Turnstile">
        <small>
          {status.turnstile_configured
            ? "Configured"
            : "Not configured: set TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY in .env. Email sign-up and password resets need it."}
        </small>
      </CheckRow>
      <CheckRow state={status.google_configured ? "done" : "todo"} name="Google sign-in">
        <small>
          {status.google_configured ? "Configured" : "Not configured. Google's setup steps are in the README."}
        </small>
      </CheckRow>
      <CheckRow state={status.warnings.includes("real_ip") ? "warn" : "done"} name="Visitor addresses">
        <small>
          {status.warnings.includes("real_ip")
            ? "Visitors' real addresses aren't reaching the app, so every per-visitor limit acts as one shared limit. Set NGINX_REAL_IP_FROM."
            : "Visitors' real addresses reach the app."}
        </small>
      </CheckRow>
      <CheckRow state={status.payments_live ? "done" : "warn"} name="Online payments">
        <small>{status.payments_live ? "Live" : "Not live: new accounts can't pay yet."}</small>
      </CheckRow>
      <CheckRow state={pages === null ? "todo" : policy ? "warn" : "done"} name="Privacy policy and terms">
        <small>
          {pages === null ? (
            "Checking the pages…"
          ) : policy ? (
            <>
              {policy} Publish the real pages before opening sign-up, then{" "}
              <button type="button" className="admin-link-button" onClick={onRecheckPages}>
                check again
              </button>
              .
            </>
          ) : (
            "Published"
          )}
        </small>
      </CheckRow>
      <CheckRow state={status.warnings.includes("admin_password_weak") ? "warn" : "done"} name="Admin password">
        <small>
          {status.warnings.includes("admin_password_weak")
            ? "Short or common: change ADMIN_PASSWORD in .env."
            : "Strong enough"}
        </small>
      </CheckRow>
    </ul>
  );
}

/** An address and Send, inside the Email row; the result under it. */
function TestEmail({ onClose }: { onClose: () => void }) {
  const id = useId();
  const [to, setTo] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    if (sending) return;
    const address = to.trim();
    setSent(null);
    if (!address.includes("@")) {
      setError("Enter an email address.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      await sendTestEmail({ to: address });
      setSent(`Queued a test email to ${address}. It should arrive within a minute.`);
    } catch (err) {
      setError(errorMessage(err, "Couldn't send the test email. Please try again."));
    } finally {
      setSending(false);
    }
  }

  /** Enter sends the test, not the dialog's form. */
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    void send();
  }

  return (
    <span className="admin-test-mail">
      <label className="sr-only" htmlFor={id}>
        Send a test email to
      </label>
      <span className="admin-test-mail-row">
        <input
          id={id}
          type="email"
          className="input"
          placeholder="Send a test email to…"
          autoComplete="email"
          autoFocus
          value={to}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          onChange={(e) => setTo(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="btn btn-sm"
          disabled={sending}
          aria-busy={sending || undefined}
          onClick={() => void send()}
        >
          {sending ? <ButtonSpinner size={13} /> : null}
          Send
        </button>
        <button type="button" className="btn btn-sm btn-ghost" aria-label="Cancel the test email" onClick={onClose}>
          Cancel
        </button>
      </span>
      {error ? (
        <span id={`${id}-error`} className="error-text" role="alert">
          <AlertCircleIcon size={13} />
          {error}
        </span>
      ) : null}
      <span className="admin-test-mail-sent" role="status">
        {sent ? (
          <>
            <CheckCircleIcon size={13} />
            {sent}
          </>
        ) : null}
      </span>
    </span>
  );
}
