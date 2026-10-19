import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useOpenSettings } from "../../lib/settingsLink";
import Avatar from "../ui/Avatar";
import { AlertCircleIcon, ArrowLeftIcon, CloseIcon, LogoutIcon, SettingsIcon } from "../ui/icons";

/**
 * The admin page's top bar: the brand and an "Admin" badge, then who is signed in, Settings (the dialog opens over this
 * page), Back to chat and Sign out. On phones the labels of Settings and Sign out collapse to their icons.
 */
export default function AdminHeader() {
  const { user, logout } = useAuth();
  const openSettings = useOpenSettings();
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
        <Link to="/" className="ui-brand admin-brand" aria-label="RAG Assistant home">
          <span className="ui-brand-mark" aria-hidden="true">
            R
          </span>
          <span className="ui-brand-name admin-brand-name">RAG Assistant</span>
        </Link>
        <span className="badge badge-accent">Admin</span>
        <span className="spacer" />
        <span className="admin-whoami" title={`Signed in as ${username}`}>
          <Avatar name={username || "?"} />
          <span className="admin-whoami-name">
            <span className="sr-only">Signed in as </span>
            {username}
          </span>
        </span>
        <button
          type="button"
          className="btn btn-ghost btn-sm admin-topbar-btn"
          aria-label="Settings"
          onClick={() => openSettings("general")}
        >
          <SettingsIcon size={16} />
          <span className="admin-topbar-label">Settings</span>
        </button>
        <Link to="/" className="btn btn-sm admin-back">
          <ArrowLeftIcon size={16} />
          Back to chat
        </Link>
        <button
          type="button"
          className="btn btn-ghost btn-sm admin-topbar-btn"
          aria-label="Sign out"
          disabled={signingOut}
          onClick={signOut}
        >
          <LogoutIcon size={16} className="admin-topbar-icon" />
          <span className="admin-topbar-label">Sign out</span>
        </button>
      </div>
      {logoutError ? (
        <div className="admin-topbar-alert">
          <div className="ui-notice ui-notice-danger" role="alert">
            <AlertCircleIcon size={15} />
            <span className="ui-notice-text">{logoutError}</span>
            <button type="button" className="btn btn-ghost btn-icon" aria-label="Dismiss" onClick={() => setLogoutError(null)}>
              <CloseIcon size={15} />
            </button>
          </div>
        </div>
      ) : null}
    </header>
  );
}
