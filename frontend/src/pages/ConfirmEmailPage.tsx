import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { authApi, authErrorCode } from "../api/client";
import type { EmailTokenInfo } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import AuthCard from "../components/auth/AuthCard";
import { AtSignIcon, CheckCircleIcon, ClockIcon, UserIcon } from "../components/ui/icons";
import { ButtonSpinner } from "../components/ui/Spinner";
import { stash, take } from "../lib/authDocument";
import { CheckingLink, FormError, IncompleteLink, LinkCheckFailed, messageOf, useFragmentToken } from "./authShared";

/** The token waits this long in this tab while the user signs in. */
const WAIT_MS = 10 * 60_000;

type State =
  | { kind: "checking" }
  | { kind: "ready"; info: EmailTokenInfo }
  | { kind: "done"; email: string }
  | { kind: "expired" | "taken" }
  | { kind: "failed"; message: string };

/** The link's token: from the fragment, or the one this tab kept while the user signed in. Kept again until used. */
function useConfirmToken(): string | null {
  const fromLink = useFragmentToken();
  const [token] = useState(() => fromLink ?? take("confirm_token"));
  useEffect(() => {
    if (token !== null) stash("confirm_token", token, WAIT_MS);
  }, [token]);
  return token;
}

/**
 * /confirm-email#t=… (docs/briefs/signup-auth.md 4.8 and 13.6): only the signed-in account that asked can confirm its
 * new address. Signed out, the page sends the user to sign in and picks the token up again; signed in to another
 * account, it offers to sign out.
 */
export default function ConfirmEmailPage() {
  const token = useConfirmToken();
  const { user, loading, logout, refresh } = useAuth();
  const [state, setState] = useState<State>({ kind: "checking" });
  const [round, setRound] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const userId = user?.id ?? null;

  useEffect(() => {
    if (token === null || loading) return;
    let live = true;
    setState({ kind: "checking" });
    authApi.checkEmailToken(token).then(
      (info) => {
        if (live) setState({ kind: "ready", info });
      },
      (err: unknown) => {
        if (!live) return;
        if (authErrorCode(err) === "link_invalid") {
          take("confirm_token");
          setState({ kind: "expired" });
        } else {
          setState({ kind: "failed", message: messageOf(err, "Couldn't reach the server. Please try again.") });
        }
      },
    );
    return () => {
      live = false;
    };
  }, [token, loading, userId, round]);

  if (token === null) return <IncompleteLink />;

  async function confirm() {
    if (busy || token === null) return;
    setBusy(true);
    setFailure(null);
    try {
      const { email } = await authApi.confirmEmail(token);
      take("confirm_token");
      setState({ kind: "done", email });
      void refresh().catch(() => undefined);
    } catch (err) {
      setBusy(false);
      switch (authErrorCode(err)) {
        case "link_invalid":
          take("confirm_token");
          setState({ kind: "expired" });
          break;
        case "email_taken":
          take("confirm_token");
          setState({ kind: "taken" });
          break;
        case "wrong_account":
          setRound((value) => value + 1);
          break;
        default:
          setFailure(messageOf(err));
      }
    }
  }

  async function signOut() {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      await logout();
    } catch {
      setFailure("Couldn't sign out. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  const continueAction = (
    <div className="auth-actions">
      <Link className="btn btn-primary auth-submit" to="/?settings=account">
        Continue
      </Link>
    </div>
  );

  switch (state.kind) {
    case "checking":
      return <CheckingLink />;
    case "failed":
      return <LinkCheckFailed message={state.message} onRetry={() => setRound((value) => value + 1)} />;
    case "expired":
      return (
        <AuthCard
          title="This link has expired"
          lede="Ask for a new link in Settings → Account."
          mark={{ icon: <ClockIcon />, tone: "warning" }}
        >
          {continueAction}
        </AuthCard>
      );
    case "taken":
      return (
        <AuthCard
          title="That address is taken"
          lede="Another account already uses this address."
          mark={{ icon: <UserIcon />, tone: "neutral" }}
        >
          {continueAction}
        </AuthCard>
      );
    case "done":
      return (
        <AuthCard
          title="Your email is confirmed"
          lede={
            <>
              <b>{state.email}</b> is now the email of your account.
            </>
          }
          mark={{ icon: <CheckCircleIcon />, tone: "success" }}
        >
          {continueAction}
        </AuthCard>
      );
  }

  const { info } = state;
  if (info.signed_in) {
    return (
      <AuthCard
        title="Confirm your email"
        lede={
          <>
            Use <b>{info.email}</b> as the email of your account?
          </>
        }
        mark={{ icon: <AtSignIcon />, tone: "accent" }}
        footer={
          <Link className="auth-quiet" to="/">
            Not now
          </Link>
        }
      >
        {failure ? <FormError message={failure} /> : null}
        <div className="auth-actions">
          <button
            type="button"
            className="btn btn-primary auth-submit"
            onClick={() => void confirm()}
            aria-busy={busy || undefined}
          >
            {busy ? <ButtonSpinner size={15} /> : null}
            Confirm this email
          </button>
        </div>
      </AuthCard>
    );
  }
  if (user) {
    return (
      <AuthCard
        title="You're signed in to another account"
        lede={`Sign out, then sign in to ${info.account} to confirm this email.`}
        mark={{ icon: <UserIcon />, tone: "neutral" }}
        footer={
          <Link className="auth-quiet" to="/">
            Not now
          </Link>
        }
      >
        {failure ? <FormError message={failure} /> : null}
        <div className="auth-actions">
          <button
            type="button"
            className="btn btn-primary auth-submit"
            onClick={() => void signOut()}
            aria-busy={busy || undefined}
          >
            {busy ? <ButtonSpinner size={15} /> : null}
            Sign out
          </button>
        </div>
      </AuthCard>
    );
  }
  return (
    <AuthCard
      title="Sign in to confirm"
      lede={
        <>
          Sign in to the account {info.account} to confirm <b>{info.email}</b>.
        </>
      }
      mark={{ icon: <AtSignIcon />, tone: "accent" }}
    >
      <div className="auth-actions">
        <a className="btn btn-primary auth-submit" href="/login?next=/confirm-email">
          Sign in
        </a>
      </div>
    </AuthCard>
  );
}
