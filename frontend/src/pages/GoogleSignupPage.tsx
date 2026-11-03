import { useEffect, useId, useState, type FormEvent } from "react";
import { ApiError, authApi, authErrorCode } from "../api/client";
import type { GooglePending } from "../api/types";
import AuthCard from "../components/auth/AuthCard";
import GoogleButton from "../components/auth/GoogleButton";
import IdentityRow from "../components/auth/IdentityRow";
import NameField from "../components/auth/NameField";
import TermsCheck from "../components/auth/TermsCheck";
import { AlertCircleIcon, ClockIcon, InfoIcon, MailIcon, UserIcon } from "../components/ui/icons";
import { ButtonSpinner } from "../components/ui/Spinner";
import { leaveForApp } from "../lib/authDocument";
import { nameProblem, nameRefusal, normalizeName } from "../lib/names";
import {
  BackToSignIn,
  CheckingLink,
  FormError,
  LinkCheckFailed,
  messageOf,
  recallGoogleNext,
  restartGoogle,
} from "./authShared";

type State =
  | { kind: "checking" }
  | { kind: "form"; pending: GooglePending }
  | { kind: "expired" | "taken" | "closed" }
  | { kind: "failed"; message: string };

/** The expired ticket's way back: Continue with Google again. */
function Expired() {
  const [error, setError] = useState<string | null>(null);
  return (
    <AuthCard
      title="That took too long"
      lede="For your safety, the Google sign-up waits 15 minutes at most."
      mark={{ icon: <ClockIcon />, tone: "warning" }}
      footer={<BackToSignIn />}
    >
      <div className="auth-actions">
        <GoogleButton intent="signin" from="signup" next={recallGoogleNext() ?? undefined} onError={setError} />
        {error ? (
          <p className="error-text" role="alert">
            <AlertCircleIcon size={13} />
            <span>{error}</span>
          </p>
        ) : null}
      </div>
    </AuthCard>
  );
}

/**
 * /signup/google (docs/briefs/signup-auth.md 4.5 and 13.4): back from Google with an address no account uses yet. The
 * account is created only after the person gives their name and accepts the terms here; from the sign-in page, "I
 * already have an account" is an equal choice. Success signs in and leaves for the app with a full page load.
 */
export default function GoogleSignupPage() {
  const nameId = useId();
  const [state, setState] = useState<State>({ kind: "checking" });
  const [round, setRound] = useState(0);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [terms, setTerms] = useState(false);
  const [termsError, setTermsError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState<"finish" | "restart" | "leave" | null>(null);

  useEffect(() => {
    let live = true;
    setState({ kind: "checking" });
    authApi.googlePending().then(
      (pending) => {
        if (live) setState({ kind: "form", pending });
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
  }, [round]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const badName = nameProblem(name);
    setNameError(badName);
    setTermsError(terms ? null : "Tick the box to accept the terms.");
    const first = badName ? "name" : terms ? null : "accept_terms";
    if (first) {
      const field = form.elements.namedItem(first);
      if (field instanceof HTMLElement) field.focus();
      return;
    }
    setFailure(null);
    setBusy("finish");
    try {
      await authApi.googleFinish({ name: normalizeName(name), accept_terms: true });
      leaveForApp(recallGoogleNext() ?? "/plans");
    } catch (err) {
      setBusy(null);
      const refusal = err instanceof ApiError && err.status === 422 ? nameRefusal(err.message, name) : null;
      if (refusal) {
        setNameError(refusal);
        const field = form.elements.namedItem("name");
        if (field instanceof HTMLElement) field.focus();
        return;
      }
      switch (authErrorCode(err)) {
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

  async function restart(from: "login" | "signup") {
    if (busy) return;
    setBusy("restart");
    setFailure(null);
    try {
      await restartGoogle(from);
    } catch (err) {
      setBusy(null);
      setFailure(messageOf(err, "We couldn't sign you in with Google. Try again."));
    }
  }

  /** Drops the ticket, then a full page load of the sign-in page. */
  async function leave(search: string) {
    if (busy) return;
    setBusy("leave");
    await authApi.googleCancel().catch(() => undefined);
    window.location.assign(`/login${search}`);
  }

  switch (state.kind) {
    case "checking":
      return <CheckingLink />;
    case "failed":
      return <LinkCheckFailed message={state.message} onRetry={() => setRound((value) => value + 1)} />;
    case "expired":
      return <Expired />;
    case "taken":
      return (
        <AuthCard
          title="You already have an account"
          lede="This address already has an account. Sign in with your password, then connect Google in Settings."
          mark={{ icon: <UserIcon />, tone: "neutral" }}
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

  const { pending } = state;
  const fromLogin = pending.from === "login";
  return (
    <AuthCard
      title={fromLogin ? "No AskNuvexusDoc account uses this address yet" : "Create your AskNuvexusDoc account"}
      lede="You're signing up with Google as:"
      footer={
        <>
          <button
            type="button"
            className="auth-link-button"
            onClick={() => void restart(pending.from)}
            aria-busy={busy === "restart" || undefined}
          >
            Use a different Google account
          </button>
          {fromLogin ? null : (
            <button type="button" className="auth-link-button auth-quiet" onClick={() => void leave("")}>
              Cancel
            </button>
          )}
        </>
      }
    >
      <form
        className="auth-form"
        onSubmit={(event) => void onSubmit(event)}
        noValidate
        aria-label="Create your account"
      >
        <IdentityRow kind="google" primary={pending.email} secondary="Google account" />
        {pending.email_verified ? null : (
          <div className="ui-notice ui-notice-info" role="status">
            <MailIcon size={18} />
            <span className="ui-notice-text">
              We'll email that address so you can confirm it. Until then, receipts and password resets can't use it.
            </span>
          </div>
        )}
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
        <TermsCheck
          checked={terms}
          onChange={(checked) => {
            setTerms(checked);
            if (checked) setTermsError(null);
          }}
          error={termsError}
        />
        {failure ? <FormError message={failure} /> : null}
        <div className="auth-actions">
          <button className="btn btn-primary auth-submit" type="submit" aria-busy={busy === "finish" || undefined}>
            {busy === "finish" ? <ButtonSpinner size={15} /> : null}
            Create account
          </button>
          {fromLogin ? (
            <button
              type="button"
              className="btn auth-submit"
              onClick={() => void leave("?google=use_password")}
              aria-busy={busy === "leave" || undefined}
            >
              I already have an account
            </button>
          ) : null}
        </div>
      </form>
    </AuthCard>
  );
}
