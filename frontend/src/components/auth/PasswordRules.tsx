import { useState } from "react";
import type { PasswordReason } from "../../api/types";
import { AlertCircleIcon, CheckCircleIcon, ShieldIcon } from "../ui/icons";
import "./auth-components.css";

type Refusal = { reason: PasswordReason; message: string };

const LENGTH_REASONS: ReadonlySet<PasswordReason> = new Set(["too_short", "too_long"]);

function Ring() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="8" />
    </svg>
  );
}

function RefusedRule({ message }: { message: string }) {
  return (
    <li className="auth-rule auth-rule-error">
      <AlertCircleIcon size={14} />
      <span role="alert">{message}</span>
    </li>
  );
}

/**
 * The rules under a new-password field: the length, checked as the user types, and the server's own checks (the
 * blocklist, context words, the breach check). A refusal (`passwordRefusal(error)`) replaces the row it broke until the
 * password is edited. Link the input to `id` with aria-describedby, and mark it aria-invalid while `rejected` stands
 * (clearing `rejected` on edit does both).
 */
export default function PasswordRules({
  id,
  password,
  minimum,
  rejected = null,
}: {
  id: string;
  password: string;
  minimum: number;
  rejected?: Refusal | null;
}) {
  // The password a refusal was for: it stands only while the password is unchanged.
  const [refused, setRefused] = useState({ rejected, password });
  if (refused.rejected !== rejected) setRefused({ rejected, password });
  const refusal = rejected !== null && refused.password === password ? rejected : null;
  const long = Array.from(password.normalize("NFC")).length >= minimum;

  return (
    <ul className="auth-rules" id={id}>
      {refusal && LENGTH_REASONS.has(refusal.reason) ? (
        <RefusedRule message={refusal.message} />
      ) : (
        <li className={long ? "auth-rule auth-rule-met" : "auth-rule"}>
          {long ? <CheckCircleIcon size={14} /> : <Ring />}
          <span>
            At least {minimum} characters
            {long ? <span className="sr-only">: done</span> : null}
          </span>
        </li>
      )}
      {refusal && !LENGTH_REASONS.has(refusal.reason) ? (
        <RefusedRule message={refusal.message} />
      ) : (
        <li className="auth-rule">
          <ShieldIcon size={14} />
          <span>Not a common or leaked password, and not your email</span>
        </li>
      )}
    </ul>
  );
}
