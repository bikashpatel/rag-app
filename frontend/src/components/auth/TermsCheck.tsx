import { useId, type ReactNode } from "react";
import { AlertCircleIcon } from "../ui/icons";
import "./auth-components.css";

function NewTabLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/**
 * The unticked terms box of both sign-up paths. The box is named `accept_terms`, so a page focuses it with
 * `form.elements.namedItem("accept_terms")` when it is the first field with a problem.
 */
export default function TermsCheck({
  checked,
  onChange,
  error = null,
}: {
  checked: boolean;
  onChange(checked: boolean): void;
  error?: string | null;
}) {
  const errorId = useId();
  return (
    <div className="field">
      <label className="auth-terms">
        <input
          type="checkbox"
          className="ui-check"
          name="accept_terms"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
        <span>
          I agree to the <NewTabLink href="/terms">Terms</NewTabLink> and the{" "}
          <NewTabLink href="/privacy">Privacy policy</NewTabLink>.
        </span>
      </label>
      {error ? (
        <p className="error-text" id={errorId} role="alert">
          <AlertCircleIcon size={13} />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}
