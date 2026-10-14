import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { loginRedirectState, postLoginPath } from "./auth/loginRedirect";
import ErrorBoundary from "./components/ui/ErrorBoundary";
import Spinner from "./components/ui/Spinner";
import ChatPage from "./pages/ChatPage";
import LoginPage from "./pages/LoginPage";

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

function FullPageSpinner() {
  return (
    <div className="page-center">
      <Spinner size={28} />
    </div>
  );
}

/** The session couldn't be checked (server down or restarting): the cookie may still be valid, so offer a retry. */
function ServerUnavailable({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="page-center">
      <div className="card stack status-card" role="alert">
        <h1>Can't reach the server</h1>
        <p>{message}</p>
        <div className="status-card-actions">
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            Retry
          </button>
        </div>
      </div>
    </div>
  );
}

function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, loading, bootError, retry, signedOut } = useAuth();
  const location = useLocation();
  if (loading) return <FullPageSpinner />;
  if (!user && bootError) return <ServerUnavailable message={bootError} onRetry={retry} />;
  // After an explicit logout don't carry the page along: the next person to sign in may be someone else.
  if (!user) return <Navigate to="/login" replace state={signedOut ? undefined : loginRedirectState(location)} />;
  if (admin && !user.is_admin) return <Navigate to="/" replace />;
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
                <RequireAuth>
                  <ChatPage />
                </RequireAuth>
              }
            />
            <Route
              path="/c/:conversationId"
              element={
                <RequireAuth>
                  <ChatPage />
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
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
