import { useEffect, useState } from "react";
import { ApiError, authApi } from "../../api/client";
import type { GoogleStart } from "../../api/types";
import { inAppBrowser } from "../../lib/authDocument";
import { CheckIcon, CopyIcon, InfoIcon } from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";
import googleG from "./google-g.svg?raw";
import "./auth-components.css";

const START_FAILED = "We couldn't sign you in with Google. Try again.";
const COPIED_MS = 3_000;
const LOCAL_HOSTS: ReadonlySet<string> = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Google's standard four-colour "G", inlined (no request) and never recoloured. Decorative. */
export function GoogleMark({ size = 18 }: { size?: number }) {
  return (
    <span
      className="auth-google-g"
      style={{ width: size, height: size }}
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: googleG }}
    />
  );
}

/** The rule between Google and the email form: "or" on sign-in, "or sign up with email" on sign-up. */
export function OrRule({ label }: { label: string }) {
  return (
    <div className="auth-or" role="separator" aria-label={label}>
      <span aria-hidden="true">{label}</span>
    </div>
  );
}

/** Where Continue with Google may send the browser: Google's sign-in page (or a local fake, for a local app). */
function isAuthorizeUrl(url: string): boolean {
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

/** In Instagram, Facebook, LinkedIn and other in-app browsers Google refuses to sign in: offer the link instead. */
function InAppNotice() {
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (copy !== "copied") return;
    const timer = window.setTimeout(() => setCopy("idle"), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copy]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopy("copied");
    } catch {
      setCopy("failed");
    }
  }

  return (
    <div className="ui-notice auth-inapp">
      <InfoIcon size={18} />
      <div className="ui-notice-text">
        <p className="auth-inapp-text">
          Google sign-in doesn't work inside this app. Open asknuvexusdoc.com in Chrome or Safari.
        </p>
        <button type="button" className="btn btn-sm auth-inapp-copy" onClick={copyLink}>
          {copy === "copied" ? <CheckIcon size={15} /> : <CopyIcon size={15} />}
          {copy === "copied" ? "Link copied" : "Copy link"}
        </button>
        <span className="sr-only" role="status">
          {copy === "copied" ? "Link copied" : ""}
        </span>
        {copy === "failed" ? <p className="auth-inapp-url">{window.location.href}</p> : null}
      </div>
    </div>
  );
}

/**
 * Continue with Google (Google's own button look; the only label Google allows us here). Starts the server-side flow
 * and sends this tab to Google's sign-in page; `onError` gets the sentence to show when it can't. In an in-app
 * browser it is the notice with Copy link instead.
 */
export default function GoogleButton({
  intent,
  next,
  from,
  password,
  size = "lg",
  onError,
  canStart,
}: {
  intent: GoogleStart["intent"];
  next?: string;
  from?: "login" | "signup";
  password?: string;
  size?: "lg" | "sm";
  onError(message: string): void;
  /** Asked on each press: false keeps the flow from starting (the caller then says why). */
  canStart?: () => boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [embedded] = useState(() => inAppBrowser());

  // Back from Google through the browser's page cache: the page is live again, so the button is too.
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setBusy(false);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  if (embedded) return <InAppNotice />;

  async function start() {
    if (busy || (canStart && !canStart())) return;
    setBusy(true);
    try {
      const { authorize_url } = await authApi.googleStart({ intent, next, from, password });
      if (!isAuthorizeUrl(authorize_url)) throw new ApiError(0, START_FAILED);
      window.location.assign(authorize_url);
    } catch (err) {
      setBusy(false);
      onError(err instanceof ApiError ? err.message : START_FAILED);
    }
  }

  return (
    <button
      type="button"
      className={size === "sm" ? "auth-google auth-google-sm" : "auth-google"}
      onClick={start}
      aria-busy={busy || undefined}
    >
      <GoogleMark />
      <span>Continue with Google</span>
      {busy ? <ButtonSpinner size={11} /> : null}
    </button>
  );
}
