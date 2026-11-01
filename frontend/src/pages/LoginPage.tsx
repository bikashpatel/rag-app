import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError, authErrorCode } from "../api/client";
import { useAuth, useAuthConfig } from "../auth/AuthContext";
import { postLoginPath } from "../auth/loginRedirect";
import GoogleButton, { OrRule } from "../components/auth/GoogleButton";
import Turnstile, { TurnstileWait } from "../components/auth/Turnstile";
import { AlertCircleIcon, ClockIcon, InfoIcon, ShieldIcon } from "../components/ui/icons";
import PasswordInput from "../components/ui/PasswordInput";
import { ButtonSpinner } from "../components/ui/Spinner";
import { leaveForApp, stash } from "../lib/authDocument";
import { ContactUs, FormError, messageOf, rememberGoogleNext, useChallenge } from "./authShared";
import AuthSplit from "./login/AuthSplit";

/** How long the typed address waits in this tab for /signup?resend=1. */
const CARRY_MS = 10 * 60_000;

type GoogleResult = "denied" | "expired" | "error" | "link_required" | "use_password" | "signup_closed" | "unavailable";
type Tone = "neutral" | "info" | "warning" | "danger";

const GOOGLE_RESULTS: Record<GoogleResult, Tone> = {
  denied: "neutral",
  expired: "warning",
  error: "danger",
  link_required: "info",
  use_password: "info",
  signup_closed: "neutral",
  unavailable: "danger",
};

function isGoogleResult(value: string | null): value is GoogleResult {
  return value !== null && Object.hasOwn(GOOGLE_RESULTS, value);
}

/** The ?google=<result> notice: back from Google, or from /signup/google's "I already have an account". */
function GoogleNotice({ result, contact }: { result: GoogleResult; contact: string | null }) {
  const tone = GOOGLE_RESULTS[result];
  let text: ReactNode;
  switch (result) {
    case "denied":
      text = "Google sign-in was cancelled.";
      break;
    case "expired":
      text = "That took too long. Try again.";
      break;
    case "error":
      text = "We couldn't sign you in with Google. Try again.";
      break;
    case "link_required":
      text = (
        <>
          <b>An account already uses this email.</b> Sign in with your password, then connect Google in Settings →
          Account.
        </>
      );
      break;
    case "use_password":
      text = "Sign in with your email or username, then connect Google in Settings → Account.";
      break;
    case "signup_closed":
      text = "New sign-ups are closed right now.";
      break;
    case "unavailable":
      text = (
        <>
          We couldn't sign you in. If you think this is a mistake, <ContactUs email={contact}>contact us</ContactUs>.
        </>
      );
      break;
  }
  let icon = <InfoIcon size={18} />;
  if (tone === "warning") icon = <ClockIcon size={18} />;
  else if (tone === "danger") icon = <AlertCircleIcon size={18} />;
  return (
    <div className={tone === "neutral" ? "ui-notice" : `ui-notice ui-notice-${tone}`} role="alert">
      {icon}
      <span className="ui-notice-text">{text}</span>
    </div>
  );
}

type Failure = { message: string; wrongDetails: boolean };

/**
 * Sign-in (docs/briefs/signup-auth.md 13.1, visual brief 4): the card of the front door's split page (AuthSplit).
 * Continue with Google first, then one "Email or username" field; after a challenge the robot check sits above Sign
 * in. A successful sign-in leaves for the app with a full page load.
 */
export default function LoginPage() {
  const { login } = useAuth();
  const { config, failed: configFailed } = useAuthConfig();
  const location = useLocation();
  const navigate = useNavigate();
  const identifierId = useId();
  const passwordId = useId();
  const errorId = useId();
  const identifierRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const captionRef = useRef<HTMLParagraphElement>(null);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState<{ identifier: boolean; password: boolean }>({
    identifier: false,
    password: false,
  });
  const [failure, setFailure] = useState<Failure | null>(null);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [challenged, setChallenged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const challenge = useChallenge();
  const retriedChallenge = useRef(false);
  const [googleResult] = useState(() => {
    const value = new URLSearchParams(location.search).get("google");
    return isGoogleResult(value) ? value : null;
  });

  // The result is shown once: the parameter leaves the address bar (the redirect state stays).
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (!params.has("google")) return;
    params.delete("google");
    const search = params.toString().replaceAll("%2F", "/");
    navigate({ pathname: location.pathname, search: search ? `?${search}` : "", hash: location.hash }, {
      replace: true,
      state: location.state,
    });
  }, [location, navigate]);

  const siteKey = config?.turnstile_site_key ?? null;
  const signupOpen = config?.signup.open ?? false;
  const next = postLoginPath(location.state, location.search);

  // A Google sign-up started here lands where this sign-in would have (the chat's default sends it to /plans).
  useEffect(() => rememberGoogleNext(next === "/" ? null : next), [next]);

  // The challenge replaces the refusal: focus on its caption says so (a screen reader reads it), and Tab goes on to
  // the check, then Sign in.
  useEffect(() => {
    if (challenged) captionRef.current?.focus();
  }, [challenged]);

  async function send(token: string | null) {
    setSubmitting(true);
    try {
      await login(identifier.trim(), password, token);
      leaveForApp(next);
    } catch (err) {
      setSubmitting(false);
      if (token !== null) challenge.spent();
      const code = authErrorCode(err);
      if (code === "challenge_required") {
        setChallenged(true);
        return;
      }
      if (code === "challenge_failed" && !retriedChallenge.current) {
        // The widget starts again; the form goes out with its next token.
        retriedChallenge.current = true;
        challenge.submit((next) => void send(next));
        return;
      }
      const wrong = err instanceof ApiError && err.status === 401;
      setFailure({ message: messageOf(err, "Sign-in failed. Please try again."), wrongDetails: wrong });
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const empty = { identifier: !identifier.trim(), password: !password };
    setMissing(empty);
    if (empty.identifier || empty.password) {
      (empty.identifier ? identifierRef : passwordRef).current?.focus();
      return;
    }
    setFailure(null);
    retriedChallenge.current = false;
    if (challenged && siteKey) challenge.submit((token) => void send(token));
    else void send(null);
  }

  /** "send it again": /signup?resend=1, the typed address carried in this tab when it is one. */
  function carryAddress() {
    if (identifier.includes("@")) stash("email", identifier.trim(), CARRY_MS);
  }

  const hints: ReactNode[] = [];
  if (failure?.wrongDetails && config?.signup.email) {
    hints.push(
      <li key="email">
        Just signed up? Open the link we emailed you, or{" "}
        <a href="/signup?resend=1" onClick={carryAddress}>
          send it again
        </a>
        .
      </li>,
    );
  }
  if (failure?.wrongDetails && config?.google_signin) {
    hints.push(
      <li key="google">
        Signed up with Google? Use{" "}
        <a
          href="#continue-with-google"
          onClick={(event) => {
            event.preventDefault();
            document.querySelector<HTMLButtonElement>(".login-alt .auth-google")?.focus();
          }}
        >
          Continue with Google
        </a>
        .
      </li>,
    );
  }

  const describedBy = (field: "identifier" | "password") =>
    [missing[field] ? `${errorId}-${field}` : null, failure ? errorId : null].filter(Boolean).join(" ") || undefined;

  return (
    <AuthSplit>
      <h1 className="login-title">
        Welcome back<span className="sr-only"> to AskNuvexusDoc</span>
      </h1>
      <p className="login-lede">Sign in to continue to your documents and chats.</p>
      {signupOpen ? (
        <p className="login-new">
          New to AskNuvexusDoc? <a href="/signup">Create an account</a>
        </p>
      ) : null}
      {googleResult ? <GoogleNotice result={googleResult} contact={config?.contact_email ?? null} /> : null}

      {config?.google_signin ? (
        <>
          <div className="login-alt" id="continue-with-google">
            <GoogleButton intent="signin" from="login" next={next} onError={setGoogleError} />
            {googleError ? (
              <p className="error-text" role="alert">
                <AlertCircleIcon size={13} />
                <span>{googleError}</span>
              </p>
            ) : null}
          </div>
          <OrRule label="or" />
        </>
      ) : null}

      <form className="login-form" onSubmit={onSubmit} noValidate>
        <div className="field">
          <label className="field-label" htmlFor={identifierId}>
            Email or username
          </label>
          <input
            ref={identifierRef}
            id={identifierId}
            className="input"
            name="username"
            autoComplete="username"
            inputMode="email"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            value={identifier}
            onChange={(event) => {
              setIdentifier(event.target.value);
              setMissing((value) => ({ ...value, identifier: false }));
            }}
            aria-invalid={missing.identifier || undefined}
            aria-describedby={describedBy("identifier")}
          />
          {missing.identifier ? (
            <p className="error-text" id={`${errorId}-identifier`} role="alert">
              <AlertCircleIcon size={13} />
              <span>Enter your email or username.</span>
            </p>
          ) : null}
        </div>
        <div className="field">
          <div className="login-label-row">
            <label className="field-label" htmlFor={passwordId}>
              Password
            </label>
            {config?.password_reset ? (
              <a className="login-forgot" href="/forgot-password">
                Forgot password?
              </a>
            ) : null}
          </div>
          <PasswordInput
            ref={passwordRef}
            id={passwordId}
            autoComplete="current-password"
            placeholder="Your password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setMissing((value) => ({ ...value, password: false }));
            }}
            aria-invalid={missing.password || undefined}
            aria-describedby={describedBy("password")}
          />
          {missing.password ? (
            <p className="error-text" id={`${errorId}-password`} role="alert">
              <AlertCircleIcon size={13} />
              <span>Enter your password.</span>
            </p>
          ) : null}
        </div>
        {failure ? (
          <FormError id={errorId} message={failure.message}>
            {hints.length > 0 ? <ul className="login-hints">{hints}</ul> : null}
          </FormError>
        ) : null}
        {challenged && siteKey ? (
          <div className="login-challenge">
            <p ref={captionRef} className="auth-turnstile-caption" tabIndex={-1}>
              <ShieldIcon size={16} />
              <span>Confirm you're not a robot, then sign in again.</span>
            </p>
            <Turnstile siteKey={siteKey} action="login" {...challenge.widget} />
            <TurnstileWait waiting={challenge.waiting} />
          </div>
        ) : null}
        <button className="btn btn-primary login-submit" type="submit" aria-busy={submitting || undefined}>
          {submitting ? <ButtonSpinner size={15} /> : null}
          Sign in
        </button>
      </form>

      {configFailed || (config && !signupOpen) ? <ClosedNote /> : null}
    </AuthSplit>
  );
}

/** While sign-up is closed: today's note. */
function ClosedNote() {
  return (
    <div className="login-foot">
      <ShieldIcon size={16} />
      <span>Accounts are created by your administrator. Ask them if you need access or a new password.</span>
    </div>
  );
}
