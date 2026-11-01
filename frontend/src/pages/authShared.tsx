// Pieces shared by the sign-up, sign-in and emailed-link pages (docs/briefs/signup-auth.md 13, visual brief 3.8).

import { useCallback, useRef, useState, type ReactNode } from "react";
import { ApiError, authApi } from "../api/client";
import AuthCard from "../components/auth/AuthCard";
import type { TurnstileLink } from "../components/auth/Turnstile";
import { AlertCircleIcon, ArrowLeftIcon, LinkBrokenIcon } from "../components/ui/icons";
import { readFragmentToken } from "../lib/authDocument";
import "./login.css";
import "./auth.css";

/** The robot check of one form: the latest token, a submit waiting for one, and the reset after each use. */
export type Challenge = {
  /** For <Turnstile {...challenge.widget}>. */
  widget: Required<TurnstileLink>;
  /** A submit is waiting for the check ("Checking you're not a robot…"); not while the check couldn't load. */
  waiting: boolean;
  /**
   * Sends at once with the token there is, or as soon as the check gives one. While the check couldn't load, its
   * warning stays and its Try again takes focus; the form goes once a retry gives a token.
   */
  submit: (send: (token: string) => void) => void;
  /** After every request that carried the token (tokens are single use): a fresh check starts. */
  spent: () => void;
  /** Forgets a waiting submit. */
  cancel: () => void;
};

export function useChallenge(): Challenge {
  const token = useRef<string | null>(null);
  const pending = useRef<((token: string) => void) | null>(null);
  const failedNow = useRef(false);
  const [held, setHeld] = useState(false);
  const [failed, setFailed] = useState(false);
  const [resetSignal, setResetSignal] = useState(0);
  const [focusSignal, setFocusSignal] = useState(0);

  const onToken = useCallback((value: string | null) => {
    token.current = value;
    const send = pending.current;
    if (value === null || send === null) return;
    pending.current = null;
    token.current = null;
    setHeld(false);
    send(value);
  }, []);

  const onFailed = useCallback((value: boolean) => {
    failedNow.current = value;
    setFailed(value);
  }, []);

  const submit = useCallback((send: (token: string) => void) => {
    const value = token.current;
    if (value === null) {
      pending.current = send;
      setHeld(true);
      if (failedNow.current) setFocusSignal((count) => count + 1);
      return;
    }
    token.current = null;
    send(value);
  }, []);

  const spent = useCallback(() => {
    token.current = null;
    setResetSignal((value) => value + 1);
  }, []);

  const cancel = useCallback(() => {
    pending.current = null;
    setHeld(false);
  }, []);

  return {
    widget: { onToken, resetSignal, onFailed, focusSignal },
    waiting: held && !failed,
    submit,
    spent,
    cancel,
  };
}

/** A refusal of the whole form, above its submit button (the sign-in error's look); `children` go under the text. */
export function FormError({ id, message, children }: { id?: string; message: string; children?: ReactNode }) {
  return (
    <div id={id} className="login-error" role="alert">
      <AlertCircleIcon size={16} />
      <div className="login-error-body">
        <span>{message}</span>
        {children}
      </div>
    </div>
  );
}

/** The error's sentence, or `fallback` when it isn't the server's. */
export function messageOf(error: unknown, fallback = "Something went wrong. Please try again."): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** The token of the emailed link (`#t=…`), read once when the page opens; the fragment leaves the address bar. */
export function useFragmentToken(): string | null {
  const [token] = useState(readFragmentToken);
  return token;
}

/** A link without its token, on any token page. */
export function IncompleteLink() {
  return (
    <AuthCard
      title="This link is incomplete"
      lede="Open it from the email again, or copy the whole link into the address bar."
      mark={{ icon: <LinkBrokenIcon />, tone: "warning" }}
      footer={<a href="/login">Go to sign in</a>}
    />
  );
}

/** Any token page while the token is checked. */
export function CheckingLink() {
  return <AuthCard title="Checking your link…" lede="This takes a moment." mark="busy" />;
}

/** The check couldn't be made (too many attempts, the server unreachable): the reason and Try again. */
export function LinkCheckFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <AuthCard title="We couldn't check your link" lede={message} mark={{ icon: <AlertCircleIcon />, tone: "warning" }}>
      <div className="auth-actions">
        <button type="button" className="btn btn-primary auth-submit" onClick={onRetry}>
          Try again
        </button>
      </div>
    </AuthCard>
  );
}

/** "Back to sign in", a full page load of /login. */
export function BackToSignIn({ label = "Back to sign in" }: { label?: string }) {
  return (
    <a href="/login">
      <ArrowLeftIcon size={15} />
      {label}
    </a>
  );
}

/** "Contact us": a mail link when the contact address is known. */
export function ContactUs({ email, children = "Contact us" }: { email: string | null; children?: ReactNode }) {
  return email ? <a href={`mailto:${email}`}>{children}</a> : <>{children}</>;
}

const GOOGLE_NEXT_KEY = "rag.auth.googleNext";

/**
 * The page a Google sign-up should land on: the ticket keeps the `next` sent at the start, but /auth/google/pending
 * doesn't return it, so the page that started remembers it in this tab.
 */
export function rememberGoogleNext(path: string | null): void {
  try {
    if (path === null) sessionStorage.removeItem(GOOGLE_NEXT_KEY);
    else sessionStorage.setItem(GOOGLE_NEXT_KEY, path);
  } catch {
    /* storage unavailable: the sign-up lands on /plans */
  }
}

export function recallGoogleNext(): string | null {
  try {
    return sessionStorage.getItem(GOOGLE_NEXT_KEY);
  } catch {
    return null;
  }
}

const LOCAL_HOSTS: ReadonlySet<string> = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Google's sign-in page (or the local fake, for a local app): the only place a Google start may send the tab. */
function isGoogleAuthorizeUrl(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.username || parsed.password) return false;
  if (parsed.protocol === "https:" && parsed.hostname === "accounts.google.com" && parsed.port === "") return true;
  const local = LOCAL_HOSTS.has(window.location.hostname) && LOCAL_HOSTS.has(parsed.hostname);
  return local && (parsed.protocol === "http:" || parsed.protocol === "https:");
}

/** Starts Google sign-in again ("Use a different Google account") and sends the tab to Google. */
export async function restartGoogle(from: "login" | "signup"): Promise<void> {
  const { authorize_url } = await authApi.googleStart({ intent: "signin", from, next: recallGoogleNext() });
  if (!isGoogleAuthorizeUrl(authorize_url)) throw new ApiError(0, "We couldn't sign you in with Google. Try again.");
  window.location.assign(authorize_url);
}
