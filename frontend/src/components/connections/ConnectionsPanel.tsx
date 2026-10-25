import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, disconnectPlugin, startPluginConnect, updatePluginConnection } from "../../api/client";
import type { PluginInfo, PluginOAuthResult } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { formatDateTime, formatDayMonth, formatRelativeTime } from "../../lib/format";
import {
  AlertCircleIcon,
  AlertIcon,
  CheckCircleIcon,
  CheckIcon,
  ClockIcon,
  CloseIcon,
  InfoIcon,
  LockIcon,
  MailIcon,
  SearchIcon,
} from "../ui/icons";
import { ButtonSpinner } from "../ui/Spinner";
import "./connections.css";
import {
  gmailReady,
  PLUGIN_STATUS_LABEL,
  pluginStatus,
  reloadPlugins,
  setPluginConnection,
  unusableReason,
  usePlugins,
  type PluginStatus,
} from "./pluginStore";

/** Where Google sent the browser back to: `/?plugin=<id>&oauth=<result>` (SPEC). */
export type OAuthReturn = { pluginId: string; result: PluginOAuthResult };

const OAUTH_RESULTS: ReadonlySet<string> = new Set<PluginOAuthResult>([
  "connected",
  "denied",
  "expired",
  "invalid",
  "no_access",
  "disabled",
  "error",
]);

export function parseOAuthReturn(search: string): OAuthReturn | null {
  const params = new URLSearchParams(search);
  const pluginId = params.get("plugin");
  const result = params.get("oauth");
  if (!pluginId || !result || !/^[a-z0-9_]{1,32}$/.test(pluginId)) return null;
  return { pluginId, result: (OAUTH_RESULTS.has(result) ? result : "error") as PluginOAuthResult };
}

/** Where an admin sets a plugin up (the admin page's Plugins tab). */
export const ADMIN_PLUGINS_PATH = "/admin#plugins";

const DAY_MS = 86_400_000;

const TESTING_HINT =
  "This app is in Google's testing mode: only Google accounts the admin added as test users can connect. Others see “Access blocked” at Google and won't come back here; ask the admin to add your account.";

/** The same text whether or not another account shares the grant, so it reveals nothing about other users (brief 5.5). */
const DISCONNECTED_TEXT =
  "Disconnected. Google access is also removed unless another account in this app uses the same Google account; you can review access at myaccount.google.com/permissions.";

type Banner = { tone: "success" | "info" | "error"; text: string };

function pluginName(plugins: PluginInfo[] | null, pluginId: string): string {
  return plugins?.find((plugin) => plugin.id === pluginId)?.name ?? (pluginId === "gmail" ? "Gmail" : pluginId);
}

function oauthBanner({ pluginId, result }: OAuthReturn, plugins: PluginInfo[] | null): Banner {
  const name = pluginName(plugins, pluginId);
  switch (result) {
    case "connected": {
      const email = plugins?.find((plugin) => plugin.id === pluginId)?.connection?.account_email;
      return { tone: "success", text: email ? `${name} is connected as ${email}.` : `${name} is connected.` };
    }
    case "denied":
      return { tone: "info", text: "Google sign-in was cancelled." };
    case "expired":
      return { tone: "error", text: "The sign-in took too long or was already used. Try again." };
    case "invalid":
      return { tone: "error", text: "This sign-in didn't start in this browser, or your session changed. Try again." };
    case "no_access":
      return { tone: "error", text: `No ${name} access was allowed. Connect again and tick the boxes.` };
    case "disabled":
      return { tone: "error", text: `${name} was turned off by the admin.` };
    case "error":
      return { tone: "error", text: `Couldn't connect ${name}. Try again; if it keeps failing, tell the admin.` };
  }
}

/**
 * The plugin's browser ("Import from Gmail", which needs reading mail) closed by itself: say why, from the current list.
 * Null while loading, or once it is usable again.
 */
function closedBanner(pluginId: string, plugins: PluginInfo[] | null): Banner | null {
  if (plugins === null) return null;
  const name = pluginName(plugins, pluginId);
  const reason = unusableReason(plugins.find((plugin) => plugin.id === pluginId), "read", name);
  return reason ? { tone: "error", text: `“Import from ${name}” closed. ${reason}` } : null;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** The heading of a plugin's card (focus target for `focusPluginId`). */
function cardHeadingId(panelId: string, pluginId: string): string {
  return `${panelId}-${pluginId}-title`;
}

export type ConnectionsPanelProps = {
  /** Shown as a message on top when the panel opened after Google sent the browser back. */
  oauthReturn: OAuthReturn | null;
  /** The panel opened because this plugin's browser closed when the plugin stopped being usable: says why on top. */
  closedPluginId?: string | null;
  /** Once the cards have loaded, focus moves to this plugin's card (its heading). */
  focusPluginId?: string | null;
  /** "Browse Gmail": the caller closes what holds this panel and opens the plugin's browser. */
  onBrowse: (plugin: PluginInfo) => void;
};

/**
 * The user's own connections to plugins: every plugin of the catalog with its status (Not set up, Turned off by the
 * admin, Reconnect needed, Connected, Not connected), connect, Use in chat, disconnect. No dialog of its own: Settings →
 * Plugins & data holds it. Cards are sections headed by an h3.
 */
export default function ConnectionsPanel({
  oauthReturn,
  closedPluginId = null,
  focusPluginId = null,
  onBrowse,
}: ConnectionsPanelProps) {
  const { plugins, error, reload } = usePlugins();
  const panelId = useId();
  const [banner, setBanner] = useState<Banner | "oauth" | "closed" | null>(
    oauthReturn ? "oauth" : closedPluginId ? "closed" : null,
  );
  const [loadedHere, setLoadedHere] = useState(false);
  let shown: Banner | null;
  if (banner === "oauth") shown = oauthReturn ? oauthBanner(oauthReturn, plugins) : null;
  else if (banner === "closed") shown = closedPluginId ? closedBanner(closedPluginId, plugins) : null;
  else shown = banner;
  const dismissRef = useRef<HTMLButtonElement>(null);
  /** A plugin just disconnected: if its card is gone from the reloaded list, focus would be lost. */
  const disconnectedId = useRef<string | null>(null);
  const focused = useRef(false);

  useEffect(() => {
    let live = true;
    void reloadPlugins().finally(() => {
      if (live) setLoadedHere(true);
    });
    return () => {
      live = false;
    };
  }, []);

  // After the fresh load (so after whatever focused the panel's container on open), move to the asked-for card.
  useEffect(() => {
    if (!focusPluginId || !loadedHere || focused.current || plugins === null) return;
    focused.current = true;
    document.getElementById(cardHeadingId(panelId, focusPluginId))?.focus();
  }, [focusPluginId, loadedHere, plugins, panelId]);

  useEffect(() => {
    const id = disconnectedId.current;
    if (id === null || plugins === null) return;
    if (plugins.some((plugin) => plugin.id === id && plugin.connection)) return; // not reloaded yet
    disconnectedId.current = null;
    const active = document.activeElement;
    if (!plugins.some((plugin) => plugin.id === id) && (active === null || active === document.body)) {
      dismissRef.current?.focus();
    }
  }, [plugins]);

  let content: ReactNode;
  if (plugins === null) {
    content = error ? (
      <div className="ui-notice ui-notice-danger conn-state" role="alert">
        <AlertCircleIcon size={15} />
        <span className="ui-notice-text">{error}</span>
        <button type="button" className="btn btn-sm" onClick={() => void reload()}>
          Retry
        </button>
      </div>
    ) : (
      <div className="conn-card conn-card-loading" role="status" aria-busy="true">
        <span className="sr-only">Loading plugins…</span>
        <span className="ui-skeleton conn-skeleton-icon" aria-hidden="true" />
        <span className="conn-skeleton-lines" aria-hidden="true">
          <span className="ui-skeleton" style={{ width: "28%" }} />
          <span className="ui-skeleton ui-skeleton-sub" style={{ width: "52%" }} />
        </span>
      </div>
    );
  } else if (plugins.length === 0) {
    content = <p className="conn-empty">No plugins are available.</p>;
  } else {
    content = (
      <ul className="conn-list">
        {plugins.map((plugin) => (
          <li key={plugin.id}>
            <PluginConnectionCard
              plugin={plugin}
              headingId={cardHeadingId(panelId, plugin.id)}
              onBrowse={onBrowse}
              onDisconnected={() => {
                disconnectedId.current = plugin.id;
                setBanner({ tone: "info", text: DISCONNECTED_TEXT });
              }}
            />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="conn-panel">
      {shown ? (
        <div
          className={`ui-notice ui-notice-${shown.tone === "error" ? "danger" : shown.tone} conn-banner`}
          role={shown.tone === "error" ? "alert" : "status"}
        >
          {shown.tone === "success" ? (
            <CheckCircleIcon size={15} />
          ) : shown.tone === "info" ? (
            <InfoIcon size={15} />
          ) : (
            <AlertCircleIcon size={15} />
          )}
          <span className="ui-notice-text">{shown.text}</span>
          <button
            ref={dismissRef}
            type="button"
            className="btn btn-ghost btn-icon"
            aria-label="Dismiss message"
            onClick={() => setBanner(null)}
          >
            <CloseIcon size={15} />
          </button>
        </div>
      ) : null}
      {plugins !== null && error ? (
        <p className="error-text" role="alert">
          <AlertCircleIcon size={13} />
          {error}
        </p>
      ) : null}
      {content}
    </div>
  );
}

const STATUS_TONE: Record<PluginStatus, string> = {
  not_configured: "ui-status ui-status-unset",
  disabled: "ui-status ui-status-off",
  reconnect_needed: "ui-status ui-status-attention",
  connected: "ui-status ui-status-connected",
  available: "ui-status",
};

/**
 * The status line (visual spec 6.7): a word with a dot, or a lock (turned off) or a dashed circle (not set up).
 * Connected says which account.
 */
function StatusLine({ status, email }: { status: PluginStatus; email: string | null }) {
  return (
    <span className={`${STATUS_TONE[status]} conn-status`}>
      {status === "disabled" ? (
        <LockIcon size={13} />
      ) : status === "not_configured" ? (
        <span className="ui-dashed" aria-hidden="true" />
      ) : null}
      <span className="conn-status-text">
        {status === "connected" && email ? `Connected as ${email}` : PLUGIN_STATUS_LABEL[status]}
      </span>
    </span>
  );
}

type ConnectRequest = { capabilities?: string[] };

type PluginConnectionCardProps = {
  plugin: PluginInfo;
  headingId: string;
  onBrowse: (plugin: PluginInfo) => void;
  onDisconnected: () => void;
};

function PluginConnectionCard({ plugin, headingId, onBrowse, onDisconnected }: PluginConnectionCardProps) {
  const { user } = useAuth();
  const hintId = useId();
  const confirmId = useId();
  const chatId = useId();
  const [asking, setAsking] = useState<ConnectRequest | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [chatOverride, setChatOverride] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const actionsRef = useRef<HTMLDivElement>(null);
  const cancelDisconnectRef = useRef<HTMLButtonElement>(null);
  /** The button that started connecting (Connect, Reconnect, Grant access), to return to when that doesn't happen. */
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  /** Focus goes back to the card once it is idle: its trigger, else its first usable action. */
  const returnFocus = useRef(false);
  /** A failed disconnect left the confirmation open; its buttons were disabled meanwhile, which dropped focus. */
  const focusCancelDisconnect = useRef(false);
  const { connection, name } = plugin;
  const status = pluginStatus(plugin);
  /** Set up and turned on: it can be connected and used. Otherwise only an existing connection can be removed. */
  const usable = status !== "not_configured" && status !== "disabled";
  const connected = connection?.status === "connected";
  const reconnectNeeded = connection?.status === "reconnect_needed";
  const endsAt = connection?.refresh_expires_at ? Date.parse(connection.refresh_expires_at) : null;
  const lastDay = connected && endsAt !== null && endsAt - Date.now() < DAY_MS;
  const canBrowse = plugin.id === "gmail" && plugin.supports_import && gmailReady([plugin]) !== null;
  const busy = redirecting || disconnecting;

  // Back from Google with the browser's Back button, the page may come from the back/forward cache as it was left.
  useEffect(() => {
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setRedirecting(false);
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  // The button that had focus was replaced (the testing-mode hint, the disconnect confirmation) or disabled while busy:
  // once the card is idle again, put focus back on its buttons (after a cancel, a failed connect, a disconnect).
  useEffect(() => {
    if (asking || confirming || busy || !returnFocus.current) return;
    returnFocus.current = false;
    const trigger = triggerRef.current;
    triggerRef.current = null;
    const target =
      trigger?.isConnected && !trigger.disabled
        ? trigger
        : actionsRef.current?.querySelector<HTMLElement>("button:not(:disabled), a[href]");
    target?.focus();
  }, [asking, confirming, busy]);

  useEffect(() => {
    if (disconnecting || !focusCancelDisconnect.current) return;
    focusCancelDisconnect.current = false;
    cancelDisconnectRef.current?.focus();
  }, [disconnecting]);

  function cancelPrompt() {
    returnFocus.current = true;
    setAsking(null);
    setConfirming(false);
  }

  function requestConnect(request: ConnectRequest, trigger: HTMLButtonElement) {
    triggerRef.current = trigger;
    setError(null);
    setConfirming(false);
    if (plugin.testing_mode) setAsking(request);
    else void connect(request);
  }

  async function connect(request: ConnectRequest) {
    setAsking(null);
    setRedirecting(true);
    try {
      await startPluginConnect(plugin.id, request.capabilities);
    } catch (err) {
      returnFocus.current = true;
      setRedirecting(false);
      setError(errorText(err, `Couldn't start connecting ${name}. Try again.`));
    }
  }

  async function disconnect() {
    setDisconnecting(true);
    setError(null);
    try {
      await disconnectPlugin(plugin.id);
      // Focus moves to the card's buttons (Connect) once the list is reloaded and the card is idle.
      returnFocus.current = true;
      setConfirming(false);
      onDisconnected();
      await reloadPlugins();
    } catch (err) {
      focusCancelDisconnect.current = true;
      setError(errorText(err, `Couldn't disconnect ${name}. Try again.`));
    } finally {
      setDisconnecting(false);
    }
  }

  async function setChatEnabled(value: boolean) {
    setChatOverride(value);
    setError(null);
    try {
      setPluginConnection(plugin.id, await updatePluginConnection(plugin.id, { chat_enabled: value }));
    } catch (err) {
      setError(errorText(err, "Couldn't save the setting. Try again."));
    } finally {
      setChatOverride(null);
    }
  }

  const reconnectButton = (
    <button
      type="button"
      className="btn btn-primary btn-sm"
      onClick={(event) => requestConnect({}, event.currentTarget)}
      disabled={busy}
      aria-busy={redirecting || undefined}
    >
      {redirecting ? <ButtonSpinner size={12} /> : null}
      Reconnect
    </button>
  );

  /**
   * "Connected 12 Oct · last used 3 hours ago" (parts wrap whole), the account first when the status line doesn't name
   * it. A healthy connection can be renewed from here ("Reconnect"); one that needs it gets the primary button instead.
   */
  let accountLine: ReactNode = null;
  if (connection && usable) {
    const lastUsed = connection.last_used_at ? formatRelativeTime(connection.last_used_at) : "";
    accountLine = (
      <p className="conn-account">
        <span className="conn-account-parts">
          {connected ? null : <span className="conn-account-email">{connection.account_email}</span>}
          <span title={formatDateTime(connection.connected_at)}>Connected {formatDayMonth(connection.connected_at)}</span>
          {lastUsed ? <span>last used {lastUsed}</span> : null}
          {connected && !lastDay ? (
            <span>
              <button
                type="button"
                className="conn-link-btn conn-account-action"
                onClick={(event) => requestConnect({}, event.currentTarget)}
                disabled={busy}
                aria-busy={redirecting || undefined}
                aria-label={`Reconnect ${name}`}
              >
                {redirecting ? <ButtonSpinner size={11} /> : null}
                Reconnect
              </button>
            </span>
          ) : null}
        </span>
      </p>
    );
  } else if (connection) {
    accountLine = (
      <p className="conn-account">
        <span className="conn-account-email">{connection.account_email}</span> is still connected.
      </p>
    );
  }

  let intro: ReactNode = null;
  if (status === "not_configured") {
    intro = (
      <p className="conn-card-text">
        {name} isn't set up yet. The admin needs to add its settings to the server.
        {user?.is_admin ? (
          <>
            {" "}
            <Link className="conn-setup-link" to={ADMIN_PLUGINS_PATH}>
              Set up in Admin → Plugins
            </Link>
          </>
        ) : null}
      </p>
    );
  } else if (status === "disabled") {
    intro = (
      <p className="conn-card-text">
        The admin turned {name} off, so the assistant can't use it.
        {connection ? " You can still disconnect your account." : null}
      </p>
    );
  } else if (!connection) {
    intro = <p className="conn-card-text">{plugin.description}</p>;
  }

  return (
    <section className="conn-card" aria-labelledby={headingId}>
      <div className="conn-card-head">
        <span className="conn-card-icon" aria-hidden="true">
          <MailIcon size={20} />
        </span>
        <div className="conn-card-heading">
          <h3 id={headingId} className="conn-card-title" tabIndex={-1}>
            {name}
          </h3>
          <StatusLine status={status} email={connection?.account_email ?? null} />
          {accountLine}
          {intro}
        </div>
        {!asking && !confirming ? (
          <div ref={actionsRef} className="conn-actions">
            {usable && !connection ? (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={(event) => requestConnect({}, event.currentTarget)}
                disabled={busy}
                aria-busy={redirecting || undefined}
              >
                {redirecting ? <ButtonSpinner size={12} /> : null}
                Connect {name}
              </button>
            ) : null}
            {usable && (reconnectNeeded || lastDay) ? reconnectButton : null}
            {canBrowse ? (
              <button type="button" className="btn btn-sm" onClick={() => onBrowse(plugin)} disabled={busy}>
                <SearchIcon size={15} />
                Browse {name}
              </button>
            ) : null}
            {connection ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm conn-disconnect"
                onClick={(event) => {
                  // Gone while confirming, so Cancel returns focus to the first action (not an older Connect button).
                  triggerRef.current = event.currentTarget;
                  setAsking(null);
                  setConfirming(true);
                }}
                disabled={busy}
              >
                Disconnect
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {usable && plugin.capabilities.length > 0 ? (
        <ul className="conn-caps" aria-label={`What ${name} can be used for`}>
          {plugin.capabilities.map((capability) => {
            const allowed = connected && capability.enabled && capability.granted;
            const missing = connected && capability.enabled && !capability.granted;
            const off = !capability.enabled;
            const className = allowed ? "conn-cap conn-cap-allowed" : off || missing ? "conn-cap conn-cap-off" : "conn-cap";
            return (
              <li key={capability.id} className="conn-cap-item">
                <span className={className} title={off ? "Turned off by the admin" : capability.description}>
                  {allowed ? (
                    <CheckIcon size={13} />
                  ) : off ? (
                    <LockIcon size={12} />
                  ) : (
                    <span className="conn-cap-dot" aria-hidden="true" />
                  )}
                  {capability.name}
                  {off ? <span className="conn-cap-state"> · off</span> : null}
                  <span className="sr-only">
                    {allowed ? " (allowed)" : off ? " (turned off by the admin)" : missing ? " (not allowed yet)" : ""}:{" "}
                    {capability.description}
                  </span>
                </span>
                {missing ? (
                  <button
                    type="button"
                    className="conn-link-btn"
                    onClick={(event) => requestConnect({ capabilities: [capability.id] }, event.currentTarget)}
                    disabled={busy}
                    aria-label={`Grant access: ${capability.name}`}
                  >
                    Grant access
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {usable && connection ? (
        <div className="conn-switch-field">
          <div className="conn-switch-text">
            <label htmlFor={chatId} className="conn-switch-label">
              Use in chat
            </label>
            <span id={`${chatId}-hint`} className="conn-switch-hint">
              The assistant can use {name} when you ask about your email. Turn it off to keep {name} out of your chats.
            </span>
          </div>
          <label className="switch">
            <input
              id={chatId}
              type="checkbox"
              role="switch"
              checked={chatOverride ?? connection.chat_enabled}
              disabled={chatOverride !== null || busy}
              aria-describedby={`${chatId}-hint`}
              onChange={(event) => void setChatEnabled(event.target.checked)}
            />
            <span aria-hidden="true" />
          </label>
        </div>
      ) : null}

      {usable && reconnectNeeded ? (
        <div className="conn-note conn-note-warning" role="status">
          <AlertIcon size={15} />
          <p>{connection?.status_message ?? `Reconnect to keep using ${name}.`}</p>
        </div>
      ) : null}
      {usable && lastDay ? (
        <div className="conn-note conn-note-warning" role="status">
          <AlertIcon size={15} />
          <p>Google will end this connection within a day. Reconnect now to avoid an interruption.</p>
        </div>
      ) : null}
      {usable && connected && endsAt !== null && !lastDay ? (
        <div className="conn-note">
          <ClockIcon size={15} />
          <p>
            Google ends this connection around{" "}
            <span title={formatDateTime(connection?.refresh_expires_at)}>
              {formatDayMonth(connection?.refresh_expires_at ?? "")}
            </span>
            . Reconnect then.
          </p>
        </div>
      ) : null}
      {usable && !connection && plugin.testing_mode ? (
        <div className="conn-note">
          <InfoIcon size={15} />
          <p>While this app is in Google's testing mode, Google ends each connection after 7 days.</p>
        </div>
      ) : null}

      {asking ? (
        <div className="conn-prompt conn-prompt-info" role="group" aria-labelledby={hintId}>
          <p id={hintId}>{TESTING_HINT}</p>
          <div className="conn-prompt-actions">
            <button type="button" className="btn btn-sm" onClick={cancelPrompt}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => void connect(asking)} autoFocus>
              Continue
            </button>
          </div>
        </div>
      ) : null}

      {confirming ? (
        <div className="conn-prompt conn-prompt-danger" role="group" aria-labelledby={confirmId}>
          <p id={confirmId}>
            Disconnect {name}? Drafts and sent emails stay in {name}, and imported documents stay in your documents.
          </p>
          <div className="conn-prompt-actions">
            <button
              ref={cancelDisconnectRef}
              type="button"
              className="btn btn-sm"
              onClick={cancelPrompt}
              disabled={disconnecting}
              autoFocus
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger btn-sm"
              onClick={() => void disconnect()}
              disabled={disconnecting}
              aria-busy={disconnecting || undefined}
            >
              {disconnecting ? <ButtonSpinner size={12} /> : null}
              Disconnect {name}
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p className="error-text conn-error" role="alert">
          <AlertCircleIcon size={13} />
          {error}
        </p>
      ) : null}
    </section>
  );
}
