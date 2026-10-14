import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import Spinner from "../components/ui/Spinner";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
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
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page-center">
      <form className="card login-card stack" onSubmit={onSubmit} noValidate>
        <div className="login-brand">
          <div className="login-logo" aria-hidden="true">
            R
          </div>
          <div>
            <h1>RAG Assistant</h1>
            <div className="muted">Sign in to chat with your documents</div>
          </div>
        </div>
        <label className="field">
          <span className="field-label">Username</span>
          <input
            className="input"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error ? (
          <div className="error-text" role="alert">
            {error}
          </div>
        ) : null}
        <button className="btn btn-primary" type="submit" disabled={submitting || !username.trim() || !password}>
          {submitting ? <Spinner size={14} label="Signing in" /> : null}
          Sign in
        </button>
        <div className="field-hint">Accounts are created by the administrator.</div>
      </form>
    </div>
  );
}
