// Settings is a dialog over any signed-in route, addressed by ?settings=<tab> (docs/briefs/ui-shell-settings.md
// section 5). These helpers let every area open it, and keep it open while the chat page replaces its own URL.

import { useCallback, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { OAuthReturn } from "../components/connections/ConnectionsPanel";

export type SettingsTab = "general" | "account" | "usage" | "plugins";

export const SETTINGS_TABS: readonly { id: SettingsTab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "account", label: "Account" },
  { id: "usage", label: "Usage" },
  { id: "plugins", label: "Plugins & data" },
];

/** The query parameter that opens the dialog. */
export const SETTINGS_PARAM = "settings";

/** null when absent; "data" is the Plugins & data tab; anything else is General. */
export function parseSettingsTab(value: string | null): SettingsTab | null {
  if (value === null) return null;
  if (value === "data") return "plugins";
  return SETTINGS_TABS.find((tab) => tab.id === value)?.id ?? "general";
}

/**
 * `location.state.settings`. `pushed`: the dialog was opened by a push (closing goes back); without it (a deep link, a
 * reload, the OAuth return, or a path that changed underneath) closing replaces the URL. Banners and focus targets
 * travel here, never in the URL.
 */
export type SettingsNavigation = {
  pushed?: true;
  focusPluginId?: string;
  closedPluginId?: string;
  oauthReturn?: OAuthReturn;
};

/** The Settings part of a location's state; null when there is none. */
export function settingsNavigation(state: unknown): SettingsNavigation | null {
  const settings = (state as { settings?: unknown } | null)?.settings;
  return settings !== null && typeof settings === "object" ? (settings as SettingsNavigation) : null;
}

/** `search` with the Settings tab set (or removed, for null); other parameters stay. */
export function withSettingsTab(search: string, tab: SettingsTab | null): string {
  const params = new URLSearchParams(search);
  if (tab === null) params.delete(SETTINGS_PARAM);
  else params.set(SETTINGS_PARAM, tab);
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** The current location, readable by callbacks that outlive the render that created them. */
function useLocationRef() {
  const location = useLocation();
  const ref = useRef(location);
  ref.current = location;
  return ref;
}

/**
 * Opens Settings on `tab` over the current page (path and hash kept) with a pushed history entry, so Back closes it.
 * When it is already open, it switches the tab instead (a replace; how it was opened is kept).
 */
export function useOpenSettings(): (tab?: SettingsTab, options?: Omit<SettingsNavigation, "pushed">) => void {
  const navigate = useNavigate();
  const locationRef = useLocationRef();
  return useCallback(
    (tab: SettingsTab = "general", options: Omit<SettingsNavigation, "pushed"> = {}) => {
      const current = locationRef.current;
      const alreadyOpen = new URLSearchParams(current.search).has(SETTINGS_PARAM);
      const pushed = alreadyOpen ? settingsNavigation(current.state)?.pushed : true;
      const settings: SettingsNavigation = pushed ? { pushed, ...options } : { ...options };
      navigate(
        { pathname: current.pathname, search: withSettingsTab(current.search, tab), hash: current.hash },
        { replace: alreadyOpen, state: { settings } },
      );
    },
    [navigate, locationRef],
  );
}

/**
 * The chat page's replace navigations (U26): `(path) => void` keeping `?settings=<tab>` (no other parameter of the
 * current URL) and the Settings navigation state, so an open dialog stays open, and dropping `pushed` when the path
 * changes (closing then replaces instead of going back to the old path). Reads the location at call time.
 */
export function useReplacePath(): (path: string) => void {
  const navigate = useNavigate();
  const locationRef = useLocationRef();
  return useCallback(
    (path: string) => {
      const current = locationRef.current;
      const target = new URL(path, window.location.origin);
      const tab = parseSettingsTab(new URLSearchParams(current.search).get(SETTINGS_PARAM));
      const previous = settingsNavigation(current.state);
      let state: { settings: SettingsNavigation } | undefined;
      if (tab !== null && previous) {
        const settings = { ...previous };
        if (target.pathname !== current.pathname) delete settings.pushed;
        state = { settings };
      }
      navigate(
        { pathname: target.pathname, search: withSettingsTab(target.search, tab), hash: target.hash },
        { replace: true, state },
      );
    },
    [navigate, locationRef],
  );
}
