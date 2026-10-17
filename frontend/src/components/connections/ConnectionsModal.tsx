import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiError, disconnectPlugin, startPluginConnect, updatePluginConnection } from "../../api/client";
import type { PluginInfo, PluginOAuthResult } from "../../api/types";
import { formatDateTime, formatRelativeTime } from "../../lib/format";
import { AlertIcon, CheckIcon, CloseIcon, MailIcon } from "../chat/icons";
import Modal from "../ui/Modal";
import Spinner from "../ui/Spinner";
import "./connections.css";
import { gmailReady, reloadPlugins, setPluginConnection, unusableReason, usePlugins } from "./pluginStore";

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

type ConnectionsModalProps = {
  /** Shown as a message on top when the dialog opened after Google sent the browser back. */
  oauthReturn: OAuthReturn | null;
  /** The dialog opened because this plugin's browser closed when the plugin stopped being usable: says why on top. */
  closedPluginId?: string | null;
  onClose: () => void;
  /** "Browse Gmail": the caller closes this dialog and opens the Gmail browser. */
  onBrowse: (plugin: PluginInfo) => void;
};

/** The user's own connections to plugins (account menu → Connections): connect, status, Use in chat, disconnect. */
export default function ConnectionsModal({ oauthReturn, closedPluginId = null, onClose, onBrowse }: ConnectionsModalProps) {
  const { plugins, error, reload } = usePlugins();
  const [banner, setBanner] = useState<Banner | "oauth" | "closed" | null>(
    oauthReturn ? "oauth" : closedPluginId ? "closed" : null,
  );
  let shown: Banner | null;
  if (banner === "oauth") shown = oauthReturn ? oauthBanner(oauthReturn, plugins) : null;
  else if (banner === "closed") shown = closedPluginId ? closedBanner(closedPluginId, plugins) : null;
  else shown = banner;
  const dismissRef = useRef<HTMLButtonElement>(null);
  /** A plugin just disconnected: if its card is gone from the reloaded list (turned off), focus would be lost. */
  const disconnectedId = useRef<string | null>(null);

  useEffect(() => {
    void reloadPlugins();
  }, []);

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
      <div className="conn-state" role="alert">
        <AlertIcon size={18} />
        <p>{error}</p>
        <button type="button" className="btn btn-sm" onClick={() => void reload()}>
          Retry
        </button>
      </div>
    ) : (
      <div className="conn-state">
        <Spinner size={18} label="Loading connections" />
      </div>
    );
  } else if (plugins.length === 0) {
    content = <p className="conn-empty">No connections are available. The admin hasn't turned any on.</p>;
  } else {
    content = (
      <ul className="conn-list">
        {plugins.map((plugin) => (
          <li key={plugin.id}>
            <PluginConnectionCard
              plugin={plugin}
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
    <Modal open title="Connections" onClose={onClose} width={600}>
      <div className="conn-modal">
        <p className="conn-intro">
          Connect your own accounts so the assistant can use them when you ask. Nobody else can see or use your
          connections.
        </p>
        {shown ? (
          <div className={`conn-banner conn-banner-${shown.tone}`} role={shown.tone === "error" ? "alert" : "status"}>
            {shown.tone === "success" ? <CheckIcon size={16} /> : <AlertIcon size={16} />}
            <span className="conn-banner-text">{shown.text}</span>
            <button
              ref={dismissRef}
              type="button"
              className="btn btn-ghost btn-icon conn-banner-close"
              aria-label="Dismiss message"
              onClick={() => setBanner(null)}
            >
              <CloseIcon size={14} />
            </button>
          </div>
        ) : null}
        {plugins !== null && error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
        {content}
      </div>
    </Modal>
  );
}

type ConnectRequest = { capabilities?: string[] };

type PluginConnectionCardProps = {
  plugin: PluginInfo;
  onBrowse: (plugin: PluginInfo) => void;
  onDisconnected: () => void;
};

function statusBadge(plugin: PluginInfo): ReactNode {
  if (!plugin.enabled) return <span className="badge badge-neutral">Turned off by the admin</span>;
  if (!plugin.connection) return <span className="badge badge-neutral">Not connected</span>;
  return plugin.connection.status === "connected" ? (
    <span className="badge badge-success">Connected</span>
  ) : (
    <span className="badge badge-warning">Reconnect needed</span>
  );
}

function PluginConnectionCard({ plugin, onBrowse, onDisconnected }: PluginConnectionCardProps) {
  const titleId = useId();
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
        : actionsRef.current?.querySelector<HTMLElement>("button:not(:disabled)");
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

  const reconnectButton = (primary: boolean) => (
    <button
      type="button"
      className={primary ? "btn btn-primary btn-sm" : "btn btn-sm"}
      onClick={(event) => requestConnect({}, event.currentTarget)}
      disabled={busy}
    >
      {redirecting ? <Spinner size={12} label="Opening Google" /> : null}
      Reconnect
    </button>
  );

  return (
    <section className="conn-card" aria-labelledby={titleId}>
      <div className="conn-card-head">
        <span className="conn-card-icon" aria-hidden="true">
          <MailIcon size={18} />
        </span>
        <div className="conn-card-heading">
          <h3 id={titleId} className="conn-card-title">
            {name}
          </h3>
          <p className="conn-card-description">{plugin.description}</p>
        </div>
        {statusBadge(plugin)}
      </div>

      {connection ? (
        <dl className="conn-facts">
          <div>
            <dt>Account</dt>
            <dd className="conn-account">{connection.account_email}</dd>
          </div>
          <div>
            <dt>Connected</dt>
            <dd>{formatDateTime(connection.connected_at)}</dd>
          </div>
          {connection.last_used_at ? (
            <div>
              <dt>Last used</dt>
              <dd>{formatRelativeTime(connection.last_used_at)}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {!plugin.enabled ? (
        <p className="conn-note">
          The admin turned {name} off, so the assistant can't use it. You can still disconnect your account.
        </p>
      ) : null}

      {plugin.enabled && reconnectNeeded ? (
        <div className="conn-warning" role="status">
          <AlertIcon size={16} />
          <p>{connection?.status_message ?? `Reconnect to keep using ${name}.`}</p>
          {reconnectButton(true)}
        </div>
      ) : null}
      {plugin.enabled && lastDay ? (
        <div className="conn-warning" role="status">
          <AlertIcon size={16} />
          <p>Google will end this connection within a day. Reconnect now to avoid an interruption.</p>
          {reconnectButton(true)}
        </div>
      ) : null}
      {plugin.enabled && connected && endsAt !== null && !lastDay ? (
        <p className="conn-note">
          Google ends this connection around {formatDateTime(connection?.refresh_expires_at)}; reconnect then.
        </p>
      ) : null}
      {plugin.enabled && !connection && plugin.testing_mode ? (
        <p className="conn-note">While this app is in Google's testing mode, Google ends each connection after 7 days.</p>
      ) : null}

      {plugin.capabilities.length > 0 ? (
        <ul className="conn-caps" aria-label={`What ${name} can be used for`}>
          {plugin.capabilities.map((capability) => {
            const allowed = connected && capability.enabled && capability.granted;
            return (
              <li key={capability.id} className={capability.enabled ? "conn-cap" : "conn-cap conn-cap-off"}>
                <span className="conn-cap-mark" aria-hidden="true">
                  {allowed ? <CheckIcon size={14} /> : <span className="conn-cap-dot" />}
                </span>
                <span className="conn-cap-text">
                  <span className="conn-cap-name">
                    {capability.name}
                    {allowed ? <span className="sr-only"> (allowed)</span> : null}
                  </span>
                  <span className="conn-cap-description">{capability.description}</span>
                </span>
                {!capability.enabled ? (
                  <span className="conn-cap-status">Turned off by the admin</span>
                ) : plugin.enabled && connected && !capability.granted ? (
                  <button
                    type="button"
                    className="btn btn-sm"
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

      {plugin.enabled && connection ? (
        <div className="conn-switch-field">
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
          <div className="conn-switch-text">
            <label htmlFor={chatId} className="conn-switch-label">
              Use in chat
            </label>
            <span id={`${chatId}-hint`} className="field-hint">
              The assistant can use {name} when you ask about your email. Turn it off to keep {name} out of your chats.
            </span>
          </div>
        </div>
      ) : null}

      {asking ? (
        <div className="conn-hint" role="group" aria-labelledby={hintId}>
          <p id={hintId}>{TESTING_HINT}</p>
          <div className="conn-hint-actions">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => void connect(asking)} autoFocus>
              Continue
            </button>
            <button type="button" className="btn btn-sm" onClick={cancelPrompt}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {confirming ? (
        <div className="conn-confirm" role="group" aria-labelledby={confirmId}>
          <p id={confirmId}>
            Disconnect {name}? Drafts and sent emails stay in {name}, and imported documents stay in your documents.
          </p>
          <div className="conn-hint-actions">
            <button type="button" className="btn btn-danger btn-sm" onClick={() => void disconnect()} disabled={disconnecting}>
              {disconnecting ? <Spinner size={12} label="Disconnecting" /> : null}
              Disconnect
            </button>
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
          </div>
        </div>
      ) : null}

      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}

      {!asking && !confirming ? (
        <div ref={actionsRef} className="conn-actions">
          {plugin.enabled && !connection ? (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={(event) => requestConnect({}, event.currentTarget)}
              disabled={busy}
            >
              {redirecting ? <Spinner size={12} label="Opening Google" /> : null}
              Connect {name}
            </button>
          ) : null}
          {plugin.enabled && connected && !lastDay ? reconnectButton(false) : null}
          {canBrowse ? (
            <button type="button" className="btn btn-sm" onClick={() => onBrowse(plugin)} disabled={busy}>
              <MailIcon size={14} />
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
    </section>
  );
}
