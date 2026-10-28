import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useOpenSettings } from "../../lib/settingsLink";
import AvatarMenu from "../chat/AvatarMenu";
import { AlertCircleIcon, ArrowLeftIcon, CloseIcon } from "../ui/icons";
import "./billing.css";

const APP_TITLE = "AskNuvexusDoc";

type PlansFrameProps = {
  /** The tab title before " · AskNuvexusDoc". */
  title: string;
  /** `.plans-main` plus the page's width variant. */
  mainClassName: string;
  /** The return page's status region. */
  live?: boolean;
  children: ReactNode;
};

/**
 * The standalone page of the plan, checkout and return pages (visual brief 3.1): a translucent top bar with the brand,
 * "Back to chat" when the user has access, and the chat's avatar menu, over a centred column.
 */
export default function PlansFrame({ title, mainClassName, live = false, children }: PlansFrameProps) {
  const { user, logout } = useAuth();
  const openSettings = useOpenSettings();
  const [error, setError] = useState<string | null>(null);
  const hasAccess = user !== null && (user.is_admin || user.plan.access);

  useEffect(() => {
    const previous = document.title;
    document.title = `${title} · ${APP_TITLE}`;
    return () => {
      document.title = previous;
    };
  }, [title]);

  const onLogout = useCallback(() => {
    logout().catch(() => setError("Couldn't sign out. Check your connection and try again."));
  }, [logout]);

  return (
    <div className="plans-page">
      <header className="plans-topbar">
        <div className="plans-topbar-inner">
          <Link to={hasAccess ? "/" : "/plans"} className="ui-brand" aria-label={`${APP_TITLE} home`}>
            <span className="ui-brand-mark" aria-hidden="true">
              N
            </span>
            <span className="ui-brand-name">{APP_TITLE}</span>
          </Link>
          <span className="spacer" />
          {hasAccess ? (
            <Link to="/" className="btn btn-sm plans-back-chat" aria-label="Back to chat">
              <ArrowLeftIcon size={15} />
              <span className="plans-topbar-label">Back to chat</span>
            </Link>
          ) : null}
          {user ? (
            <AvatarMenu
              user={user}
              imagesOffered={false}
              onOpenSettings={(tab) => openSettings(tab)}
              onPreferenceError={setError}
              onLogout={onLogout}
            />
          ) : null}
        </div>
      </header>
      {error ? (
        <div className="plans-alert">
          <div className="ui-notice ui-notice-danger" role="alert">
            <AlertCircleIcon size={15} />
            <span className="ui-notice-text">{error}</span>
            <button
              type="button"
              className="btn btn-ghost btn-icon"
              aria-label="Dismiss"
              onClick={() => setError(null)}
            >
              <CloseIcon size={15} />
            </button>
          </div>
        </div>
      ) : null}
      <main className={mainClassName} aria-live={live ? "polite" : undefined}>
        {children}
      </main>
    </div>
  );
}
