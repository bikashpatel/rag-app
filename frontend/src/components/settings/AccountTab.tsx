import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError, getAccount, logoutOtherSessions } from "../../api/client";
import type { AccountInfo, DeviceType, LoginEvent, SignupSource } from "../../api/types";
import { useAuth, useAuthConfig } from "../../auth/AuthContext";
import { formatDate, formatRelativeTime } from "../../lib/format";
import { personName } from "../../lib/names";
import Avatar from "../ui/Avatar";
import {
  AlertIcon,
  CheckCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  InfoIcon,
  LaptopIcon,
  LogoutIcon,
  MonitorIcon,
  PhoneIcon,
  TabletIcon,
} from "../ui/icons";
import SecurityMark from "../ui/marks/SecurityMark";
import { ButtonSpinner } from "../ui/Spinner";
import EmailSection from "./EmailSection";
import NameSection from "./NameSection";
import { AccountSkeleton, PanelError, PanelHead } from "./PanelParts";
import SignInMethods from "./SignInMethods";
import { useLoad } from "./useLoad";

/** Sign-ins shown before "Show all". */
const FIRST_LOGINS = 5;
const LOCALE = "en-GB";
/** Until /auth/config answers: the server's own minimum is checked on save anyway. */
const DEFAULT_MINIMUM = 8;
/** Google's return to Settings → Account: its result, and the form a step-up was for. */
const RETURN_PARAMS = ["google", "change", "setup"] as const;

const SOURCE_LINE: Record<SignupSource, string> = {
  env: "The administrator account is set in the server's settings",
  admin: "Your account was created by the administrator",
  email: "You signed up with email",
  google: "You signed up with Google",
};

const DEVICE_ICON: Record<DeviceType, ReactNode> = {
  desktop: <LaptopIcon size={18} />,
  mobile: <PhoneIcon size={18} />,
  tablet: <TabletIcon size={18} />,
  unknown: <MonitorIcon size={18} />,
};

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Today, 09:14", "Yesterday, 17:42", "14 Oct, 08:03", "3 Dec 2025, 21:15" (local time). */
function shortMoment(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const now = new Date();
  const time = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return `Today, ${time}`;
  if (sameDay(d, yesterday)) return `Yesterday, ${time}`;
  const date = new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    year: d.getFullYear() === now.getFullYear() ? undefined : "numeric",
  }).format(d);
  return `${date}, ${time}`;
}

/** "Sat 18 Oct 2026, 09:14" (local time). */
function exactMoment(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const date = new Intl.DateTimeFormat(LOCALE, { weekday: "short", day: "numeric", month: "short", year: "numeric" }).format(d);
  const time = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d);
  return `${date}, ${time}`;
}

/** The time of a sign-in: short on screen, exact in the tooltip and for screen readers. */
function Moment({ iso, className }: { iso: string; className?: string }) {
  const exact = exactMoment(iso);
  return (
    <time className={className} dateTime={iso} title={exact}>
      <span aria-hidden="true">{shortMoment(iso)}</span>
      <span className="sr-only">{exact}</span>
    </time>
  );
}

function browserOn(event: LoginEvent): string {
  const browser = event.browser ?? "Unknown browser";
  return event.os ? `${browser} on ${event.os}` : browser;
}

/** Where a sign-in came from: the approximate address, or "Via a local network or proxy" for a non-public one. */
function Place({ event }: { event: LoginEvent }) {
  if (event.ip === null) return <>Unknown address</>;
  if (!event.ip_private) return <>{event.ip}</>;
  return (
    <span title={event.ip}>
      Via a local network or proxy<span className="sr-only"> ({event.ip})</span>
    </span>
  );
}

function placeText(event: LoginEvent): string {
  if (event.ip === null) return "unknown address";
  return event.ip_private ? "via a local network or proxy" : event.ip;
}

function methodText(event: LoginEvent): string {
  return event.method === "google" ? "with Google" : "with password";
}

/** The env admin's account is defined in .env: no email, Google or reset (functional 3.5). */
function managedByEnv(account: AccountInfo): boolean {
  return account.password_managed_by_env || account.signup_source === "env";
}

function roleLabel(isAdmin: boolean): string {
  return isAdmin ? "Administrator" : "Member";
}

function LoginRow({ event }: { event: LoginEvent }) {
  return (
    <li className="settings-login">
      <span className="settings-login-icon" aria-hidden="true">
        {DEVICE_ICON[event.device]}
      </span>
      <div className="settings-login-text">
        <b>{browserOn(event)}</b>
        <span>
          {methodText(event)} · <Place event={event} />
        </span>
      </div>
      <div className="settings-login-end">
        {event.current ? <span className="badge badge-success">This device</span> : null}
        <Moment iso={event.created_at} className="settings-login-time" />
      </div>
    </li>
  );
}

function Profile({ account }: { account: AccountInfo }) {
  const logins = account.recent_logins;
  const last = logins[0] ?? null;
  const current = logins.find((event) => event.current) ?? null;
  const lastSignIn = last?.created_at ?? account.last_login_at;
  const env = managedByEnv(account);
  const name = personName(account);
  return (
    <>
      <div className="settings-profile">
        <Avatar name={name} size={56} />
        <div>
          <div className="settings-profile-name">{name}</div>
          {account.email ? <div className="settings-profile-email">{account.email}</div> : null}
          <div className="settings-profile-meta">
            <span className={account.is_admin ? "badge badge-accent" : "badge"}>{roleLabel(account.is_admin)}</span>
            <span>{SOURCE_LINE[env ? "env" : account.signup_source]}</span>
          </div>
        </div>
      </div>
      <dl className="settings-facts">
        <dt>Username</dt>
        <dd>
          {account.username}
          {env ? null : <span className="settings-fact-note">Also works for sign-in</span>}
        </dd>
        {env ? null : (
          <>
            <dt>Email</dt>
            <dd>
              {account.email ? (
                <span className="settings-email-value">
                  <span className="badge badge-success">
                    <CheckIcon size={13} />
                    Confirmed
                  </span>
                  {account.email_verified_at ? (
                    <span className="settings-email-none">since {formatDate(account.email_verified_at)}</span>
                  ) : null}
                </span>
              ) : (
                <span className="settings-email-none">No email</span>
              )}
            </dd>
          </>
        )}
        <dt>Role</dt>
        <dd>{roleLabel(account.is_admin)}</dd>
        <dt>Account created</dt>
        <dd>{formatDate(account.created_at)}</dd>
        <dt>Last sign-in</dt>
        <dd>
          {lastSignIn ? <Moment iso={lastSignIn} /> : "Never"}
          {last ? (
            <small>
              {browserOn(last)} · {last.device === "unknown" ? "unknown device" : last.device} ·{" "}
              {methodText(last)} ·{" "}
              {last.ip_private ? (
                <span title={last.ip ?? undefined}>
                  via a local network or proxy<span className="sr-only"> ({last.ip})</span>
                </span>
              ) : (
                placeText(last)
              )}
            </small>
          ) : null}
        </dd>
        {current && current.id !== last?.id ? (
          <>
            <dt>This session</dt>
            <dd>
              {browserOn(current)}
              <small>Signed in {formatRelativeTime(current.created_at)}</small>
            </dd>
          </>
        ) : null}
      </dl>
    </>
  );
}

function SignIns({ account }: { account: AccountInfo }) {
  const listId = useId();
  const headingId = useId();
  const [all, setAll] = useState(false);
  const logins = account.recent_logins;
  const shown = all ? logins : logins.slice(0, FIRST_LOGINS);
  return (
    <div className="settings-group">
      <h3 id={headingId}>Recent sign-ins</h3>
      <p className="settings-desc">
        If you don't recognise one, change your password and log out of your other sessions. Addresses are approximate
        and are as seen by this server. Behind a proxy or tunnel, sign-ins show as coming from a local network.
      </p>
      {logins.length === 0 ? (
        <p className="settings-note">
          Sign-ins are recorded from now on.
          {account.last_login_at ? (
            <>
              {" "}
              Last sign-in: <Moment iso={account.last_login_at} />.
            </>
          ) : null}
        </p>
      ) : (
        <>
          <ul className="settings-logins" id={listId} aria-labelledby={headingId}>
            {shown.map((event) => (
              <LoginRow key={event.id} event={event} />
            ))}
          </ul>
          {logins.length > FIRST_LOGINS ? (
            <button
              type="button"
              className="btn btn-ghost btn-sm settings-more"
              aria-expanded={all}
              aria-controls={listId}
              onClick={() => setAll((value) => !value)}
            >
              {all ? "Show fewer" : `Show all (${logins.length})`}
              <ChevronDownIcon size={15} />
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}

/** A result line that takes focus when it appears (after a password change or "Log out others"). */
function Result({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <p ref={ref} className="ui-notice ui-notice-success settings-result" role="status" tabIndex={-1}>
      <CheckCircleIcon size={15} />
      <span className="ui-notice-text">{children}</span>
    </p>
  );
}

/** The env admin's email and sign-in methods: managed in .env (functional 13.7). */
function AdminSections() {
  return (
    <>
      <div className="settings-group">
        <h3>Email</h3>
        <p className="ui-notice settings-pending">
          <InfoIcon size={18} />
          <span className="ui-notice-text">The admin account is managed in .env.</span>
        </p>
      </div>
      <div className="settings-group">
        <div className="settings-group-head">
          <SecurityMark size={36} />
          <h3>Password</h3>
          <p className="settings-desc">
            The admin password is set with <code>ADMIN_PASSWORD</code> in the server's <code>.env</code> file. Change
            it there and restart the backend.
          </p>
        </div>
      </div>
    </>
  );
}

function SessionsSection() {
  const { logout } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const othersRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const errorId = useId();

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  async function logOutOthers() {
    if (working) return;
    setWorking(true);
    setError(null);
    try {
      await logoutOtherSessions();
      setConfirming(false);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't sign out the other sessions. Please try again.");
    } finally {
      setWorking(false);
    }
  }

  function cancel() {
    setConfirming(false);
    setError(null);
    othersRef.current?.focus();
  }

  return (
    <div className="settings-group">
      <div className="settings-row settings-row-wrap">
        <div className="settings-label">
          <h3>Sessions</h3>
          <p className="settings-desc">Sign out every other browser, device and API token. You stay signed in here.</p>
        </div>
        <div className="settings-actions">
          <button
            ref={othersRef}
            type="button"
            className="btn btn-sm"
            aria-expanded={confirming}
            onClick={() => {
              setDone(false);
              setError(null);
              setConfirming((value) => !value);
            }}
          >
            <LogoutIcon size={15} />
            Log out of all other sessions
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setLogoutError(null);
              logout().catch(() => setLogoutError("Couldn't sign out. Check your connection and try again."));
            }}
          >
            Log out
          </button>
        </div>
      </div>
      {confirming ? (
        <div className="settings-confirm" role="group" aria-label="Confirm logging out of other sessions">
          <p>Sign out every other browser, device and API token? You stay signed in here.</p>
          <div className="settings-actions">
            {/* aria-disabled, not disabled, while working: a failure then leaves focus here, next to its error. */}
            <button
              ref={confirmRef}
              type="button"
              className="btn btn-primary btn-sm"
              onClick={logOutOthers}
              aria-disabled={working || undefined}
              aria-busy={working || undefined}
              aria-describedby={error ? errorId : undefined}
            >
              {working ? <ButtonSpinner size={14} /> : null}
              Log out others
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={cancel} disabled={working}>
              Cancel
            </button>
          </div>
          {error ? (
            <p id={errorId} className="error-text settings-error" role="alert">
              <AlertIcon size={13} />
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
      {done ? <Result>Other sessions were signed out.</Result> : null}
      {logoutError ? (
        <p className="error-text settings-error" role="alert">
          <AlertIcon size={13} />
          {logoutError}
        </p>
      ) : null}
    </div>
  );
}

/** Google's return parameters (`?google=`, `change`, `setup`), read once; they then leave the address bar. */
function useAccountReturn(): { google: string | null; change: boolean; setup: boolean } {
  const location = useLocation();
  const navigate = useNavigate();
  const [value] = useState(() => {
    const params = new URLSearchParams(location.search);
    const google = params.get("google");
    const ok = google === "reauth_ok";
    return {
      google: ok ? null : google,
      change: ok && params.get("change") === "email",
      setup: ok && params.get("setup") === "password",
    };
  });
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (!RETURN_PARAMS.some((name) => params.has(name))) return;
    RETURN_PARAMS.forEach((name) => params.delete(name));
    const search = params.toString().replaceAll("%2F", "/");
    navigate(
      { pathname: location.pathname, search: search ? `?${search}` : "", hash: location.hash },
      { replace: true, state: location.state },
    );
  }, [location, navigate]);
  return value;
}

/** Settings → Account: profile, name, email, sign-in methods, sign-in activity, sessions (functional 13.7). */
export default function AccountTab({ onClose }: { onClose: () => void }) {
  const account = useLoad((signal) => getAccount(signal), "Couldn't load your account.");
  const { config } = useAuthConfig();
  const returned = useAccountReturn();
  const [googleResult, setGoogleResult] = useState(returned.google);
  const minimum = config?.password_min_length ?? DEFAULT_MINIMUM;
  const { reload } = account;
  // Google's result describes the account as it came back: any later action or reload makes it stale.
  const dismissResult = useCallback(() => setGoogleResult(null), []);
  const changed = useCallback(() => {
    setGoogleResult(null);
    reload();
  }, [reload]);
  let body: ReactNode;
  if (account.data) {
    const data = account.data;
    // Adding an email needs the mailer (password reset is available exactly when mail and Turnstile are set up).
    const showEmail = Boolean(config?.password_reset || data.email || data.pending_email);
    body = managedByEnv(data) ? (
      <>
        <Profile account={data} />
        <NameSection account={data} onChanged={changed} />
        <AdminSections />
        <SignIns account={data} />
        <SessionsSection />
      </>
    ) : (
      <>
        <Profile account={data} />
        <NameSection account={data} onChanged={changed} />
        {showEmail ? (
          <EmailSection account={data} resumeChange={returned.change} onAction={dismissResult} onChanged={changed} />
        ) : null}
        <SignInMethods
          account={data}
          googleSignin={config?.google_signin ?? false}
          minimum={minimum}
          googleResult={googleResult}
          resumeSetup={returned.setup}
          onAction={dismissResult}
          onChanged={changed}
        />
        <SignIns account={data} />
        <SessionsSection />
      </>
    );
  } else if (account.error) {
    body = <PanelError message={account.error} onRetry={account.reload} />;
  } else {
    body = <AccountSkeleton />;
  }
  return (
    <>
      <PanelHead title="Account" onClose={onClose} />
      <div className="settings-body" aria-busy={account.loading && !account.data}>
        {body}
      </div>
    </>
  );
}
