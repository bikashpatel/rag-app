import { useId, useRef, useState, type FormEvent } from "react";
import { ApiError, authApi, authErrorCode } from "../api/client";
import { useAuthConfig } from "../auth/AuthContext";
import AuthCard from "../components/auth/AuthCard";
import EmailField from "../components/auth/EmailField";
import Turnstile, { TurnstileWait } from "../components/auth/Turnstile";
import { InfoIcon, MailCheckIcon } from "../components/ui/icons";
import { ButtonSpinner } from "../components/ui/Spinner";
import { BackToSignIn, FormError, LinkCheckFailed, messageOf, useChallenge } from "./authShared";

/**
 * /forgot-password (docs/briefs/signup-auth.md 4.7 and 13.5): an address and the robot check; the answer is the same
 * for every address, so the sent state says "if an account uses it".
 */
export default function ForgotPasswordPage() {
  const { config, failed, retry } = useAuthConfig();
  const id = useId();
  const emailId = `${id}-email`;
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const challenge = useChallenge();
  const retried = useRef(false);

  if (config === null) {
    return failed ? (
      <LinkCheckFailed message="Couldn't reach the server. Please try again." onRetry={retry} />
    ) : (
      <AuthCard title="Reset your password" mark="busy" />
    );
  }
  const siteKey = config.turnstile_site_key;
  if (!config.password_reset || !siteKey) {
    return (
      <AuthCard
        title="Password reset isn't available"
        lede="Password reset isn't available right now. Ask the administrator."
        mark={{ icon: <InfoIcon />, tone: "neutral" }}
        footer={<BackToSignIn />}
      />
    );
  }
  if (sentTo !== null) {
    return (
      <AuthCard
        title="Check your email"
        lede={
          <>
            If an account uses <b>{sentTo}</b>, we've emailed it a link to reset the password. The link works for 30
            minutes.
          </>
        }
        mark={{ icon: <MailCheckIcon />, tone: "accent" }}
        footer={<BackToSignIn />}
      >
        <div className="ui-notice" role="status">
          <InfoIcon size={18} />
          <span className="ui-notice-text">
            Nothing arrived? Check your spam folder, or make sure this is the address on your account.
          </span>
        </div>
      </AuthCard>
    );
  }

  async function send(token: string) {
    const address = email.trim();
    setSubmitting(true);
    try {
      await authApi.forgot({ email: address, turnstile_token: token });
      setSentTo(address);
    } catch (err) {
      setSubmitting(false);
      challenge.spent();
      const code = authErrorCode(err);
      if (code === "challenge_failed" && !retried.current) {
        retried.current = true;
        challenge.submit((value) => void send(value));
      } else if (code === null && err instanceof ApiError && err.status === 422) {
        setEmailError(err.message);
        document.getElementById(emailId)?.focus();
      } else {
        setFailure(messageOf(err));
      }
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    if (!email.trim()) {
      setEmailError("Enter your email.");
      document.getElementById(emailId)?.focus();
      return;
    }
    setFailure(null);
    retried.current = false;
    challenge.submit((token) => void send(token));
  }

  return (
    <AuthCard
      title="Reset your password"
      lede="Enter the email address on your account and we'll send you a link to choose a new password."
      footer={<BackToSignIn />}
    >
      <form className="auth-form" onSubmit={onSubmit} noValidate aria-label="Reset your password">
        <EmailField
          id={emailId}
          label="Email"
          value={email}
          onChange={(value) => {
            setEmail(value);
            setEmailError(null);
          }}
          error={emailError}
          hint="No email on your account? Ask the administrator."
          autoFocus
        />
        <div>
          <Turnstile siteKey={siteKey} action="reset" {...challenge.widget} />
          <TurnstileWait waiting={challenge.waiting} />
        </div>
        {failure ? <FormError message={failure} /> : null}
        <button className="btn btn-primary auth-submit" type="submit" aria-busy={submitting || undefined}>
          {submitting ? <ButtonSpinner size={15} /> : null}
          Send reset link
        </button>
      </form>
    </AuthCard>
  );
}
