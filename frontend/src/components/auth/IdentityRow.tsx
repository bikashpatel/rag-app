import Avatar from "../ui/Avatar";
import { MailIcon } from "../ui/icons";
import { GoogleMark } from "./GoogleButton";
import "./auth-components.css";

/** Whose account a page acts for: an address, a username (with the avatar) or a Google account. Text, not a field. */
export default function IdentityRow({
  kind,
  primary,
  secondary,
}: {
  kind: "email" | "user" | "google";
  primary: string;
  secondary?: string;
}) {
  return (
    <div className="auth-identity">
      {kind === "user" ? (
        <Avatar name={primary} />
      ) : (
        <span className="auth-identity-icon" aria-hidden="true">
          {kind === "google" ? <GoogleMark /> : <MailIcon size={17} />}
        </span>
      )}
      <span className="auth-identity-text">
        <span className="auth-identity-primary">{primary}</span>
        {secondary ? <span className="auth-identity-secondary">{secondary}</span> : null}
      </span>
    </div>
  );
}
