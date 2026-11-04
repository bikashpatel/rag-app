import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { authApi, authErrorCode } from "../api/client";
import type { SignupCheck } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import AuthCard from "../components/auth/AuthCard";
import IdentityRow from "../components/auth/IdentityRow";
import { AlertCircleIcon, ClockIcon, InfoIcon, KeyIcon, UserIcon } from "../components/ui/icons";
import AllSetMark from "../components/ui/marks/AllSetMark";
import PasswordInput from "../components/ui/PasswordInput";
import { ButtonSpinner } from "../components/ui/Spinner";
import { leaveForApp, stash } from "../lib/authDocument";
import { personName } from "../lib/names";
import { CheckingLink, FormError, IncompleteLink, LinkCheckFailed, messageOf, useFragmentToken } from "./authShared";

/** How long the address waits in this tab for "Sign up again". */
const CARRY_MS = 10 * 60_000;

type State =
  | { kind: "checking" }
  | { kind: "form"; check: SignupCheck }
  | { kind: "reset" | "expired" | "taken" | "closed" }
  | { kind: "failed"; message: string };

/** "Sign up again": /signup with the address carried in this tab. */
function SignUpAgain({ email, className }: { email: string | null; className?: string }) {
  return (
    <a className={className} href="/signup" onClick={() => email && stash("email", email, CARRY_MS)}>
      Sign up again
    </a>
  );
}

/**
 * /verify-email#t=… (docs/briefs/signup-auth.md 4.2 and 13.3): the emailed sign-up link. In the browser that signed
 * up, one click creates the account; elsewhere the password chosen then is the proof. Success signs in and leaves for
 * the app (or the page the visitor was heading for) with a full page load.
 */
export default function VerifyEmailPage() {
  const token = useFragmentToken();
  const { user } = useAuth();
  const passwordId = useId();
  const errorId = useId();
  const passwordRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<State>({ kind: "checking" });
  const [round, setRound] = useState(0);
  const [password, setPassword] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const email = state.kind === "form" ? state.check.email : null;

  useEffect(() => {
    if (token === null) return;
    let live = true;
    setState({ kind: "checking" });
    authApi.checkSignup(token).then(
      (check) => {
        if (live) setState({ kind: "form", check });
      },
      (err: unknown) => {
        if (!live) return;
        if (authErrorCode(err) === "link_invalid") setState({ kind: "expired" });
        else setState({ kind: "failed", message: messageOf(err, "Couldn't reach the server. Please try again.") });
      },
    );
    return () => {
      live = false;
    };
  }, [token, round]);

  if (token === null) return <IncompleteLink />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting || state.kind !== "form" || token === null) return;
    const needsPassword = state.check.needs_password;
    if (needsPassword && !password) {
      setFieldError("Enter the password you chose when you signed up.");
      passwordRef.current?.focus();
      return;
    }
    setFieldError(null);
    setFailure(null);
    setSubmitting(true);
    try {
      await authApi.completeSignup({ token, password: needsPassword ? password : null });
      leaveForApp(state.check.next ?? "/plans");
    } catch (err) {
      setSubmitting(false);
      switch (authErrorCode(err)) {
        case "wrong_password":
          setFieldError(messageOf(err));
          passwordRef.current?.focus();
          passwordRef.current?.select();
          break;
        case "signup_reset":
          setState({ kind: "reset" });
          break;
        case "link_invalid":
          setState({ kind: "expired" });
          break;
        case "email_taken":
          setState({ kind: "taken" });
          break;
        case "signup_closed":
          setState({ kind: "closed" });
          break;
        default:
          setFailure(messageOf(err));
      }
    }
  }

  switch (state.kind) {
    case "checking":
      return <CheckingLink />;
    case "failed":
      return <LinkCheckFailed message={state.message} onRetry={() => setRound((value) => value + 1)} />;
    case "reset":
      return (
        <AuthCard
          title="Too many wrong passwords"
          lede="For your safety this link stopped working. Sign up again to choose a new password."
          mark={{ icon: <KeyIcon />, tone: "warning" }}
          footer={<a href="/login">Sign in</a>}
        >
          <div className="auth-actions">
            <SignUpAgain email={email} className="btn btn-primary auth-submit" />
          </div>
        </AuthCard>
      );
    case "expired":
      return (
        <AuthCard
          title="This link has expired"
          lede={
            "Links work once, for 24 hours. Sign up again to get a new one, or sign in if your account is " +
            "already set up."
          }
          mark={{ icon: <ClockIcon />, tone: "warning" }}
          footer={<a href="/login">Sign in</a>}
        >
          <div className="auth-actions">
            <SignUpAgain email={email} className="btn btn-primary auth-submit" />
          </div>
        </AuthCard>
      );
    case "taken":
      return (
        <AuthCard
          title="You already have an account"
          lede="This address already has an account. Sign in instead."
          mark={{ icon: <UserIcon />, tone: "neutral" }}
          footer={
            <a className="auth-quiet" href="/forgot-password">
              Forgot your password?
            </a>
          }
        >
          <div className="auth-actions">
            <a className="btn btn-primary auth-submit" href="/login">
              Sign in
            </a>
          </div>
        </AuthCard>
      );
    case "closed":
      return (
        <AuthCard
          title="Sign-ups are closed"
          lede="New sign-ups are closed right now. If you already have an account, sign in."
          mark={{ icon: <InfoIcon />, tone: "neutral" }}
        >
          <div className="auth-actions">
            <a className="btn btn-primary auth-submit" href="/login">
              Sign in
            </a>
          </div>
        </AuthCard>
      );
  }

  const { check } = state;
  return (
    <AuthCard
      title="Finish creating your account"
      lede={
        check.needs_password
          ? "One last step: enter the password you chose when you signed up."
          : "Your email is confirmed. Create your account to continue."
      }
      art={check.needs_password ? undefined : <AllSetMark size={56} />}
      footer={
        check.needs_password ? (
          <span className="auth-footer-line">
            Forgot it? <SignUpAgain email={check.email} />
          </span>
        ) : undefined
      }
    >
      <form className="auth-form" onSubmit={onSubmit} noValidate aria-label="Finish creating your account">
        {user ? (
          <div className="ui-notice" role="status">
            <InfoIcon size={18} />
            <span className="ui-notice-text">Finishing signs you out of {personName(user)}.</span>
          </div>
        ) : null}
        <IdentityRow kind="email" primary={check.email} secondary="Confirmed by this link" />
        {check.needs_password ? (
          <>
            {/* For password managers: the saved entry is found by its address. */}
            <input
              className="sr-only"
              type="email"
              name="username"
              autoComplete="username"
              value={check.email}
              readOnly
              tabIndex={-1}
              aria-hidden="true"
            />
            <div className="field">
              <label className="field-label" htmlFor={passwordId}>
                The password you chose
              </label>
              <PasswordInput
                ref={passwordRef}
                id={passwordId}
                autoComplete="current-password"
                autoFocus
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                  setFieldError(null);
                }}
                aria-invalid={fieldError ? true : undefined}
                aria-describedby={fieldError ? errorId : undefined}
              />
              {fieldError ? (
                <p className="error-text" id={errorId} role="alert">
                  <AlertCircleIcon size={13} />
                  <span>{fieldError}</span>
                </p>
              ) : null}
            </div>
          </>
        ) : null}
        {failure ? <FormError message={failure} /> : null}
        <button className="btn btn-primary auth-submit" type="submit" aria-busy={submitting || undefined}>
          {submitting ? <ButtonSpinner size={15} /> : null}
          Create account
        </button>
      </form>
    </AuthCard>
  );
}
