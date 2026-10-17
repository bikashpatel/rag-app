import { useEffect, useSyncExternalStore } from "react";
import { ApiError, listPlugins, onPluginProblem } from "../../api/client";
import type { PluginConnectionStatus, PluginInfo } from "../../api/types";

// A module store like documentEvents.ts: the chat page, the account menu, the documents panel and the dialogs share
// the caller's plugin list (GET /plugins) without new props on DocumentPanel.

type PluginState = { plugins: PluginInfo[] | null; error: string | null };

/** Focus and visibility reloads: at most one per this interval (brief 15.1, P26). */
const FOCUS_RELOAD_MS = 30_000;

const listeners = new Set<() => void>();
let state: PluginState = { plugins: null, error: null };
let latestRequest = 0;
let lastLoadStarted = 0;
let controller: AbortController | null = null;

function setState(next: PluginState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Loads GET /plugins; a response to a superseded request is dropped. */
export async function reloadPlugins(): Promise<void> {
  const request = ++latestRequest;
  lastLoadStarted = Date.now();
  controller?.abort();
  controller = new AbortController();
  try {
    const plugins = await listPlugins(controller.signal);
    if (request === latestRequest) setState({ plugins, error: null });
  } catch (err) {
    if (request !== latestRequest || (err instanceof DOMException && err.name === "AbortError")) return;
    setState({ ...state, error: err instanceof ApiError ? err.message : "Couldn't load your connections." });
  }
}

/** Forgets the list (another user may sign in next), cancelling a request in flight. */
function clearPlugins(): void {
  latestRequest += 1;
  controller?.abort();
  controller = null;
  setState({ plugins: null, error: null });
}

/** Applies a connection the server returned (PATCH …/connection) without waiting for the next reload. */
export function setPluginConnection(pluginId: string, connection: PluginConnectionStatus): void {
  if (!state.plugins) return;
  setState({
    ...state,
    plugins: state.plugins.map((plugin) => (plugin.id === pluginId ? { ...plugin, connection } : plugin)),
  });
}

function reloadIfStale(): void {
  if (Date.now() - lastLoadStarted >= FOCUS_RELOAD_MS) void reloadPlugins();
}

onPluginProblem(() => void reloadPlugins());

export function usePlugins(): PluginState & { reload: () => Promise<void> } {
  const current = useSyncExternalStore(subscribe, () => state);
  return { ...current, reload: reloadPlugins };
}

/**
 * Used once by the chat page: loads the list on mount, reloads it when the window regains focus or the tab becomes
 * visible (at most every 30 s), so a plugin the admin turned off disappears without a page reload, and clears it on
 * unmount.
 */
export function usePluginLifecycle(): void {
  useEffect(() => {
    void reloadPlugins();
    const onVisibility = () => {
      if (document.visibilityState === "visible") reloadIfStale();
    };
    window.addEventListener("focus", reloadIfStale);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", reloadIfStale);
      document.removeEventListener("visibilitychange", onVisibility);
      clearPlugins();
    };
  }, []);
}

/** The capability is turned on by the admin and granted by the caller's connection. */
export function capabilityUsable(plugin: PluginInfo, capabilityId: string): boolean {
  return plugin.capabilities.some((capability) => capability.id === capabilityId && capability.enabled && capability.granted);
}

/** The Gmail plugin when it is enabled, connected and can read mail (browsing and importing work); else null. */
export function gmailReady(plugins: PluginInfo[] | null): PluginInfo | null {
  const gmail = plugins?.find((plugin) => plugin.id === "gmail");
  if (!gmail || !gmail.enabled || gmail.connection?.status !== "connected") return null;
  return capabilityUsable(gmail, "read") ? gmail : null;
}

/**
 * Why `capabilityId` of `plugin` can't be used now, for a message ("… needs to be reconnected."); null when it can.
 * `plugin` undefined: no longer in the caller's list (turned off and not connected).
 */
export function unusableReason(plugin: PluginInfo | undefined, capabilityId: string, name: string): string | null {
  if (!plugin || !plugin.enabled) return `The admin turned ${name} off.`;
  if (!plugin.connection) return `${name} is no longer connected.`;
  if (plugin.connection.status !== "connected") return `${name} needs to be reconnected.`;
  const capability = plugin.capabilities.find((item) => item.id === capabilityId);
  if (!capability?.enabled) return `The admin turned off “${capability?.name ?? capabilityId}” for ${name}.`;
  if (!capability.granted) return `Your connection no longer allows “${capability.name}”. Grant access again below.`;
  return null;
}

// Connections opened from outside the chat page (the documents panel's Gmail browser), without new DocumentPanel props.
type ConnectionsListener = (closedPluginId: string | null) => void;
const connectionsListeners = new Set<ConnectionsListener>();

/** Asks the chat page to open Connections; `closedPluginId`: that plugin's browser just closed as it became unusable. */
export function requestConnections(closedPluginId: string | null = null): void {
  connectionsListeners.forEach((listener) => listener(closedPluginId));
}

export function onConnectionsRequest(listener: ConnectionsListener): () => void {
  connectionsListeners.add(listener);
  return () => connectionsListeners.delete(listener);
}
