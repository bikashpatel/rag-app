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
import {
  api,
  ApiError,
  authApi,
  getSessionUser,
  patchPreferences,
  setPlanRequiredHandler,
  setUnauthorizedHandler,
} from "../api/client";
import type { AuthConfig, LoginRequest, Me, Preferences, PreferencesUpdate } from "../api/types";
import { announceUserChanged } from "../lib/appEvents";
import {
  applyAppearance,
  cachePreferences,
  normalizePreferences,
  readCachedPreferences,
  samePreferences,
} from "../theme/appearance";

/** The session check's state: signed-out is a 401 from /auth/me in this document (Turnstile may load only then). */
export type AuthStatus = "loading" | "signed-in" | "signed-out" | "unavailable";

type AuthContextValue = {
  user: Me | null;
  status: AuthStatus;
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
  /**
   * POST /auth/login: the browser is signed in (the session cookie) and Me comes back. This document's state stays as
   * it is: the caller leaves for the app with a full page load (leaveForApp), so no app page runs in a signed-out
   * document (docs/briefs/signup-auth.md 7.4).
   */
  login: (identifier: string, password: string, turnstileToken?: string | null) => Promise<Me>;
  /** Rejects (and keeps the user signed in) when the server couldn't clear the session cookie. */
  logout: () => Promise<void>;
  /** Re-fetch /auth/me (e.g. to update the usage meter after a chat, or when a plan ends). */
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

/** GET /auth/config, once per document (a failure is asked again by the next caller). */
let authConfig: { promise: Promise<AuthConfig>; value: AuthConfig | null } | null = null;

export function loadAuthConfig(): Promise<AuthConfig> {
  if (authConfig) return authConfig.promise;
  const entry: { promise: Promise<AuthConfig>; value: AuthConfig | null } = {
    value: null,
    promise: authApi.config().then(
      (config) => {
        entry.value = config;
        return config;
      },
      (err: unknown) => {
        if (authConfig === entry) authConfig = null;
        throw err;
      },
    ),
  };
  authConfig = entry;
  return entry.promise;
}

/** For tests: the next caller fetches the configuration afresh. */
export function forgetAuthConfig(): void {
  authConfig = null;
}

/**
 * What the signed-out pages may offer (GET /auth/config): null while it loads; `failed` when it couldn't be loaded
 * (`retry` asks again). Shared by every caller in the document.
 */
export function useAuthConfig(): { config: AuthConfig | null; failed: boolean; retry: () => void } {
  const [state, setState] = useState<{ config: AuthConfig | null; failed: boolean }>(() => ({
    config: authConfig?.value ?? null,
    failed: false,
  }));
  const [round, setRound] = useState(0);
  useEffect(() => {
    let live = true;
    loadAuthConfig().then(
      (config) => {
        if (live) setState({ config, failed: false });
      },
      () => {
        if (live) setState((current) => ({ config: current.config, failed: current.config === null }));
      },
    );
    return () => {
      live = false;
    };
  }, [round]);
  const retry = useCallback(() => {
    setState((current) => ({ ...current, failed: false }));
    setRound((value) => value + 1);
  }, []);
  return { ...state, retry };
}

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

/** setTimeout's delay must fit in 32 bits (about 24.8 days): a later end is checked again from a refresh at 24 days. */
const MAX_TIMER_MS = 24 * 86_400_000;
/** After the paid time ends, so the server already agrees. */
const PLAN_END_SLACK_MS = 1_000;
/** When this browser's clock is ahead of the server's: Me still has access after its paid time; ask again later. */
const PLAN_END_RECHECK_MS = 60_000;

/** Milliseconds until Me should be fetched again because the paid time ends; null when access doesn't end by time. */
function planRefreshDelay(user: Me | null): number | null {
  const paidThrough = user?.plan.access ? user.plan.paid_through : null;
  const end = paidThrough ? Date.parse(paidThrough) : Number.NaN;
  if (Number.isNaN(end)) return null;
  const delay = end + PLAN_END_SLACK_MS - Date.now();
  return delay > 0 ? Math.min(delay, MAX_TIMER_MS) : PLAN_END_RECHECK_MS;
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
    // The session check (U25): getSessionUser waits for a cookie re-issue in flight; null only on 401. A signed-in tab
    // checks a 401 once more, in case this request left with the cookie "Log out others" or a password change replaced.
    let me = await getSessionUser();
    if (me === null && userRef.current !== null && session.current === started) me = await getSessionUser();
    if (session.current !== started) return;
    if (me) receiveMe(me, savesAtRequest);
    else signOutLocally();
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
    // A 402 plan_required or plan_expired: the plan ended (or was revoked). Concurrent ones share one refresh of Me,
    // which the pages then mirror (8.7); errors are left to the request that failed.
    let refreshing = false;
    setPlanRequiredHandler(() => {
      if (refreshing || userRef.current === null) return;
      refreshing = true;
      refresh()
        .catch(() => undefined)
        .finally(() => {
          refreshing = false;
        });
    });
    return () => setPlanRequiredHandler(null);
  }, [refresh]);

  // An open tab learns that the paid time ended (8.1): re-armed by every Me.
  useEffect(() => {
    const delay = planRefreshDelay(user);
    if (delay === null) return;
    const timer = window.setTimeout(() => {
      refresh().catch(() => undefined);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [user, refresh]);

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

  const login = useCallback((identifier: string, password: string, turnstileToken?: string | null) => {
    const body: LoginRequest = { identifier, password };
    if (turnstileToken) body.turnstile_token = turnstileToken;
    return api.post<Me>("/auth/login", body);
  }, []);

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

  let status: AuthStatus = "signed-out";
  if (loading) status = "loading";
  else if (user) status = "signed-in";
  else if (bootError) status = "unavailable";

  const value = useMemo(
    () => ({
      user,
      status,
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
    [user, status, loading, bootError, boot, signedOut, login, logout, refresh, preferences, updatePreferences],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
