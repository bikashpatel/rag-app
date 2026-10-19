import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, type Location } from "react-router-dom";
import type { PluginInfo } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { parseOAuthReturn } from "../connections/ConnectionsPanel";
import { onConnectionsRequest, requestPluginBrowser } from "../connections/pluginStore";
import {
  parseSettingsTab,
  SETTINGS_PARAM,
  settingsNavigation,
  useOpenSettings,
  withSettingsTab,
  type SettingsNavigation,
} from "../../lib/settingsLink";
import type { PluginsIntent } from "./PluginsDataTab";
import SettingsModal from "./SettingsModal";

const NO_INTENT: PluginsIntent = { oauthReturn: null, closedPluginId: null, focusPluginId: null };

/** The chat routes, where the plugin's browser (Gmail import) lives. */
function isChatPath(pathname: string): boolean {
  return pathname === "/" || pathname.startsWith("/c/");
}

/** The location's state with the Settings part replaced (or removed, for undefined); other keys stay. */
function withSettingsState(state: unknown, settings: SettingsNavigation | undefined): Record<string, unknown> | null {
  const rest = { ...(state !== null && typeof state === "object" ? (state as Record<string, unknown>) : {}) };
  delete rest.settings;
  if (settings) rest.settings = settings;
  return Object.keys(rest).length > 0 ? rest : null;
}

function intentOf(settings: SettingsNavigation | null): PluginsIntent | null {
  if (!settings || (!settings.oauthReturn && !settings.closedPluginId && !settings.focusPluginId)) return null;
  return {
    oauthReturn: settings.oauthReturn ?? null,
    closedPluginId: settings.closedPluginId ?? null,
    focusPluginId: settings.focusPluginId ?? null,
  };
}

/** React Router keeps the history index in history.state: is there an entry to go back to? */
function canGoBack(): boolean {
  const idx = (window.history.state as { idx?: unknown } | null)?.idx;
  return typeof idx === "number" && idx > 0;
}

/**
 * Mounted once, next to the routes (docs/briefs/ui-shell-settings.md 5 and 10.1): shows the Settings dialog while the
 * URL has ?settings=<tab> and a user is signed in, turns Google's OAuth return into Settings → Plugins & data, opens
 * that tab for requestConnections(), and opens the plugin's browser only after the dialog has unmounted (U22).
 */
export default function SettingsHost() {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const openSettings = useOpenSettings();
  const locationRef = useRef<Location>(location);
  locationRef.current = location;

  const tab = parseSettingsTab(new URLSearchParams(location.search).get(SETTINGS_PARAM));
  const open = user !== null && tab !== null;

  /** Banner and focus target for Plugins & data, moved out of the history entry so a reload doesn't repeat them. */
  const [intent, setIntent] = useState<{ value: PluginsIntent; key: string }>({ value: NO_INTENT, key: "" });
  const [pendingBrowse, setPendingBrowse] = useState<string | null>(null);
  /** Set after "Delete all chats": closing then replaces the URL instead of going back (U16, 10.5). */
  const replaceOnClose = useRef(false);
  const handledOAuth = useRef<string | null>(null);

  // Back from Google (/?plugin=<id>&oauth=<result>): Settings → Plugins & data with the outcome, once.
  useEffect(() => {
    if (user === null) return;
    const params = new URLSearchParams(location.search);
    if (!params.has("plugin") && !params.has("oauth")) return;
    if (handledOAuth.current === location.key) return;
    handledOAuth.current = location.key;
    const oauthReturn = parseOAuthReturn(location.search);
    params.delete("plugin");
    params.delete("oauth");
    const search = params.toString() ? `?${params.toString()}` : "";
    navigate(
      { pathname: location.pathname, search: oauthReturn ? withSettingsTab(search, "plugins") : search, hash: location.hash },
      { replace: true, state: oauthReturn ? { settings: { oauthReturn } } : null },
    );
  }, [user, location, navigate]);

  // The one-off parts of the navigation state (banner, card to focus) are used from this render on, then moved out of
  // the history entry (a reload or Back and Forward would show them again) into this component's state.
  const fromNavigation = open ? intentOf(settingsNavigation(location.state)) : null;
  const shownIntent = fromNavigation ? { value: fromNavigation, key: location.key } : intent;
  useEffect(() => {
    if (!open) return;
    const settings = settingsNavigation(location.state);
    const value = intentOf(settings);
    if (!value) return;
    setIntent({ value, key: location.key });
    navigate(
      { pathname: location.pathname, search: location.search, hash: location.hash },
      { replace: true, state: withSettingsState(location.state, settings?.pushed ? { pushed: true } : {}) },
    );
  }, [open, location, navigate]);

  // The intent belongs to one visit of Plugins & data.
  useEffect(() => {
    if (tab !== "plugins" || !open) setIntent((current) => (current.key ? { value: NO_INTENT, key: "" } : current));
    if (!open) replaceOnClose.current = false;
  }, [tab, open]);

  // The documents panel and the Gmail browser ask for Connections (now Settings → Plugins & data).
  useEffect(
    () => onConnectionsRequest((closedPluginId) => openSettings("plugins", closedPluginId ? { closedPluginId } : {})),
    [openSettings],
  );

  // "Browse Gmail" from Settings: ask the chat page only once the dialog is gone (its focus is back on the opener).
  useEffect(() => {
    if (user === null) {
      setPendingBrowse(null);
      return;
    }
    if (open || pendingBrowse === null) return;
    requestPluginBrowser(pendingBrowse);
    setPendingBrowse(null);
  }, [open, pendingBrowse, user]);

  const close = useCallback(() => {
    const current = locationRef.current;
    if (settingsNavigation(current.state)?.pushed && !replaceOnClose.current && canGoBack()) {
      navigate(-1);
      return;
    }
    navigate(
      { pathname: current.pathname, search: withSettingsTab(current.search, null), hash: current.hash },
      { replace: true, state: withSettingsState(current.state, undefined) },
    );
  }, [navigate]);

  const browse = useCallback(
    (plugin: PluginInfo) => {
      setPendingBrowse(plugin.id);
      const current = locationRef.current;
      if (isChatPath(current.pathname)) close();
      else navigate("/", { replace: true });
    },
    [close, navigate],
  );

  const allChatsDeleted = useCallback(() => {
    replaceOnClose.current = true;
  }, []);

  if (!open || user === null || tab === null) return null;
  return (
    <SettingsModal
      user={user}
      tab={tab}
      onSelectTab={openSettings}
      onClose={close}
      pluginsIntent={shownIntent.value}
      pluginsIntentKey={shownIntent.key}
      onBrowse={browse}
      onAllChatsDeleted={allChatsDeleted}
    />
  );
}
