import { lazy, Suspense, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigationType, useParams } from "react-router-dom";
import type { Me } from "./api/types";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { loginRedirectState, postLoginPath } from "./auth/loginRedirect";
import SettingsHost from "./components/settings/SettingsHost";
import ErrorBoundary from "./components/ui/ErrorBoundary";
import { FullPageMessage, FullPageSpinner } from "./components/ui/FullPageState";
import { parseSettingsTab, SETTINGS_PARAM, withSettingsTab } from "./lib/settingsLink";
import BillingReturnPage from "./pages/BillingReturnPage";
import ChatPage from "./pages/ChatPage";
import CheckoutPage from "./pages/CheckoutPage";
import LoginPage from "./pages/LoginPage";
import PlansPage from "./pages/PlansPage";
import { publicRoutes } from "./pages/public/routes";

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

/**
 * Without access (no plan, or it ended) the chat goes to /plans, keeping an open Settings tab. Decided when the route
 * is entered (a page load, or a navigation to another path), not when Me changes on the page already shown, so a plan
 * that ends there doesn't move the user (8.1, 8.7). The chat page's own replace navigations keep the decision.
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
  const tab = parseSettingsTab(new URLSearchParams(search).get(SETTINGS_PARAM));
  return <Navigate to={{ pathname: "/plans", search: withSettingsTab("", tab) }} replace />;
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

function PublicOnly({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <FullPageSpinner />;
  // Same target as LoginPage's own redirect, so whichever runs first after sign-in goes to the same place.
  if (user) return <Navigate to={postLoginPath(location.state)} replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route
              path="/login"
              element={
                <PublicOnly>
                  <LoginPage />
                </PublicOnly>
              }
            />
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
