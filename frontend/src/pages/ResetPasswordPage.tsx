import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { authApi, authErrorCode, passwordRefusal } from "../api/client";
import type { PasswordReason, PasswordTokenInfo } from "../api/types";
import { useAuthConfig } from "../auth/AuthContext";
import AuthCard from "../components/auth/AuthCard";
import IdentityRow from "../components/auth/IdentityRow";
import PasswordRules from "../components/auth/PasswordRules";
import { AlertCircleIcon, CheckCircleIcon, ClockIcon } from "../components/ui/icons";
import PasswordInput from "../components/ui/PasswordInput";
import { ButtonSpinner } from "../components/ui/Spinner";
import {
  BackToSignIn,
  CheckingLink,
  FormError,
  IncompleteLink,
  LinkCheckFailed,
  messageOf,
  useFragmentToken,
} from "./authShared";

/** Until /auth/config answers: the server's own minimum is checked on save anyway. */
const DEFAULT_MINIMUM = 8;

type State =
  | { kind: "checking" }
  | { kind: "form"; info: PasswordTokenInfo }
  | { kind: "done"; info: PasswordTokenInfo }
  | { kind: "expired" }
  | { kind: "failed"; message: string };

/**
 * /reset-password#t=… (docs/briefs/signup-auth.md 4.7 and 13.5): a new password twice, and for an account with Google
 * the offer to disconnect it. Saving ends every session and never signs in.
 */
export default function ResetPasswordPage() {
  const token = useFragmentToken();
  const { config } = useAuthConfig();
  const id = useId();
  const passwordRef = useRef<HTMLInputElement>(null);
  const repeatRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<State>({ kind: "checking" });
  const [round, setRound] = useState(0);
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [disconnect, setDisconnect] = useState(false);
  const [refusal, setRefusal] = useState<{ reason: PasswordReason; message: string } | null>(null);
  const [mismatch, setMismatch] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const minimum = config?.password_min_length ?? DEFAULT_MINIMUM;

  useEffect(() => {
    if (token === null) return;
    let live = true;
    setState({ kind: "checking" });
    authApi.checkPasswordToken(token).then(
      (info) => {
        if (!live) return;
        setDisconnect(info.google?.suggest_disconnect ?? false);
        setState({ kind: "form", info });
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
    const short = Array.from(password.normalize("NFC")).length < minimum;
    const different = repeat !== password;
    setRefusal(short ? { reason: "too_short", message: `Use at least ${minimum} characters.` } : null);
    setMismatch(!short && different);
    setFailure(null);
    if (short || different) {
      (short ? passwordRef : repeatRef).current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      await authApi.resetPassword({ token, new_password: password, disconnect_google: disconnect });
      setState({ kind: "done", info: state.info });
    } catch (err) {
      setSubmitting(false);
      const rejected = passwordRefusal(err);
      if (rejected) {
        setRefusal(rejected);
        passwordRef.current?.focus();
      } else if (authErrorCode(err) === "link_invalid") {
        setState({ kind: "expired" });
      } else {
        setFailure(messageOf(err));
      }
    }
  }

  switch (state.kind) {
    case "checking":
      return <CheckingLink />;
    case "failed":
      return <LinkCheckFailed message={state.message} onRetry={() => setRound((value) => value + 1)} />;
    case "expired":
      return (
        <AuthCard
          title="This link has expired"
          lede="Reset links work once, for 30 minutes. Ask for a new one."
          mark={{ icon: <ClockIcon />, tone: "warning" }}
          footer={<BackToSignIn />}
        >
          <div className="auth-actions">
            <a className="btn btn-primary auth-submit" href="/forgot-password">
              Ask for a new link
            </a>
          </div>
        </AuthCard>
      );
    case "done":
      return (
        <AuthCard
          title={state.info.has_password ? "Password changed" : "Your password is set"}
          lede={
            state.info.has_password ? (
              "Your password was changed and you were signed out everywhere. Sign in with the new one."
            ) : (
              <>
                Sign in with <b>{state.info.email}</b> and your new password, or keep using Google.
              </>
            )
          }
          mark={{ icon: <CheckCircleIcon />, tone: "success" }}
        >
          <div className="auth-actions">
            <a className="btn btn-primary auth-submit" href="/login">
              Sign in
            </a>
          </div>
        </AuthCard>
      );
  }

  const { info } = state;
  const rulesId = `${id}-rules`;
  const mismatchId = `${id}-mismatch`;
  return (
    <AuthCard
      title={info.has_password ? "Choose a new password" : "Set your password"}
      lede="Saving it signs you out on every device."
    >
      <form className="auth-form" onSubmit={onSubmit} noValidate aria-label="Choose a new password">
        <IdentityRow kind="user" primary={info.email} secondary={info.username} />
        <input
          className="sr-only"
          type="email"
          name="username"
          autoComplete="username"
          value={info.email}
          readOnly
          tabIndex={-1}
          aria-hidden="true"
        />
        <div className="field">
          <label className="field-label" htmlFor={`${id}-new`}>
            New password
          </label>
          <PasswordInput
            ref={passwordRef}
            id={`${id}-new`}
            autoComplete="new-password"
            autoFocus
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
        <div className="field">
          <label className="field-label" htmlFor={`${id}-repeat`}>
            Repeat it
          </label>
          <PasswordInput
            ref={repeatRef}
            id={`${id}-repeat`}
            autoComplete="new-password"
            value={repeat}
            onChange={(event) => {
              setRepeat(event.target.value);
              setMismatch(false);
            }}
            aria-invalid={mismatch || undefined}
            aria-describedby={mismatch ? mismatchId : undefined}
          />
          {mismatch ? (
            <p className="error-text" id={mismatchId} role="alert">
              <AlertCircleIcon size={13} />
              <span>The passwords don't match.</span>
            </p>
          ) : null}
        </div>
        {info.google ? (
          <div className="field">
            <label className="auth-terms">
              <input
                type="checkbox"
                className="ui-check"
                checked={disconnect}
                onChange={(event) => setDisconnect(event.target.checked)}
                aria-describedby={`${id}-google-hint`}
              />
              <span>
                Also disconnect Google sign-in (<b>{info.google.email}</b>)
              </span>
            </label>
            <span className="auth-check-hint" id={`${id}-google-hint`}>
              Do this if someone else may have connected it.
            </span>
          </div>
        ) : null}
        {failure ? <FormError message={failure} /> : null}
        <button className="btn btn-primary auth-submit" type="submit" aria-busy={submitting || undefined}>
          {submitting ? <ButtonSpinner size={15} /> : null}
          Save password
        </button>
      </form>
    </AuthCard>
  );
}
