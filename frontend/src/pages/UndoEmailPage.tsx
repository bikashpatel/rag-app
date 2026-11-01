import { useEffect, useState } from "react";
import { authApi, authErrorCode } from "../api/client";
import type { UndoInfo } from "../api/types";
import { useAuthConfig } from "../auth/AuthContext";
import AuthCard from "../components/auth/AuthCard";
import { AlertIcon, CheckCircleIcon, ClockIcon, UserIcon } from "../components/ui/icons";
import { ButtonSpinner } from "../components/ui/Spinner";
import {
  CheckingLink,
  ContactUs,
  FormError,
  IncompleteLink,
  LinkCheckFailed,
  messageOf,
  useFragmentToken,
} from "./authShared";

type State =
  | { kind: "checking" }
  | { kind: "ready"; info: UndoInfo }
  | { kind: "done"; info: UndoInfo }
  | { kind: "expired" | "taken" }
  | { kind: "failed"; message: string };

const signInLink = <a href="/login">Go to sign in</a>;

/**
 * /undo-email#t=… (docs/briefs/signup-auth.md 4.8 and 13.6): the link in the "your email was changed" notice. Undoing
 * puts the old address back, removes the password and ends every session; a reset link goes to the old address.
 */
export default function UndoEmailPage() {
  const token = useFragmentToken();
  const { config } = useAuthConfig();
  const [state, setState] = useState<State>({ kind: "checking" });
  const [round, setRound] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const contact = config?.contact_email ?? null;

  useEffect(() => {
    if (token === null) return;
    let live = true;
    setState({ kind: "checking" });
    authApi.checkUndo(token).then(
      (info) => {
        if (live) setState({ kind: "ready", info });
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

  async function undo(info: UndoInfo) {
    if (busy || token === null) return;
    setBusy(true);
    setFailure(null);
    try {
      await authApi.undoEmail(token);
      setState({ kind: "done", info });
    } catch (err) {
      setBusy(false);
      switch (authErrorCode(err)) {
        case "link_invalid":
          setState({ kind: "expired" });
          break;
        case "email_taken":
          setState({ kind: "taken" });
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
    case "expired":
      return (
        <AuthCard
          title="This link has expired"
          lede={
            <>
              Undo links work once, for 7 days. <ContactUs email={contact} /> if you still need help.
            </>
          }
          mark={{ icon: <ClockIcon />, tone: "warning" }}
          footer={signInLink}
        />
      );
    case "taken":
      return (
        <AuthCard
          title="That address is taken"
          lede={
            <>
              That address is now used by another account. <ContactUs email={contact} />.
            </>
          }
          mark={{ icon: <UserIcon />, tone: "neutral" }}
          footer={signInLink}
        />
      );
    case "done":
      return (
        <AuthCard
          title="Your email is back"
          lede={
            <>
              We signed out every session and removed the password. Check <b>{state.info.old_email}</b> for a link to
              choose a new one.
            </>
          }
          mark={{ icon: <CheckCircleIcon />, tone: "success" }}
        >
          <div className="auth-actions">
            <a className="btn btn-primary auth-submit" href="/login">
              Go to sign in
            </a>
          </div>
        </AuthCard>
      );
  }

  const { info } = state;
  return (
    <AuthCard
      title="Undo the email change?"
      lede={
        <>
          Put <b>{info.old_email}</b> back as the email of your account? This signs out every session and removes the
          password; we'll email you a link to choose a new one.
        </>
      }
      mark={{ icon: <AlertIcon />, tone: "warning" }}
      footer={
        <a className="auth-quiet" href="/">
          Not now
        </a>
      }
    >
      <p className="auth-text-small">The new address was {info.new_email}.</p>
      {failure ? <FormError message={failure} /> : null}
      <div className="auth-actions">
        <button
          type="button"
          className="btn btn-primary auth-submit"
          onClick={() => void undo(info)}
          aria-busy={busy || undefined}
        >
          {busy ? <ButtonSpinner size={15} /> : null}
          Undo the change
        </button>
      </div>
    </AuthCard>
  );
}
