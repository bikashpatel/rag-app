import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError, getSessionUser, patchPreferences, setUnauthorizedHandler } from "../api/client";
import type { Me, Preferences, PreferencesUpdate } from "../api/types";
import { announceUserChanged } from "../lib/appEvents";
import {
  applyAppearance,
  cachePreferences,
  normalizePreferences,
  readCachedPreferences,
  samePreferences,
} from "../theme/appearance";

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
  /** The signed-in user's preferences (optimistic while a save is pending); the cached ones while signed out. */
  preferences: Preferences;
  /**
   * Applies at once (state, <html>, cache), then PATCH /me/preferences. One request in flight per tab: changes made
   * meanwhile are merged into the next request. Resolves with the server's result; rejects with ApiError after
   * reverting to the last confirmed preferences (re-applied and cached).
   */
  updatePreferences: (update: PreferencesUpdate) => Promise<Preferences>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

type Waiter = { resolve: (preferences: Preferences) => void; reject: (err: unknown) => void };
/** Preference changes saved by one PATCH, and the updatePreferences calls waiting for it. */
type Batch = { patch: PreferencesUpdate; waiters: Waiter[] };

/** Another tab's cached preferences go to the mounted provider; without one (no React yet) they apply directly. */
let externalPreferencesHandler: ((preferences: Preferences) => void) | null = null;

/** startAppearanceEngine's `onExternalChange` (main.tsx). */
export function handleExternalPreferences(preferences: Preferences): void {
  if (externalPreferencesHandler) externalPreferencesHandler(preferences);
  else applyAppearance(preferences);
}

function droppedError(): ApiError {
  return new ApiError(401, "You were signed out before this setting was saved.");
}

/** The update without keys set to undefined (they mean "unchanged", like omitted ones). */
function definedFields(update: PreferencesUpdate): PreferencesUpdate {
  return Object.fromEntries(Object.entries(update).filter(([, value]) => value !== undefined)) as PreferencesUpdate;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const [preferences, setPreferences] = useState<Preferences>(readCachedPreferences);

  const userRef = useRef<Me | null>(null);
  /** Bumped on every sign-in, sign-out and change of user: responses to requests of an earlier session are dropped. */
  const session = useRef(0);
  /** The last preferences the server confirmed (Me or a PATCH response); the cached ones while signed out. */
  const confirmed = useRef<Preferences>(preferences);
  /** What is shown: confirmed, with the pending changes on top. */
  const shown = useRef<Preferences>(preferences);
  const inFlight = useRef<Batch | null>(null);
  const queued = useRef<Batch | null>(null);
  /** Finished saves: a Me requested before one ended may carry older preferences, so its preferences are skipped. */
  const saves = useRef(0);
  const announcedUser = useRef<number | null>(null);

  /** Shows `next` (state and <html>; and the first-paint cache unless it came from another tab). Unchanged: no-op. */
  const show = useCallback((next: Preferences, cache = true) => {
    if (!samePreferences(shown.current, next)) {
      shown.current = next;
      setPreferences(next);
    }
    applyAppearance(next);
    if (cache) cachePreferences(next);
  }, []);

  const optimistic = useCallback(
    (): Preferences => ({ ...confirmed.current, ...inFlight.current?.patch, ...queued.current?.patch }),
    [],
  );

  /** Forgets changes not saved yet: they belong to a session that ended. A save in flight finishes unseen. */
  const dropPending = useCallback(() => {
    queued.current?.waiters.forEach((waiter) => waiter.reject(droppedError()));
    queued.current = null;
    inFlight.current = null;
  }, []);

  const signOutLocally = useCallback(() => {
    session.current += 1;
    dropPending();
    // The appearance stays as it is (and cached): the login page doesn't flash, and the next sign-in reconciles.
    confirmed.current = shown.current;
    userRef.current = null;
    setUser(null);
  }, [dropPending]);

  /** A Me from the server: sign in (or stay), and take its preferences for the fields without a pending change. */
  const receiveMe = useCallback(
    (me: Me, savesAtRequest: number) => {
      const userChanged = userRef.current?.id !== me.id;
      if (userChanged) {
        session.current += 1;
        dropPending();
      }
      userRef.current = me;
      setUser(me);
      // An older server without preferences in Me (absent field): keep what is shown.
      const hasPreferences = me.preferences !== null && typeof me.preferences === "object";
      if (hasPreferences && (userChanged || saves.current === savesAtRequest)) {
        confirmed.current = normalizePreferences(me.preferences);
        show(optimistic());
      }
    },
    [dropPending, optimistic, show],
  );

  const refresh = useCallback(async () => {
    const started = session.current;
    const savesAtRequest = saves.current;
    let me: Me;
    try {
      me = await api.get<Me>("/auth/me");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        if (session.current === started) signOutLocally();
        return;
      }
      throw err;
    }
    if (session.current === started) receiveMe(me, savesAtRequest);
  }, [receiveMe, signOutLocally]);

  const boot = useCallback(() => {
    setLoading(true);
    setBootError(null);
    // refresh() already handles 401 (signed out); anything else means the session couldn't be checked.
    refresh()
      .catch((err: unknown) => {
        setBootError(err instanceof ApiError ? err.message : "Couldn't reach the server. Please try again.");
      })
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    // A 401 is verified before signing out (U25): after "Log out others" or a password change, requests this browser
    // sent with the old cookie get 401s although the new cookie works. Concurrent 401s share one check.
    let checking = false;
    setUnauthorizedHandler(() => {
      if (checking || userRef.current === null) return;
      checking = true;
      const started = session.current;
      const savesAtRequest = saves.current;
      getSessionUser()
        .then(
          (me) => {
            if (session.current !== started) return;
            if (me) receiveMe(me, savesAtRequest);
            else signOutLocally();
          },
          // Unreachable or a server error: stay signed in; the failed request shows its own error and the next one checks again.
          () => undefined,
        )
        .finally(() => {
          checking = false;
        });
    });
    boot();
    return () => setUnauthorizedHandler(null);
  }, [boot, receiveMe, signOutLocally]);

  useEffect(() => {
    externalPreferencesHandler = (external) => {
      confirmed.current = external;
      show(optimistic(), false);
    };
    return () => {
      externalPreferencesHandler = null;
    };
  }, [optimistic, show]);

  // Per-user module stores (plugins, a queued plugin browser) clear themselves (U30). A layout effect, so they are
  // cleared before the pages of the new user run their mount effects (which load that user's state).
  const userId = user?.id ?? null;
  useLayoutEffect(() => {
    if (announcedUser.current === userId) return;
    announcedUser.current = userId;
    announceUserChanged(userId);
  }, [userId]);

  const login = useCallback(
    async (username: string, password: string) => {
      const me = await api.post<Me>("/auth/login", { username, password });
      session.current += 1;
      dropPending();
      setBootError(null);
      setSignedOut(false);
      receiveMe(me, saves.current);
    },
    [dropPending, receiveMe],
  );

  const logout = useCallback(async () => {
    // The session is an httpOnly cookie only the server can clear: if the request fails (network error, 5xx) the
    // browser still holds a valid session, so stay signed in and let the caller report it.
    await api.post<void>("/auth/logout");
    setSignedOut(true);
    signOutLocally();
  }, [signOutLocally]);

  const flush = useCallback(() => {
    const batch = queued.current;
    if (!batch || inFlight.current) return;
    queued.current = null;
    inFlight.current = batch;
    const settle = () => {
      saves.current += 1;
      inFlight.current = null;
    };
    patchPreferences(batch.patch).then(
      (result) => {
        const saved = normalizePreferences(result);
        if (inFlight.current === batch) {
          settle();
          confirmed.current = saved;
          show(optimistic());
          flush();
        }
        batch.waiters.forEach((waiter) => waiter.resolve(saved));
      },
      (err: unknown) => {
        if (inFlight.current === batch) {
          // This request's fields go back to the confirmed values, except those changed again since (still queued).
          settle();
          show(optimistic());
          flush();
        }
        batch.waiters.forEach((waiter) => waiter.reject(err));
      },
    );
  }, [optimistic, show]);

  const updatePreferences = useCallback(
    (update: PreferencesUpdate): Promise<Preferences> =>
      new Promise<Preferences>((resolve, reject) => {
        const changes = definedFields(update);
        if (userRef.current === null) {
          // Signed out: nothing to save to; the change only applies to this browser.
          confirmed.current = { ...shown.current, ...changes };
          show(confirmed.current);
          resolve(confirmed.current);
          return;
        }
        const batch = queued.current ?? { patch: {}, waiters: [] };
        batch.patch = { ...batch.patch, ...changes };
        batch.waiters.push({ resolve, reject });
        queued.current = batch;
        show(optimistic());
        flush();
      }),
    [flush, optimistic, show],
  );

  const value = useMemo(
    () => ({
      user,
      loading,
      bootError,
      retry: boot,
      signedOut,
      login,
      logout,
      refresh,
      preferences,
      updatePreferences,
    }),
    [user, loading, bootError, boot, signedOut, login, logout, refresh, preferences, updatePreferences],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
