import { useId, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { postLoginPath } from "../auth/loginRedirect";
import { AlertCircleIcon, FileIcon, LockIcon, QuoteIcon, ShieldIcon, SparkleIcon } from "../components/ui/icons";
import PasswordInput from "../components/ui/PasswordInput";
import { ButtonSpinner } from "../components/ui/Spinner";
import "./login.css";

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
 * Sign-in (visual spec 6.28): a split page with the editorial aside on the left (hidden on phones, where a compact hero
 * sits above the form) and the form card on the right.
 */
export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
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
      <aside className="login-aside" aria-hidden="true">
        <Brand />
        <div className="login-hero">
          <p className="login-hero-title">
            Ask your documents <em>anything.</em>
          </p>
          <p className="login-hero-deck">
            Upload contracts, handbooks and reports, then talk them through. Every answer points to the passages it came
            from.
          </p>
        </div>

        <div className="login-specimens">
          <div className="login-spec login-spec-doc">
            <div className="login-spec-doc-head">
              <span className="login-spec-tag">PDF</span>
              <span>
                <b>Northwind-MSA-2025.pdf</b>
                <small>Page 12 · Term and renewal</small>
              </span>
            </div>
            <div className="login-spec-lines">
              <i />
              <i style={{ width: "92%" }} />
              <i className="login-spec-hl" style={{ width: "86%" }} />
              <i className="login-spec-hl" style={{ width: "64%" }} />
              <i style={{ width: "96%" }} />
              <i style={{ width: "58%" }} />
            </div>
          </div>
          <div className="login-spec login-spec-answer">
            <div className="login-spec-answer-head">
              <span className="login-spec-mark">
                <SparkleIcon size={14} />
              </span>
              <span>Answer</span>
            </div>
            <p>
              Northwind renews every 12 months unless either side gives <strong>60 days’ written</strong>{" "}
              <span className="login-spec-glue">
                <strong>notice</strong>.<span className="login-spec-cite">1</span>
              </span>
            </p>
            <div className="login-spec-answer-foot">
              <span className="login-spec-dots">
                <span className="login-spec-dot login-spec-dot-red">
                  <FileIcon size={13} />
                </span>
                <span className="login-spec-dot login-spec-dot-blue">
                  <FileIcon size={13} />
                </span>
              </span>
              2 sources
            </div>
          </div>
        </div>

        <ul className="login-points">
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
      </aside>

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
        </div>
      </main>
    </div>
  );
}
