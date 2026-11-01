import { useState } from "react";
import { useLocation } from "react-router-dom";
import GoogleButton from "../auth/GoogleButton";
import { AlertIcon } from "../ui/icons";

/** Fresh enough to act without another Google sign-in: this session holds a step-up grant until `until`. */
export function hasStepUp(until: string | null, now: number = Date.now()): boolean {
  return until !== null && Date.parse(until) > now;
}

/**
 * "Confirm it's you with Google" (docs/briefs/signup-auth.md 4.5, 4.8 and 4.9): an account without a password proves
 * itself with a fresh Google sign-in as its linked identity before changing its email or adding a password. Google
 * brings the browser back to this page with Settings → Account open and `returnParam` set, where the form opens.
 */
export default function StepUp({
  text,
  returnParam,
}: {
  text: string;
  returnParam: "change=email" | "setup=password";
}) {
  const { pathname } = useLocation();
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="settings-confirm settings-reauth">
      <p>{text}</p>
      <GoogleButton
        intent="reauth"
        next={`${pathname}?settings=account&${returnParam}`}
        size="sm"
        onError={setError}
      />
      {error ? (
        <p className="error-text settings-error" role="alert">
          <AlertIcon size={13} />
          {error}
        </p>
      ) : null}
    </div>
  );
}
