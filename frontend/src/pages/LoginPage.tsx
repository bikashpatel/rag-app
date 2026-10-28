import { useId, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { postLoginPath } from "../auth/loginRedirect";
import PolicyLinks from "../components/billing/PolicyLinks";
import { useMediaQuery } from "../components/chat/useMediaQuery";
import { AlertCircleIcon, ShieldIcon } from "../components/ui/icons";
import PasswordInput from "../components/ui/PasswordInput";
import { ButtonSpinner } from "../components/ui/Spinner";
import HeroArt from "./login/HeroArt";
import { Deck, Headline, Pitch } from "./login/LoginCopy";
import ProductTour from "./login/ProductTour";
import "./login.css";

/** Below this the aside (and its tour) isn't rendered: login.css hides it at the same width. */
const PHONE_QUERY = "(max-width: 799.98px)";

/** The brand mark and name (decorative here: the page's heading names the app for assistive tech). */
function Brand() {
  return (
    <span className="ui-brand" aria-hidden="true">
      <span className="ui-brand-mark">N</span>
      <span className="ui-brand-name">AskNuvexusDoc</span>
    </span>
  );
}

/**
 * Sign-in (visual spec 6.28): a split page with the form card on the right and, on the left, the aside with the
 * owner's hero picture and copy around the product tour. The form comes first in the DOM, so keyboard and screen-reader
 * users reach it before the copy; the grid places the aside on the left, and the form stays in view while the page
 * scrolls. Phones get no aside: the picture shows above the form (placed there by CSS, it follows the form in the DOM)
 * and the copy below it.
 */
export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const phone = useMediaQuery(PHONE_QUERY);
  const usernameId = useId();
  const errorId = useId();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      // Back to the page that sent us here (e.g. a bookmarked conversation after the session expired).
      navigate(postLoginPath(location.state), { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const describedBy = error ? errorId : undefined;

  return (
    <div className="login-page">
      <main className="login-main">
        <div className="login-card">
          <h1 className="login-title">
            Welcome back<span className="sr-only"> to AskNuvexusDoc</span>
          </h1>
          <p className="login-lede">Sign in to continue to your documents and chats.</p>

          <form className="login-form" onSubmit={onSubmit} noValidate>
            <div className="field">
              <label className="field-label" htmlFor={usernameId}>
                Username
              </label>
              <input
                id={usernameId}
                className="input"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                aria-describedby={describedBy}
                required
              />
            </div>
            <PasswordInput
              label="Password"
              autoComplete="current-password"
              placeholder="Your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-describedby={describedBy}
              required
            />
            {error ? (
              <div id={errorId} className="login-error" role="alert">
                <AlertCircleIcon size={16} />
                <span>{error}</span>
              </div>
            ) : null}
            <button
              className="btn btn-primary login-submit"
              type="submit"
              disabled={submitting || !username.trim() || !password}
              aria-busy={submitting || undefined}
            >
              {submitting ? <ButtonSpinner size={15} /> : null}
              Sign in
            </button>
          </form>

          <div className="login-foot">
            <ShieldIcon size={16} />
            <span>Accounts are created by your administrator. Ask them if you need access or a new password.</span>
          </div>

          {phone ? (
            <>
              <div className="login-mobile-hero">
                <Brand />
                <Headline />
                <HeroArt phone />
              </div>
              <div className="login-about">
                <Deck />
                <Pitch />
              </div>
            </>
          ) : null}

          <p className="login-credit">This application is designed, developed &amp; maintained by ELARXSYS.COM</p>
          <PolicyLinks compact className="login-policies" />
        </div>
      </main>

      {phone ? null : (
        <aside className="login-aside" aria-label="About AskNuvexusDoc">
          <div className="login-hero">
            <Brand />
            <Headline />
            <HeroArt />
            <Deck />
          </div>
          <ProductTour />
          <Pitch />
        </aside>
      )}
    </div>
  );
}
