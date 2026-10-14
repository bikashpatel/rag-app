import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { AlertIcon, ArrowLeftIcon, CloseIcon } from "./icons";

export default function AdminHeader() {
  const { user, logout } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const username = user?.username ?? "";

  function signOut() {
    setSigningOut(true);
    setLogoutError(null);
    // On failure the session cookie is still set: stay signed in and say so instead of pretending to sign out.
    logout().catch(() => {
      setSigningOut(false);
      setLogoutError("Couldn't sign out. Check your connection and try again.");
    });
  }

  return (
    <header className="admin-topbar">
      <div className="admin-topbar-inner">
        <Link to="/" className="admin-brand" aria-label="RAG Assistant home">
          <span className="admin-logo" aria-hidden="true">
            R
          </span>
          <span className="admin-brand-name">RAG Assistant</span>
        </Link>
        <span className="badge badge-info">Admin</span>
        <span className="spacer" />
        <span className="admin-whoami" title={`Signed in as ${username}`}>
          <span className="admin-avatar" aria-hidden="true">
            {username.charAt(0).toUpperCase()}
          </span>
          <span className="admin-whoami-name">
            <span className="sr-only">Signed in as </span>
            {username}
          </span>
        </span>
        <Link to="/" className="btn btn-sm">
          <ArrowLeftIcon size={14} />
          Back to chat
        </Link>
        <button type="button" className="btn btn-ghost btn-sm" disabled={signingOut} onClick={signOut}>
          Sign out
        </button>
      </div>
      {logoutError ? (
        <div className="admin-topbar-alert">
          <div className="admin-notice admin-notice-danger" role="alert">
            <AlertIcon size={18} />
            <span className="admin-notice-text">{logoutError}</span>
            <button
              type="button"
              className="btn btn-ghost btn-icon btn-sm"
              aria-label="Dismiss"
              onClick={() => setLogoutError(null)}
            >
              <CloseIcon size={14} />
            </button>
          </div>
        </div>
      ) : null}
    </header>
  );
}
