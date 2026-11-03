import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, authApi, authErrorCode, passwordRefusal, type AuthErrorCode } from "../api/client";
import type { AuthConfig, PasswordReason } from "../api/types";
import { useAuthConfig } from "../auth/AuthContext";
import EmailField from "../components/auth/EmailField";
import GoogleButton, { OrRule } from "../components/auth/GoogleButton";
import NameField from "../components/auth/NameField";
import PasswordRules from "../components/auth/PasswordRules";
import TermsCheck from "../components/auth/TermsCheck";
import Turnstile, { TurnstileWait } from "../components/auth/Turnstile";
import { AlertCircleIcon, CheckIcon, InfoIcon, MailCheckIcon, RefreshIcon } from "../components/ui/icons";
import PasswordInput from "../components/ui/PasswordInput";
import Spinner, { ButtonSpinner } from "../components/ui/Spinner";
import { safeNextPath, take } from "../lib/authDocument";
import { nameProblem, nameRefusal, normalizeName } from "../lib/names";
import { FormError, messageOf, rememberGoogleNext, useChallenge, type Challenge } from "./authShared";
import AuthSplit from "./login/AuthSplit";

/** The address and the send times of this tab's sign-up, so a reload keeps "Check your inbox". */
const SENT_KEY = "rag.auth.signupSent";
/** The server sends one mail a minute, three an hour per address (docs/briefs/signup-auth.md 4.3): mirrored here. */
const COOLDOWN_MS = 60_000;
const HOUR_MS = 3_600_000;
const SENDS_PER_HOUR = 3;
const LEDE = "Chat with your documents. Choose a plan after you sign up.";
/** Enough to catch a slip before the server's full check (docs/briefs/signup-auth.md 3.2). */
const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
/** With these refusals the form offers Google too (the other way in needs no email). */
const GOOGLE_HELPS: ReadonlySet<AuthErrorCode> = new Set([
  "too_many_attempts",
  "mail_unavailable",
  "challenge_unavailable",
]);

type Sent = { address: string; sends: number[] };

function readSent(): Sent | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(SENT_KEY) ?? "null") as Partial<Sent> | null;
    if (typeof value?.address !== "string" || !Array.isArray(value.sends)) return null;
    return { address: value.address, sends: value.sends.filter((time): time is number => typeof time === "number") };
  } catch {
    return null;
  }
}

function writeSent(sent: Sent): void {
  try {
    sessionStorage.setItem(SENT_KEY, JSON.stringify(sent));
  } catch {
    /* storage unavailable: a reload shows the form again */
  }
}

/** The send times of the last hour. */
function recentSends(sent: Sent, now: number): number[] {
  return sent.sends.filter((time) => now - time < HOUR_MS);
}

/** Milliseconds until another mail to this address may go (0: now). */
function cooldown(sent: Sent | null, address: string, now: number): number {
  if (!sent || sent.address.trim().toLowerCase() !== address.trim().toLowerCase() || sent.sends.length === 0) return 0;
  return Math.max(0, Math.max(...sent.sends) + COOLDOWN_MS - now);
}

/** "0:42". */
function clock(ms: number): string {
  const seconds = Math.ceil(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** The current time, ticking every second while `active`. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

/** The card's heading (the page focuses it when the card's state changes). */
function Title({ children }: { children: ReactNode }) {
  return (
    <h1 className="login-title" tabIndex={-1}>
      {children}
    </h1>
  );
}

/** Continue with Google, and why it couldn't start. */
function GoogleOption({ next, size = "lg" }: { next: string | null; size?: "lg" | "sm" }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <GoogleButton intent="signin" from="signup" next={next ?? undefined} size={size} onError={setError} />
      {error ? (
        <p className="error-text" role="alert">
          <AlertCircleIcon size={13} />
          <span>{error}</span>
        </p>
      ) : null}
    </>
  );
}

/** A form-level refusal, with the small Google button when Google is the other way in. */
function Refusal({ message, google, next }: { message: string; google: boolean; next: string | null }) {
  return (
    <div className="auth-refusal">
      <FormError message={message} />
      {google ? <GoogleOption next={next} size="sm" /> : null}
    </div>
  );
}

type RobotCheckProps = { siteKey: string; action: "signup" | "resend"; challenge: Challenge };

/** Cloudflare's check and the line under it while a submit waits for its token. */
function RobotCheck({ siteKey, action, challenge }: RobotCheckProps) {
  return (
    <div>
      <Turnstile siteKey={siteKey} action={action} {...challenge.widget} />
      <TurnstileWait waiting={challenge.waiting} />
    </div>
  );
}

type FormProps = {
  config: AuthConfig;
  siteKey: string;
  next: string | null;
  initialName: string;
  initialEmail: string;
  sent: Sent | null;
  onSent: (address: string, name: string) => void;
};

/** Name, email, password, the terms and the robot check; Google above when it is on. */
function SignupForm({ config, siteKey, next, initialName, initialEmail, sent, onSent }: FormProps) {
  const ids = useId();
  const nameId = `${ids}-name`;
  const emailId = `${ids}-email`;
  const passwordId = `${ids}-password`;
  const rulesId = `${ids}-rules`;
  const formRef = useRef<HTMLFormElement>(null);
  const [name, setName] = useState(initialName);
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState("");
  const [terms, setTerms] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [termsError, setTermsError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ reason: PasswordReason; message: string } | null>(null);
  const [failure, setFailure] = useState<{ message: string; code: AuthErrorCode | null } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const challenge = useChallenge();
  const retried = useRef(false);
  const minimum = config.password_min_length;
  const now = useNow(cooldown(sent, email, Date.now()) > 0);
  const wait = cooldown(sent, email, now);

  function focus(name: string) {
    const element = formRef.current?.elements.namedItem(name);
    if (element instanceof HTMLElement) element.focus();
  }

  async function send(token: string) {
    setSubmitting(true);
    try {
      await authApi.signup({
        name: normalizeName(name),
        email: email.trim(),
        password,
        accept_terms: true,
        turnstile_token: token,
        next,
      });
      onSent(email.trim(), name);
    } catch (err) {
      setSubmitting(false);
      challenge.spent();
      const code = authErrorCode(err);
      const rejected = passwordRefusal(err);
      const invalid = code === null && err instanceof ApiError && err.status === 422;
      const badName = invalid ? nameRefusal(err.message, name) : null;
      if (code === "challenge_failed" && !retried.current) {
        retried.current = true;
        challenge.submit((value) => void send(value));
      } else if (rejected) {
        setRefusal(rejected);
        focus("password");
      } else if (badName) {
        setNameError(badName);
        focus("name");
      } else if (invalid) {
        setEmailError(err.message);
        focus("email");
      } else {
        setFailure({ message: messageOf(err), code });
      }
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting || wait > 0) return;
    const address = email.trim();
    const badName = nameProblem(name);
    let badEmail: string | null = null;
    if (!address) badEmail = "Enter your email.";
    else if (!EMAIL_SHAPE.test(address)) badEmail = "Enter a valid email address.";
    const short = Array.from(password.normalize("NFC")).length < minimum;
    setNameError(badName);
    setEmailError(badEmail);
    setRefusal(short ? { reason: "too_short", message: `Use at least ${minimum} characters.` } : null);
    setTermsError(terms ? null : "Tick the box to accept the terms.");
    setFailure(null);
    const first = [badName && "name", badEmail && "email", short && "password", !terms && "accept_terms"].find(Boolean);
    if (first) {
      focus(first);
      return;
    }
    retried.current = false;
    challenge.submit((token) => void send(token));
  }

  const google = config.signup.google;
  return (
    <>
      {google ? (
        <>
          <div className="login-alt">
            <GoogleOption next={next} />
          </div>
          {config.signup.email ? <OrRule label="or sign up with email" /> : null}
        </>
      ) : null}
      {config.signup.email ? (
        <form ref={formRef} className="login-form" onSubmit={onSubmit} noValidate aria-label="Sign up with email">
          <NameField
            id={nameId}
            label="Your name"
            value={name}
            onChange={(value) => {
              setName(value);
              setNameError(null);
            }}
            error={nameError}
          />
          <EmailField
            id={emailId}
            label="Email"
            value={email}
            onChange={(value) => {
              setEmail(value);
              setEmailError(null);
            }}
            error={emailError}
          />
          <div className="field">
            <label className="field-label" htmlFor={passwordId}>
              Password
            </label>
            <PasswordInput
              id={passwordId}
              name="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                setRefusal(null);
              }}
              aria-invalid={refusal ? true : undefined}
              aria-describedby={rulesId}
            />
            <PasswordRules id={rulesId} password={password} minimum={minimum} rejected={refusal} />
          </div>
          <TermsCheck
            checked={terms}
            onChange={(checked) => {
              setTerms(checked);
              if (checked) setTermsError(null);
            }}
            error={termsError}
          />
          <RobotCheck siteKey={siteKey} action="signup" challenge={challenge} />
          {failure ? (
            <Refusal
              message={failure.message}
              google={google && failure.code !== null && GOOGLE_HELPS.has(failure.code)}
              next={next}
            />
          ) : null}
          <button
            className="btn btn-primary login-submit auth-submit"
            type="submit"
            aria-busy={submitting || undefined}
            aria-disabled={wait > 0 || undefined}
          >
            {submitting ? <ButtonSpinner size={15} /> : null}
            {wait > 0 ? (
              <>
                Create account in <span className="auth-countdown">{clock(wait)}</span>
              </>
            ) : (
              "Create account"
            )}
          </button>
        </form>
      ) : null}
    </>
  );
}

type ResendProps = { config: AuthConfig; siteKey: string; sent: Sent; next: string | null; onSent: () => void };

/** "Didn't get it?" with Resend: a countdown while the server would drop it, then the widget and the request. */
function Resend({ config, siteKey, sent, next, onSent }: ResendProps) {
  const [phase, setPhase] = useState<"idle" | "sending" | "done">("idle");
  const [failure, setFailure] = useState<{ message: string; code: AuthErrorCode | null } | null>(null);
  const challenge = useChallenge();
  const retried = useRef(false);
  const now = useNow(true);
  const wait = cooldown(sent, sent.address, now);
  const google = config.signup.google;

  async function send(token: string) {
    try {
      await authApi.resend({ email: sent.address, turnstile_token: token });
      setPhase("done");
      onSent();
    } catch (err) {
      challenge.spent();
      const code = authErrorCode(err);
      if (code === "challenge_failed" && !retried.current) {
        retried.current = true;
        challenge.submit((value) => void send(value));
        return;
      }
      setPhase("idle");
      setFailure({ message: messageOf(err), code });
    }
  }

  function start() {
    if (phase === "sending" || wait > 0) return;
    setFailure(null);
    setPhase("sending");
    retried.current = false;
    challenge.submit((token) => void send(token));
  }

  if (recentSends(sent, now).length >= SENDS_PER_HOUR) {
    return (
      <div className="auth-resend">
        <div className="ui-notice" role="status">
          <InfoIcon size={18} />
          <div className="ui-notice-text">
            We've sent 3 emails. Check Spam and Gmail's Promotions and Updates tabs
            {google ? ", use Continue with Google," : ""} or try again in an hour.
            {google ? <GoogleOption next={next} size="sm" /> : null}
          </div>
        </div>
      </div>
    );
  }

  let label: ReactNode;
  if (phase === "sending") {
    label = (
      <>
        <ButtonSpinner size={14} />
        Sending…
      </>
    );
  } else if (wait > 0) {
    label = (
      <>
        <RefreshIcon size={15} />
        Resend in <span className="auth-countdown">{clock(wait)}</span>
      </>
    );
  } else {
    label = (
      <>
        <RefreshIcon size={15} />
        Resend email
      </>
    );
  }
  return (
    <div className="auth-resend">
      <div className="auth-resend-row">
        <p className="auth-resend-text">
          <b>Didn't get it?</b>
          Check your spam folder too.
        </p>
        <span className="auth-resend-end">
          <span className="auth-done-line" role="status">
            {phase === "done" && wait > 0 ? (
              <>
                <CheckIcon size={15} />
                Sent again
              </>
            ) : null}
          </span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={start}
            aria-disabled={phase === "sending" || wait > 0 || undefined}
            aria-busy={phase === "sending" || undefined}
          >
            {label}
          </button>
        </span>
      </div>
      {phase === "sending" ? <RobotCheck siteKey={siteKey} action="resend" challenge={challenge} /> : null}
      {failure ? (
        <Refusal
          message={failure.message}
          google={google && failure.code !== null && GOOGLE_HELPS.has(failure.code)}
          next={next}
        />
      ) : null}
    </div>
  );
}

type RequestProps = { siteKey: string; onSent: (address: string) => void };

/** /signup?resend=1 without a carried address: the address, the check and "Send the link again". */
function ResendRequest({ siteKey, onSent }: RequestProps) {
  const id = useId();
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const challenge = useChallenge();

  async function send(token: string) {
    setSubmitting(true);
    try {
      await authApi.resend({ email: email.trim(), turnstile_token: token });
      onSent(email.trim());
    } catch (err) {
      setSubmitting(false);
      challenge.spent();
      setFailure(messageOf(err));
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    if (!email.trim()) {
      setEmailError("Enter your email.");
      document.getElementById(`${id}-email`)?.focus();
      return;
    }
    setFailure(null);
    challenge.submit((token) => void send(token));
  }

  return (
    <form className="login-form" onSubmit={onSubmit} noValidate aria-label="Send the link again">
      <EmailField
        id={`${id}-email`}
        label="Email"
        value={email}
        onChange={(value) => {
          setEmail(value);
          setEmailError(null);
        }}
        error={emailError}
        autoFocus
      />
      <RobotCheck siteKey={siteKey} action="resend" challenge={challenge} />
      {failure ? <FormError message={failure} /> : null}
      <button className="btn btn-primary login-submit" type="submit" aria-busy={submitting || undefined}>
        {submitting ? <ButtonSpinner size={15} /> : null}
        Send the link again
      </button>
    </form>
  );
}

/**
 * Sign-up (docs/briefs/signup-auth.md 13.2, visual brief 5): the front door's split page with "Create your account",
 * Google first, then email, password, the terms and the robot check. Once sent, "Check your inbox" replaces the form in
 * the same card, with Resend and "Start again". /signup?resend=1 (from the sign-in hint) opens on the sent state.
 */
export default function SignupPage() {
  const { config, failed, retry } = useAuthConfig();
  const [params] = useSearchParams();
  const nextParam = params.get("next");
  const next = nextParam === null ? null : safeNextPath(nextParam);
  const resendMode = params.get("resend") === "1";
  const [record, setRecord] = useState<Sent | null>(() => {
    const stored = readSent();
    const carried = resendMode ? take("email") : null;
    if (carried === null || carried.toLowerCase() === stored?.address.toLowerCase()) return stored;
    return { address: carried, sends: [] };
  });
  const [view, setView] = useState<"form" | "sent">(record ? "sent" : "form");
  const [formName, setFormName] = useState("");
  const [formEmail, setFormEmail] = useState("");
  const shownView = useRef(view);

  useEffect(() => rememberGoogleNext(next), [next]);

  // Switching between the form and "Check your inbox" moves focus to the new heading.
  useEffect(() => {
    if (shownView.current === view) return;
    shownView.current = view;
    document.querySelector<HTMLElement>(".login-card .login-title")?.focus();
  }, [view]);

  function sentTo(address: string) {
    const now = Date.now();
    const same = record?.address.toLowerCase() === address.toLowerCase();
    const updated = { address, sends: [...(same ? recentSends(record, now) : []), now] };
    writeSent(updated);
    setRecord(updated);
    setView("sent");
  }

  let card: ReactNode;
  let compact = false;
  if (config === null) {
    card = (
      <>
        <Title>Create your account</Title>
        <p className="login-lede">{LEDE}</p>
        {failed ? (
          <div className="ui-notice ui-notice-danger auth-signup-closed" role="alert">
            <AlertCircleIcon size={18} />
            <span className="ui-notice-text">Couldn't reach the server.</span>
            <button type="button" className="btn btn-sm" onClick={retry}>
              Try again
            </button>
          </div>
        ) : (
          <div className="auth-signup-closed">
            <Spinner size={20} label="Loading" />
          </div>
        )}
      </>
    );
  } else if (!config.signup.open || (config.signup.email && !config.turnstile_site_key)) {
    compact = true;
    card = (
      <div className="auth-signup auth-signup-short">
        <Title>Create your account</Title>
        <p className="login-lede">{LEDE}</p>
        <div className="ui-notice" role="status">
          <InfoIcon size={18} />
          <span className="ui-notice-text">
            <b>New sign-ups are closed right now.</b> If you already have an account, sign in.
          </span>
        </div>
        <div className="login-form">
          <a className="btn btn-primary login-submit" href="/login">
            Sign in
          </a>
        </div>
      </div>
    );
  } else if (view === "sent" && record) {
    compact = true;
    card = (
      <div className="auth-signup auth-signup-short">
        <div className="auth-sent-mark" aria-hidden="true">
          <MailCheckIcon size={26} />
        </div>
        <Title>Check your inbox</Title>
        <p className="auth-sent-text">
          We sent an email to <b>{record.address}</b>.
        </p>
        <ol className="auth-steps">
          <li>
            <span>
              Open the link in it within <b>24 hours</b>.
            </span>
          </li>
          <li>
            <span>
              In this browser that's all it takes. On another device, enter the <b>password you just chose</b>.
            </span>
          </li>
          <li>
            <span>Choose a plan to start chatting with your documents.</span>
          </li>
        </ol>
        {config.signup.email && config.turnstile_site_key ? (
          <Resend
            config={config}
            siteKey={config.turnstile_site_key}
            sent={record}
            next={next}
            onSent={() => sentTo(record.address)}
          />
        ) : null}
        <p className="auth-sent-foot">
          Wrong address?{" "}
          <button
            type="button"
            onClick={() => {
              setFormEmail(record.address);
              setView("form");
            }}
          >
            Start again
          </button>
        </p>
      </div>
    );
  } else if (resendMode && config.signup.email && config.turnstile_site_key) {
    card = (
      <div className="auth-signup">
        <Title>Send the link again</Title>
        <p className="login-lede">Enter the email address you signed up with.</p>
        <ResendRequest siteKey={config.turnstile_site_key} onSent={sentTo} />
        <p className="login-new">
          Already have an account? <a href="/login">Sign in</a>
        </p>
      </div>
    );
  } else {
    card = (
      <div className="auth-signup">
        <Title>Create your account</Title>
        <p className="login-lede">{LEDE}</p>
        <p className="login-new">
          Already have an account? <a href="/login">Sign in</a>
        </p>
        <SignupForm
          config={config}
          siteKey={config.turnstile_site_key ?? ""}
          next={next}
          initialName={formName}
          initialEmail={formEmail}
          sent={record}
          onSent={(address, name) => {
            setFormName(name);
            sentTo(address);
          }}
        />
      </div>
    );
  }

  return <AuthSplit compactOnPhone={compact}>{card}</AuthSplit>;
}
