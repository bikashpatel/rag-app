import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, setUnauthorizedHandler } from "../api/client";
import type { Me } from "../api/types";

type AuthContextValue = {
  user: Me | null;
  loading: boolean;
  /**
   * Set when the session check on boot failed for a reason other than 401 (server unreachable, 5xx). The session
   * cookie may still be valid, so the app shows a retry screen instead of the login form.
   */
  bootError: string | null;
  /** Re-run the boot session check (after `bootError`). */
  retry: () => void;
  /** True after an explicit logout: the login page then doesn't send the next sign-in back to the previous page. */
  signedOut: boolean;
  login: (username: string, password: string) => Promise<void>;
  /** Rejects (and keeps the user signed in) when the server couldn't clear the session cookie. */
  logout: () => Promise<void>;
  /** Re-fetch /auth/me (e.g. to update the usage meter after a chat). */
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [signedOut, setSignedOut] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setUser(await api.get<Me>("/auth/me"));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setUser(null);
      else throw err;
    }
  }, []);

  const boot = useCallback(() => {
    setLoading(true);
    setBootError(null);
    // refresh() already handles 401 (signed out); anything else means the session couldn't be checked.
    refresh()
      .catch((err: unknown) => {
        setUser(null);
        setBootError(err instanceof ApiError ? err.message : "Couldn't reach the server. Please try again.");
      })
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    boot();
    return () => setUnauthorizedHandler(null);
  }, [boot]);

  const login = useCallback(async (username: string, password: string) => {
    const me = await api.post<Me>("/auth/login", { username, password });
    setBootError(null);
    setSignedOut(false);
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    // The session is an httpOnly cookie only the server can clear: if the request fails (network error, 5xx) the
    // browser still holds a valid session, so stay signed in and let the caller report it.
    await api.post<void>("/auth/logout");
    setSignedOut(true);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, bootError, retry: boot, signedOut, login, logout, refresh }),
    [user, loading, bootError, boot, signedOut, login, logout, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
