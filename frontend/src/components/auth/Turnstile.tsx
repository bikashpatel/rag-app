import { useEffect, useId, useRef, useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import { reloadForTurnstile, turnstileAllowed } from "../../lib/authDocument";
import { useResolvedTheme, type ResolvedTheme } from "../../theme/appearance";
import { AlertIcon } from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";
import "./auth-components.css";

export type TurnstileAction = "signup" | "resend" | "reset" | "login";

type RenderOptions = {
  sitekey: string;
  action: TurnstileAction;
  theme: ResolvedTheme;
  size: "flexible";
  appearance: "always";
  "refresh-expired": "auto";
  "response-field": false;
  language: "auto";
  callback: (token: string) => void;
  "expired-callback": () => void;
  "error-callback": () => void;
};

/** The part of Cloudflare's `window.turnstile` the widget uses (explicit rendering). */
export type TurnstileApi = {
  render(container: HTMLElement, options: RenderOptions): string | null | undefined;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

/** From this exact URL: Cloudflare forbids copies, proxies and pinned hashes of it. */
export const TURNSTILE_SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const LOAD_TIMEOUT_MS = 15_000;

let loading: Promise<void> | null = null;

/** Adds Cloudflare's script once; widgets mounted meanwhile share the load, and a failed load is tried afresh. */
function loadScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = TURNSTILE_SCRIPT_URL;
    script.async = true;
    script.onload = () => {
      loading = null;
      if (window.turnstile) resolve();
      else reject(new Error("Turnstile didn't start"));
    };
    script.onerror = () => {
      loading = null;
      script.remove();
      reject(new Error("Turnstile didn't load"));
    };
    document.head.append(script);
  });
  return loading;
}

function googleButton(): HTMLButtonElement | null {
  return document.querySelector<HTMLButtonElement>("button.auth-google");
}

/** How a form's challenge (pages/authShared useChallenge) follows its widget. */
export type TurnstileLink = {
  /** Each token, and null when it expires or the widget goes. */
  onToken(token: string | null): void;
  /** Bumped after every submit (tokens are single use): the widget starts again. */
  resetSignal?: number;
  /** Whether the couldn't-load warning is up. */
  onFailed?(failed: boolean): void;
  /** Bumped when a submit came while the warning is up: its Try again takes focus. */
  focusSignal?: number;
};

/**
 * Cloudflare's robot check (Managed, explicit rendering) in a slot that keeps its 65 px whatever happens. Nothing
 * loads until the session check said "signed out" in this document, and only in a document whose CSP allows it (a
 * page elsewhere reloads itself once). Follows the app's theme; says when it couldn't load (TurnstileLink).
 */
export default function Turnstile({
  siteKey,
  action,
  onToken,
  resetSignal = 0,
  onFailed,
  focusSignal = 0,
}: { siteKey: string; action: TurnstileAction } & TurnstileLink) {
  const { user, loading: checkingSession, bootError, retry } = useAuth();
  const signedOut = !checkingSession && !bootError && user === null;
  const theme = useResolvedTheme();
  const container = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const tokenListener = useRef(onToken);
  const failedListener = useRef(onFailed);
  const lastReset = useRef(resetSignal);
  const lastFocus = useRef(focusSignal);
  const retryRef = useRef<HTMLButtonElement>(null);
  const warningId = useId();
  const [phase, setPhase] = useState<"loading" | "ready" | "failed">("loading");
  const [attempt, setAttempt] = useState(0);
  const [googleOnPage, setGoogleOnPage] = useState(false);

  useEffect(() => {
    tokenListener.current = onToken;
    failedListener.current = onFailed;
  }, [onToken, onFailed]);

  useEffect(() => {
    if (!signedOut) return;
    if (!turnstileAllowed()) {
      if (!reloadForTurnstile()) setPhase("failed");
      return;
    }
    let live = true;
    const fail = () => {
      if (!live) return;
      tokenListener.current(null);
      setPhase("failed");
    };
    setPhase("loading");
    const timer = window.setTimeout(() => {
      if (widget.current === null) fail();
    }, LOAD_TIMEOUT_MS);
    loadScript().then(() => {
      const api = window.turnstile;
      if (!live || !api || !container.current) return;
      window.clearTimeout(timer);
      setPhase("ready");
      try {
        widget.current =
          api.render(container.current, {
            sitekey: siteKey,
            action,
            theme,
            size: "flexible",
            appearance: "always",
            "refresh-expired": "auto",
            "response-field": false,
            language: "auto",
            callback: (token) => {
              if (!live) return;
              setPhase("ready");
              tokenListener.current(token);
            },
            "expired-callback": () => {
              if (live) tokenListener.current(null);
            },
            "error-callback": fail,
          }) ?? null;
      } catch {
        fail();
      }
    }, fail);
    return () => {
      live = false;
      window.clearTimeout(timer);
      if (widget.current !== null) window.turnstile?.remove(widget.current);
      widget.current = null;
      tokenListener.current(null);
    };
  }, [signedOut, siteKey, action, theme, attempt]);

  useEffect(() => {
    if (lastReset.current === resetSignal) return;
    lastReset.current = resetSignal;
    if (widget.current !== null) window.turnstile?.reset(widget.current);
    tokenListener.current(null);
  }, [resetSignal]);

  const shown = bootError ? "failed" : signedOut ? phase : "waiting";
  useEffect(() => {
    if (shown === "failed") setGoogleOnPage(googleButton() !== null);
    failedListener.current?.(shown === "failed");
  }, [shown]);

  useEffect(() => () => failedListener.current?.(false), []);

  useEffect(() => {
    if (lastFocus.current === focusSignal) return;
    lastFocus.current = focusSignal;
    retryRef.current?.scrollIntoView({ block: "center" });
    retryRef.current?.focus();
  }, [focusSignal]);

  function tryAgain() {
    if (bootError) retry();
    else setAttempt((value) => value + 1);
  }

  function toGoogle() {
    const button = googleButton();
    button?.scrollIntoView({ block: "center" });
    button?.focus();
  }

  return (
    <div className="auth-turnstile">
      {shown === "loading" ? (
        <div className="auth-turnstile-loading">
          <ButtonSpinner size={14} />
          <span>Loading the security check…</span>
        </div>
      ) : null}
      <div ref={container} className="auth-turnstile-widget" />
      {shown === "failed" ? (
        <div className="ui-notice ui-notice-warning" role="alert">
          <AlertIcon size={18} />
          <div className="ui-notice-text">
            <p className="auth-turnstile-text" id={warningId}>
              The security check couldn't load. Check your connection or turn off blockers for this site, then try
              again.
            </p>
            <div className="auth-turnstile-retry">
              <button
                ref={retryRef}
                type="button"
                className="btn btn-sm"
                onClick={tryAgain}
                aria-describedby={warningId}
              >
                Try again
              </button>
              {googleOnPage ? (
                <button type="button" className="auth-turnstile-google" onClick={toGoogle}>
                  or continue with Google
                </button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * "Checking you're not a robot…" under the widget while a submitted form waits for its token (the form goes out when
 * the token arrives). Always rendered, so the polite live region exists before the words appear: put it with the
 * widget in one wrapper so an empty line adds no gap to the form.
 */
export function TurnstileWait({ waiting }: { waiting: boolean }) {
  return (
    <p className="auth-turnstile-wait" aria-live="polite">
      {waiting ? (
        <>
          <ButtonSpinner size={13} />
          <span>Checking you're not a robot…</span>
        </>
      ) : null}
    </p>
  );
}
