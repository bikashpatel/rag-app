import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { accountEmailApi, ApiError } from "../../api/client";
import type { AccountInfo } from "../../api/types";
import { formatDate } from "../../lib/format";
import GoogleButton, { GoogleMark } from "../auth/GoogleButton";
import { AlertCircleIcon, AlertIcon, CheckCircleIcon, ClockIcon, InfoIcon, KeyIcon } from "../ui/icons";
import SecurityMark from "../ui/marks/SecurityMark";
import PasswordInput from "../ui/PasswordInput";
import { ButtonSpinner } from "../ui/Spinner";
import PasswordForm from "./PasswordForm";
import StepUp, { hasStepUp } from "./StepUp";

/** What Google's return to Settings → Account (`?google=`) says, in this group (functional 13.7, visual 7.3). */
const GOOGLE_RESULTS: Record<string, { tone: "success" | "danger" | "warning" | "neutral"; text: string }> = {
  linked: { tone: "success", text: "Google is connected." },
  in_use: { tone: "danger", text: "That Google account is already connected to another AskNuvexusDoc account." },
  already_linked: { tone: "neutral", text: "A Google account is already connected to your account." },
  denied: { tone: "neutral", text: "Google sign-in was cancelled." },
  error: { tone: "danger", text: "We couldn't sign you in with Google. Try again." },
  expired: { tone: "warning", text: "That took too long. Try again." },
  reauth_mismatch: { tone: "danger", text: "That's a different Google account from the one connected here." },
};

function ResultNotice({ result }: { result: string }) {
  const known = GOOGLE_RESULTS[result];
  if (!known) return null;
  const icons: Record<typeof known.tone, ReactNode> = {
    success: <CheckCircleIcon size={18} />,
    danger: <AlertCircleIcon size={18} />,
    warning: <ClockIcon size={18} />,
    neutral: <InfoIcon size={18} />,
  };
  const tone = known.tone === "neutral" ? "ui-notice" : `ui-notice ui-notice-${known.tone}`;
  return (
    <p className={`${tone} settings-result`} role="status">
      {icons[known.tone]}
      <span className="ui-notice-text">{known.text}</span>
    </p>
  );
}

/** A result line that takes focus when it appears. */
function Done({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <p ref={ref} className="ui-notice ui-notice-success settings-result" role="status" tabIndex={-1}>
      <CheckCircleIcon size={15} />
      <span className="ui-notice-text">{children}</span>
    </p>
  );
}

type Row = { icon: ReactNode; name: string; line: ReactNode; action?: ReactNode; more?: ReactNode };

function Method({ icon, name, line, action, more }: Row) {
  return (
    <li className="settings-method">
      <div className="settings-method-row">
        <span className="settings-method-icon" aria-hidden="true">
          {icon}
        </span>
        <div className="settings-method-text">
          <b>{name}</b>
          <span>{line}</span>
        </div>
        {action ? <div className="settings-method-end">{action}</div> : null}
      </div>
      {more ? <div className="settings-method-more">{more}</div> : null}
    </li>
  );
}

type PasswordRowProps = {
  account: AccountInfo;
  minimum: number;
  resumeSetup: boolean;
  onAction: () => void;
  onChanged: () => void;
};

/** Password: Change password… as before, or, without one, Set a password after Google's step-up (4.9). */
function PasswordRow({ account, minimum, resumeSetup, onAction, onChanged }: PasswordRowProps) {
  const formId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(resumeSetup && !account.has_password);
  const [forceStepUp, setForceStepUp] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const setup = !account.has_password;
  const stepUp = setup && (forceStepUp || !hasStepUp(account.stepup_until));

  let more: ReactNode = null;
  if (open && stepUp) {
    more = (
      <div id={formId}>
        <StepUp
          text="For your safety, confirm it's you with Google before adding a password."
          returnParam="setup=password"
        />
      </div>
    );
  } else if (open) {
    more = (
      <PasswordForm
        id={formId}
        mode={setup ? "setup" : "change"}
        minimum={minimum}
        onChanged={() => {
          setOpen(false);
          setDone(
            setup
              ? "Password added. You can now sign in with your email and this password."
              : "Your password was changed. Your other sessions were signed out.",
          );
          if (setup) onChanged();
        }}
        onCancel={() => {
          setOpen(false);
          toggleRef.current?.focus();
        }}
        onStepUpNeeded={() => setForceStepUp(true)}
      />
    );
  } else if (done) {
    more = <Done>{done}</Done>;
  }

  return (
    <Method
      icon={<KeyIcon size={18} />}
      name="Password"
      line={setup ? "Not set. Add one to sign in without Google." : "Changing it signs you out everywhere else."}
      action={
        <button
          ref={toggleRef}
          type="button"
          className="btn btn-sm"
          aria-expanded={open}
          aria-controls={open ? formId : undefined}
          onClick={() => {
            onAction();
            setDone(null);
            setForceStepUp(false);
            setOpen((value) => !value);
          }}
        >
          <KeyIcon size={15} />
          {setup ? "Set a password" : "Change password…"}
        </button>
      }
      more={more}
    />
  );
}

/** Connect: the password, then Google (4.6). */
function ConnectGoogle({ onCancel }: { onCancel: () => void }) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  function canStart(): boolean {
    if (password) return true;
    setError("Enter your password.");
    inputRef.current?.focus();
    return false;
  }

  return (
    <div className="settings-confirm" id={id}>
      <p>Enter your password, then choose your Google account.</p>
      <div className="settings-inline-form">
        <PasswordInput
          ref={inputRef}
          label="Your password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
            setError(null);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          autoFocus
        />
        {error ? (
          <p className="error-text" id={`${id}-error`} role="alert">
            <AlertIcon size={13} />
            {error}
          </p>
        ) : null}
        <div className="settings-actions">
          <GoogleButton intent="link" password={password} size="sm" canStart={canStart} onError={setError} />
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

/** Disconnect: confirmed with the password (4.6). */
function DisconnectGoogle({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const id = useId();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    if (!password) {
      setError("Enter your password.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await accountEmailApi.unlinkGoogle({ password });
      onDone();
    } catch (err) {
      setSaving(false);
      setError(err instanceof ApiError ? err.message : "Couldn't disconnect Google. Please try again.");
    }
  }

  return (
    <form
      className="settings-confirm"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
      aria-label="Disconnect Google"
    >
      <p>Enter your password to disconnect Google sign-in.</p>
      <div className="settings-inline-form">
        <PasswordInput
          label="Your password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
            setError(null);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          autoFocus
        />
        {error ? (
          <p className="error-text" id={`${id}-error`} role="alert">
            <AlertIcon size={13} />
            {error}
          </p>
        ) : null}
        <div className="settings-actions">
          <button type="submit" className="btn btn-danger btn-sm" aria-busy={saving || undefined}>
            {saving ? <ButtonSpinner size={14} /> : null}
            Disconnect Google
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    </form>
  );
}

type GoogleRowProps = { account: AccountInfo; onAction: () => void; onChanged: () => void };

/** Google: connected (Disconnect…) or not (Connect Google…, for an account with a password). */
function GoogleRow({ account, onAction, onChanged }: GoogleRowProps) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const google = account.google;
  const cancel = () => {
    setOpen(false);
    toggleRef.current?.focus();
  };
  const toggle = () => {
    onAction();
    setDone(false);
    setOpen((value) => !value);
  };

  if (google) {
    let blocked: string | null = null;
    if (!account.has_password) blocked = "Set a password first, so you can still sign in without Google.";
    else if (!account.email) blocked = "Confirm an email first, so you can still sign in without Google.";
    return (
      <Method
        icon={<GoogleMark />}
        name="Google"
        line={`Connected as ${google.email} since ${formatDate(google.linked_at)}`}
        action={
          <button
            ref={toggleRef}
            type="button"
            className="btn btn-sm"
            aria-expanded={open}
            disabled={blocked !== null}
            onClick={toggle}
          >
            Disconnect…
          </button>
        }
        more={
          blocked ??
          (open ? (
            <DisconnectGoogle
              onDone={() => {
                setOpen(false);
                setDone(true);
                onChanged();
              }}
              onCancel={cancel}
            />
          ) : null)
        }
      />
    );
  }
  return (
    <Method
      icon={<GoogleMark />}
      name="Google"
      line="Not connected. Sign in with your Google account too."
      action={
        account.has_password ? (
          <button
            ref={toggleRef}
            type="button"
            className="btn btn-sm"
            aria-expanded={open}
            onClick={toggle}
          >
            Connect Google…
          </button>
        ) : undefined
      }
      more={
        open ? (
          <ConnectGoogle onCancel={cancel} />
        ) : done ? (
          <Done>Google sign-in is disconnected.</Done>
        ) : null
      }
    />
  );
}

type SignInMethodsProps = {
  account: AccountInfo;
  /** GET /auth/config: Google sign-in configured, and the password policy's minimum. */
  googleSignin: boolean;
  minimum: number;
  /** Google's return (`?google=`): its result (until an action starts), and the form a step-up was for. */
  googleResult: string | null;
  resumeSetup: boolean;
  /** An action starts (a form opens or closes): Google's result no longer applies. */
  onAction: () => void;
  onChanged: () => void;
};

/** Settings → Account → Sign-in methods: the password and Google, each a way in with its action (visual 7.3). */
export default function SignInMethods({
  account,
  googleSignin,
  minimum,
  googleResult,
  resumeSetup,
  onAction,
  onChanged,
}: SignInMethodsProps) {
  const headingId = useId();
  return (
    <div className="settings-group">
      <div className="settings-group-head">
        <SecurityMark size={36} />
        <h3 id={headingId}>Sign-in methods</h3>
        <p className="settings-desc">Keep at least one way in that you can use.</p>
      </div>
      <ul className="settings-methods" aria-labelledby={headingId}>
        <PasswordRow
          account={account}
          minimum={minimum}
          resumeSetup={resumeSetup}
          onAction={onAction}
          onChanged={onChanged}
        />
        {googleSignin || account.google ? (
          <GoogleRow account={account} onAction={onAction} onChanged={onChanged} />
        ) : null}
      </ul>
      {googleResult ? <ResultNotice result={googleResult} /> : null}
    </div>
  );
}
