import { useEffect, useId, useRef, useState, type FormEvent, type Ref } from "react";
import { accountEmailApi, ApiError, authErrorCode } from "../../api/client";
import type { AccountInfo } from "../../api/types";
import { formatRelativeTime } from "../../lib/format";
import EmailField from "../auth/EmailField";
import { AlertIcon, ClockIcon, InfoIcon, MailIcon } from "../ui/icons";
import PasswordInput from "../ui/PasswordInput";
import { ButtonSpinner } from "../ui/Spinner";
import StepUp, { hasStepUp } from "./StepUp";

type EmailFormProps = {
  account: AccountInfo;
  id: string;
  /** Back from Google's step-up: no password field. */
  confirmedWithGoogle: boolean;
  onSent: () => void;
  onCancel: () => void;
  onStepUpNeeded: () => void;
};

/** The new address and, for an account with a password, the password (functional 4.8, visual 7.2). */
function EmailForm({ account, id, confirmedWithGoogle, onSent, onCancel, onStepUpNeeded }: EmailFormProps) {
  const passwordRef = useRef<HTMLInputElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const needsPassword = account.email_change_needs === "password";

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    const missingEmail = !email.trim();
    const missingPassword = needsPassword && !password;
    setEmailError(missingEmail ? "Enter the new address." : null);
    setPasswordError(missingPassword ? "Enter your password." : null);
    setError(null);
    if (missingEmail) {
      document.getElementById(`${id}-email`)?.focus();
      return;
    }
    if (missingPassword) {
      passwordRef.current?.focus();
      return;
    }
    setSaving(true);
    try {
      await accountEmailApi.change({ email: email.trim(), password: needsPassword ? password : null });
      onSent();
    } catch (err) {
      setSaving(false);
      const code = authErrorCode(err);
      if (code === "wrong_password") {
        setPasswordError(err instanceof ApiError ? err.message : "Your password is incorrect.");
        passwordRef.current?.focus();
      } else if (code === "reauth_required") {
        onStepUpNeeded();
      } else if (code === null && err instanceof ApiError && err.status === 422) {
        setEmailError(err.message);
        document.getElementById(`${id}-email`)?.focus();
      } else {
        setError(err instanceof ApiError ? err.message : "Couldn't send the link. Please try again.");
      }
    }
  }

  return (
    <form
      id={id}
      className="settings-inline-form"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
      aria-label={account.email ? "Change email" : "Add email"}
    >
      {confirmedWithGoogle ? <p className="settings-inline-note">Confirmed with Google · 5 minutes</p> : null}
      <EmailField
        id={`${id}-email`}
        label="Email address"
        value={email}
        onChange={(value) => {
          setEmail(value);
          setEmailError(null);
        }}
        error={emailError}
        hint="We'll email a link to this address. It becomes your email when you open it while signed in."
        autoFocus
      />
      {needsPassword ? (
        <div className="field">
          <PasswordInput
            ref={passwordRef}
            label="Your password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setPasswordError(null);
            }}
            aria-invalid={passwordError ? true : undefined}
            aria-describedby={passwordError ? `${id}-password-error` : undefined}
          />
          {passwordError ? (
            <p className="error-text" id={`${id}-password-error`} role="alert">
              <AlertIcon size={13} />
              {passwordError}
            </p>
          ) : null}
        </div>
      ) : null}
      {error ? (
        <p className="error-text" role="alert">
          <AlertIcon size={13} />
          {error}
        </p>
      ) : null}
      <div className="settings-actions">
        <button type="submit" className="btn btn-primary btn-sm" aria-busy={saving || undefined}>
          {saving ? <ButtonSpinner size={14} /> : null}
          Send confirmation link
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}

type PendingProps = {
  account: AccountInfo;
  /** The notice, focusable (tabIndex -1): it takes focus when a sent link brings it. */
  ref: Ref<HTMLDivElement>;
  onAction: () => void;
  /** After Cancel: the notice and its buttons go, so focus moves on. */
  onCancelled: () => void;
  onChanged: () => void;
};

/** "Waiting for you to confirm …" with Resend and Cancel. */
function Pending({ account, ref, onAction, onCancelled, onChanged }: PendingProps) {
  const pending = account.pending_email;
  const [busy, setBusy] = useState<"resend" | "cancel" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resent, setResent] = useState(false);
  if (!pending) return null;

  async function act(kind: "resend" | "cancel") {
    if (busy) return;
    onAction();
    setBusy(kind);
    setError(null);
    try {
      if (kind === "resend") await accountEmailApi.resend();
      else await accountEmailApi.cancel();
      setResent(kind === "resend");
      if (kind === "cancel") onCancelled();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  const sent = pending.sent_at
    ? `We emailed a link ${formatRelativeTime(pending.sent_at)}.`
    : "We're sending a link.";
  const until = account.email ? ` Until then your email stays ${account.email}.` : "";
  return (
    <>
      <div ref={ref} className="ui-notice ui-notice-info settings-pending" role="status" tabIndex={-1}>
        <ClockIcon size={18} />
        <span className="ui-notice-text">
          Waiting for you to confirm <b>{pending.address}</b>
          <small>
            {sent} Open it while signed in here; it works for 24 hours.{until}
            {resent ? " Sent again." : ""}
          </small>
        </span>
        <span className="settings-pending-actions">
          <button
            type="button"
            className="btn btn-xs"
            onClick={() => void act("resend")}
            aria-busy={busy === "resend" || undefined}
          >
            {busy === "resend" ? <ButtonSpinner size={12} /> : null}
            Resend
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-xs"
            onClick={() => void act("cancel")}
            aria-busy={busy === "cancel" || undefined}
          >
            Cancel
          </button>
        </span>
      </div>
      {error ? (
        <p className="error-text settings-error" role="alert">
          <AlertIcon size={13} />
          {error}
        </p>
      ) : null}
    </>
  );
}

type EmailSectionProps = {
  account: AccountInfo;
  /** Back from Google's step-up for a change of email: the form opens at once. */
  resumeChange: boolean;
  /** An action starts (a form opens or closes, Resend, Cancel): Google's result no longer applies. */
  onAction: () => void;
  onChanged: () => void;
};

/** Settings → Account → Email: add or change it (a Google step-up first without a password); the pending one. */
export default function EmailSection({ account, resumeChange, onAction, onChanged }: EmailSectionProps) {
  const formId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const pendingRef = useRef<HTMLDivElement>(null);
  /** The account a link was sent from: its reload brings the pending notice, which then takes focus. */
  const sentFrom = useRef<AccountInfo | null>(null);
  const [open, setOpen] = useState(resumeChange);
  const [forceStepUp, setForceStepUp] = useState(false);
  const allowed = account.email_change_needs !== "not_allowed";
  const stepUp = account.email_change_needs === "google" && (forceStepUp || !hasStepUp(account.stepup_until));

  useEffect(() => {
    if (sentFrom.current === null || sentFrom.current === account) return;
    sentFrom.current = null;
    if (account.pending_email) pendingRef.current?.focus();
  }, [account]);

  return (
    <div className="settings-group">
      <div className="settings-row settings-row-wrap">
        <div className="settings-label">
          <h3>Email</h3>
          <p className="settings-desc">
            {account.email
              ? "Sign in with it, reset your password, and get receipts and plan reminders."
              : "Add an email to sign in with it, reset your password and get receipts and plan reminders."}
          </p>
        </div>
        {allowed ? (
          <button
            ref={toggleRef}
            type="button"
            className="btn btn-sm"
            aria-expanded={open}
            aria-controls={open ? formId : undefined}
            onClick={() => {
              onAction();
              setForceStepUp(false);
              setOpen((value) => !value);
            }}
          >
            <MailIcon size={15} />
            {account.email ? "Change email…" : "Add email…"}
          </button>
        ) : null}
      </div>
      {!allowed ? (
        <p className="ui-notice settings-pending">
          <InfoIcon size={18} />
          <span className="ui-notice-text">
            To change your email, set a password first with Forgot password on the sign-in page.
          </span>
        </p>
      ) : null}
      {open && stepUp ? (
        <div id={formId}>
          <StepUp
            text="For your safety, confirm it's you with Google before changing your email."
            returnParam="change=email"
          />
        </div>
      ) : null}
      {open && !stepUp ? (
        <EmailForm
          account={account}
          id={formId}
          confirmedWithGoogle={account.email_change_needs === "google"}
          onSent={() => {
            sentFrom.current = account;
            toggleRef.current?.focus();
            setOpen(false);
            onChanged();
          }}
          onCancel={() => {
            setOpen(false);
            toggleRef.current?.focus();
          }}
          onStepUpNeeded={() => setForceStepUp(true)}
        />
      ) : null}
      <Pending
        ref={pendingRef}
        account={account}
        onAction={onAction}
        onCancelled={() => toggleRef.current?.focus()}
        onChanged={onChanged}
      />
    </div>
  );
}
