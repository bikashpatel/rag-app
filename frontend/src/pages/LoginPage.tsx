import { useId, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { postLoginPath } from "../auth/loginRedirect";
import PolicyLinks from "../components/billing/PolicyLinks";
import { useMediaQuery } from "../components/chat/useMediaQuery";
import { AlertCircleIcon, FileIcon, LockIcon, QuoteIcon, ShieldIcon } from "../components/ui/icons";
import PasswordInput from "../components/ui/PasswordInput";
import { ButtonSpinner } from "../components/ui/Spinner";
import ProductTour from "./login/ProductTour";
import "./login.css";

/** Below this the aside (and its tour) isn't rendered: login.css hides it at the same width. */
const PHONE_QUERY = "(max-width: 799.98px)";

/** The brand mark and name (decorative here: the page's heading names the app for assistive tech). */
function Brand() {
  return (
    <span className="ui-brand">
      <span className="ui-brand-mark">R</span>
      <span className="ui-brand-name">RAG Assistant</span>
    </span>
  );
}

/**
 * Sign-in (visual spec 6.28): a split page with the form card on the right and the editorial aside with the product
 * tour on the left (not rendered on phones, where a compact hero sits above the form). The form comes first in the
 * DOM, so keyboard and screen-reader users reach it before the tour; the grid places the aside on the left.
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
          <div className="login-mobile-hero" aria-hidden="true">
            <Brand />
            <p className="login-hero-title">
              Ask your documents <em>anything.</em>
            </p>
            <p className="login-hero-deck">Every answer points to the passages it came from.</p>
          </div>

          <h1 className="login-title">
            Welcome back<span className="sr-only"> to RAG Assistant</span>
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

          <p className="login-credit">This application is designed, developed &amp; maintained by ELARXSYS.COM</p>
          <PolicyLinks compact className="login-policies" />
        </div>
      </main>

      {phone ? null : (
        <div className="login-aside">
          <div className="login-hero" aria-hidden="true">
            <Brand />
            <p className="login-hero-title">
              Ask your documents <em>anything.</em>
            </p>
          </div>
          <ProductTour />
          <ul className="login-points" aria-hidden="true">
            <li>
              <LockIcon size={15} />
              Private to you
            </li>
            <li>
              <QuoteIcon size={15} />
              Cited answers
            </li>
            <li>
              <FileIcon size={15} />
              Files and images on request
            </li>
          </ul>
        </div>
      )}
    </div>
  );
}
