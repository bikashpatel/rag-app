import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigationType, useParams } from "react-router-dom";
import type { Me } from "./api/types";
import { AuthProvider, loadAuthConfig, useAuth } from "./auth/AuthContext";
import { loginRedirectState, postLoginPath } from "./auth/loginRedirect";
import SettingsHost from "./components/settings/SettingsHost";
import ErrorBoundary from "./components/ui/ErrorBoundary";
import { FullPageMessage, FullPageSpinner } from "./components/ui/FullPageState";
import { leaveForApp, turnstileAllowed } from "./lib/authDocument";
import { parseSettingsTab, SETTINGS_PARAM, withSettingsTab } from "./lib/settingsLink";
import BillingReturnPage from "./pages/BillingReturnPage";
import ChatPage from "./pages/ChatPage";
import CheckoutPage from "./pages/CheckoutPage";
import ConfirmEmailPage from "./pages/ConfirmEmailPage";
import ForgotPasswordPage from "./pages/ForgotPasswordPage";
import GoogleSignupPage from "./pages/GoogleSignupPage";
import LoginPage from "./pages/LoginPage";
import PlansPage from "./pages/PlansPage";
import { publicRoutes } from "./pages/public/routes";
import ResetPasswordPage from "./pages/ResetPasswordPage";
import SignupPage from "./pages/SignupPage";
import UndoEmailPage from "./pages/UndoEmailPage";
import VerifyEmailPage from "./pages/VerifyEmailPage";

const CHUNK_RELOAD_KEY = "rag.chunkReload";

// After a redeploy an open tab still references the old hashed chunk, which is now a 404: reload once to pick up
// the new index.html. If that already happened (or storage is unavailable, so there's no loop guard), rethrow so
// the ErrorBoundary shows instead of reloading forever.
const AdminPage = lazy(() =>
  import("./pages/AdminPage").then(
    (mod) => {
      try {
        sessionStorage.removeItem(CHUNK_RELOAD_KEY);
      } catch {
        /* storage unavailable */
      }
      return mod;
    },
    (err: unknown) => {
      let alreadyReloaded = true;
      try {
        alreadyReloaded = sessionStorage.getItem(CHUNK_RELOAD_KEY) !== null;
        if (!alreadyReloaded) sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
      } catch {
        /* storage unavailable: never auto-reload */
      }
      if (alreadyReloaded) throw err;
      window.location.reload();
      return new Promise<never>(() => {}); // keep the Suspense spinner until the reload happens
    },
  ),
);

/** The session couldn't be checked (server down or restarting): the cookie may still be valid, so offer a retry. */
function ServerUnavailable({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <FullPageMessage
      title="Can't reach the server"
      action={
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          Try again
        </button>
      }
    >
      <p>{message}</p>
    </FullPageMessage>
  );
}

/** /settings/:tab is a deep link to the Settings dialog over the chat: /?settings=<tab> (unknown tabs: General). */
function SettingsRedirect() {
  const { tab } = useParams();
  return <Navigate to={`/${withSettingsTab("", parseSettingsTab(tab ?? "general"))}`} replace />;
}

/** The parameters of a return to Settings → Account (Google's link and step-up results) that /plans keeps too. */
const ACCOUNT_RETURN_PARAMS = ["google", "change", "setup"] as const;

/** `?settings=<tab>` and the account-return parameters of `search`, for the redirect to /plans. */
function plansSearch(search: string): string {
  const params = new URLSearchParams(search);
  const kept = new URLSearchParams(withSettingsTab("", parseSettingsTab(params.get(SETTINGS_PARAM))));
  for (const name of ACCOUNT_RETURN_PARAMS) {
    const value = params.get(name);
    if (value !== null) kept.set(name, value);
  }
  const query = kept.toString();
  return query ? `?${query}` : "";
}

/**
 * Without access (no plan, or it ended) the chat goes to /plans, keeping an open Settings tab (and a return from
 * Google to Settings → Account). Decided when the route is entered (a page load, or a navigation to another path),
 * not when Me changes on the page already shown, so a plan that ends there doesn't move the user (8.1, 8.7). The chat
 * page's own replace navigations keep the decision.
 */
function PlanGate({ user, children }: { user: Me; children: ReactNode }) {
  const { pathname, search } = useLocation();
  const navigationType = useNavigationType();
  const hasAccess = user.is_admin || user.plan.access;
  const [entry, setEntry] = useState({ pathname, allowed: hasAccess });
  let current = entry;
  if (entry.pathname !== pathname) {
    current = { pathname, allowed: navigationType === "REPLACE" ? entry.allowed : hasAccess };
    setEntry(current);
  }
  if (current.allowed) return <>{children}</>;
  return <Navigate to={{ pathname: "/plans", search: plansSearch(search) }} replace />;
}

export function RequireAuth({
  children,
  admin = false,
  needsPlan = false,
}: {
  children: ReactNode;
  admin?: boolean;
  /** The chat routes: only with access to the app (a plan, or the admin). */
  needsPlan?: boolean;
}) {
  const { user, loading, bootError, retry, signedOut } = useAuth();
  const location = useLocation();
  if (loading) return <FullPageSpinner />;
  if (!user && bootError) return <ServerUnavailable message={bootError} onRetry={retry} />;
  // After an explicit logout don't carry the page along: the next person to sign in may be someone else.
  if (!user) return <Navigate to="/login" replace state={signedOut ? undefined : loginRedirectState(location)} />;
  if (admin && !user.is_admin) return <Navigate to="/" replace />;
  if (needsPlan) return <PlanGate user={user}>{children}</PlanGate>;
  return <>{children}</>;
}

/** A full page load into the app (a document whose CSP allowed Cloudflare's script never runs an app page). */
function LeaveForApp({ path }: { path: string }) {
  useEffect(() => leaveForApp(path), [path]);
  return <FullPageSpinner />;
}

/**
 * The signed-out pages (/login, /signup, /forgot-password): a session goes to the app, the place sign-in would have
 * gone to. The sign-up configuration is asked for at once, beside the session check.
 */
function PublicOnly({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  useEffect(() => {
    loadAuthConfig().catch(() => undefined);
  }, []);
  if (loading) return <FullPageSpinner />;
  if (user) {
    const path = postLoginPath(location.state, location.search);
    return turnstileAllowed() ? <LeaveForApp path={path} /> : <Navigate to={path} replace />;
  }
  return <>{children}</>;
}

/** A signed-out page at `path`. */
function publicOnly(path: string, page: ReactNode) {
  return <Route path={path} element={<PublicOnly>{page}</PublicOnly>} />;
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            {publicOnly("/login", <LoginPage />)}
            {publicOnly("/signup", <SignupPage />)}
            {publicOnly("/forgot-password", <ForgotPasswordPage />)}
            {/* The emailed links and Google's return: with or without a session. */}
            <Route path="/verify-email" element={<VerifyEmailPage />} />
            <Route path="/signup/google" element={<GoogleSignupPage />} />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            <Route path="/confirm-email" element={<ConfirmEmailPage />} />
            <Route path="/undo-email" element={<UndoEmailPage />} />
            <Route
              path="/"
              element={
                <RequireAuth needsPlan>
                  <ChatPage />
                </RequireAuth>
              }
            />
            <Route
              path="/c/:conversationId"
              element={
                <RequireAuth needsPlan>
                  <ChatPage />
                </RequireAuth>
              }
            />
            <Route
              path="/plans"
              element={
                <RequireAuth>
                  <PlansPage />
                </RequireAuth>
              }
            />
            <Route
              path="/plans/checkout/:code"
              element={
                <RequireAuth>
                  <CheckoutPage />
                </RequireAuth>
              }
            />
            <Route
              path="/billing/return"
              element={
                <RequireAuth>
                  <BillingReturnPage />
                </RequireAuth>
              }
            />
            <Route
              path="/admin"
              element={
                <RequireAuth admin>
                  <Suspense fallback={<FullPageSpinner />}>
                    <AdminPage />
                  </Suspense>
                </RequireAuth>
              }
            />
            <Route path="/settings" element={<SettingsRedirect />} />
            <Route path="/settings/:tab" element={<SettingsRedirect />} />
            {publicRoutes}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <SettingsHost />
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
