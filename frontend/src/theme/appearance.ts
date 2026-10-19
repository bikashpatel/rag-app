// The theme engine (docs/briefs/ui-shell-settings.md section 6). public/theme-boot.js applies the cached appearance
// before the first paint; this module re-applies it, follows the OS while the theme is System, and reports other
// tabs' changes. CSS never reads prefers-color-scheme: the resolved theme is the data-theme attribute set here.
// Keep the allowed values in sync with public/theme-boot.js and SPEC.md section 6 (Preferences).

import { useSyncExternalStore } from "react";
import type { AccentColor, ChatFontSize, Preferences, ThemePreference } from "../api/types";

export const PREFERENCES_KEY = "rag.preferences";

export const DEFAULT_PREFERENCES: Preferences = {
  theme: "system",
  accent: "clay",
  chat_font_size: "default",
  enter_to_send: true,
  default_model_id: null,
};

export const THEMES: readonly { id: ThemePreference; label: string }[] = [
  { id: "system", label: "System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

export const ACCENTS: readonly { id: AccentColor; label: string }[] = [
  { id: "clay", label: "Clay" },
  { id: "ocean", label: "Ocean" },
  { id: "sage", label: "Sage" },
  { id: "plum", label: "Plum" },
  { id: "slate", label: "Slate" },
];

export const CHAT_FONT_SIZES: readonly { id: ChatFontSize; label: string }[] = [
  { id: "small", label: "Small" },
  { id: "default", label: "Default" },
  { id: "large", label: "Large" },
];

export type ResolvedTheme = "light" | "dark";

/** --bg of each theme in global.css, for <meta name="theme-color">. */
const THEME_COLOR: Record<ResolvedTheme, string> = { light: "#faf8f3", dark: "#262421" };
const DARK_QUERY = "(prefers-color-scheme: dark)";
const MAX_ID = 2_147_483_647;

function pick<T extends string>(value: unknown, allowed: readonly { id: T }[], fallback: T): T {
  return allowed.some((option) => option.id === value) ? (value as T) : fallback;
}

/** Validated field by field: a missing or invalid value is its default (as the server reads stored preferences). */
export function normalizePreferences(value: unknown): Preferences {
  const raw = value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const modelId = raw.default_model_id;
  return {
    theme: pick(raw.theme, THEMES, DEFAULT_PREFERENCES.theme),
    accent: pick(raw.accent, ACCENTS, DEFAULT_PREFERENCES.accent),
    chat_font_size: pick(raw.chat_font_size, CHAT_FONT_SIZES, DEFAULT_PREFERENCES.chat_font_size),
    enter_to_send: typeof raw.enter_to_send === "boolean" ? raw.enter_to_send : DEFAULT_PREFERENCES.enter_to_send,
    default_model_id:
      typeof modelId === "number" && Number.isInteger(modelId) && modelId >= 1 && modelId <= MAX_ID ? modelId : null,
  };
}

export function samePreferences(a: Preferences, b: Preferences): boolean {
  return (
    a.theme === b.theme &&
    a.accent === b.accent &&
    a.chat_font_size === b.chat_font_size &&
    a.enter_to_send === b.enter_to_send &&
    a.default_model_id === b.default_model_id
  );
}

function serialize(preferences: Preferences): string {
  const { theme, accent, chat_font_size, enter_to_send, default_model_id } = preferences;
  return JSON.stringify({ theme, accent, chat_font_size, enter_to_send, default_model_id });
}

export function readCachedPreferences(): Preferences {
  try {
    return normalizePreferences(JSON.parse(localStorage.getItem(PREFERENCES_KEY) ?? "null"));
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}

/** Writes only when the value differs from what is stored; private mode and full quotas are fine. */
export function cachePreferences(preferences: Preferences): void {
  try {
    const value = serialize(preferences);
    if (localStorage.getItem(PREFERENCES_KEY) !== value) localStorage.setItem(PREFERENCES_KEY, value);
  } catch {
    /* storage unavailable: the cache is only for the first paint */
  }
}

function systemTheme(): ResolvedTheme {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

function resolve(theme: ThemePreference): ResolvedTheme {
  return theme === "system" ? systemTheme() : theme;
}

type Applied = { theme: ResolvedTheme; preference: ThemePreference; accent: AccentColor; font: ChatFontSize };

/** What is on <html> now: set by theme-boot.js at first, then only by applyAppearance. */
let applied: Applied | null = null;
let lastPreferences: Preferences = DEFAULT_PREFERENCES;
let switchingFrame = 0;
const themeListeners = new Set<() => void>();

function readApplied(): Applied {
  const root = document.documentElement;
  return {
    theme: root.getAttribute("data-theme") === "dark" ? "dark" : "light",
    preference: pick(root.getAttribute("data-theme-preference"), THEMES, DEFAULT_PREFERENCES.theme),
    accent: pick(root.getAttribute("data-accent"), ACCENTS, DEFAULT_PREFERENCES.accent),
    font: pick(root.getAttribute("data-chat-font"), CHAT_FONT_SIZES, DEFAULT_PREFERENCES.chat_font_size),
  };
}

function setMeta(name: string, content: string): void {
  const meta = document.querySelector(`meta[name="${name}"]`);
  if (meta && meta.getAttribute("content") !== content) meta.setAttribute("content", content);
}

/** Turns transitions off (global.css) for two frames, so a theme swap doesn't animate every border and fill. */
function markSwitching(): void {
  const root = document.documentElement;
  root.setAttribute("data-theme-switching", "");
  cancelAnimationFrame(switchingFrame);
  switchingFrame = requestAnimationFrame(() => {
    switchingFrame = requestAnimationFrame(() => root.removeAttribute("data-theme-switching"));
  });
}

/** Sets the four attributes on <html> and both metas; a no-op when the resolved theme, preference, accent and font are unchanged. */
export function applyAppearance(preferences: Preferences): void {
  lastPreferences = preferences;
  const next: Applied = {
    theme: resolve(preferences.theme),
    preference: preferences.theme,
    accent: preferences.accent,
    font: preferences.chat_font_size,
  };
  const previous = applied ?? readApplied();
  applied = next;
  if (
    previous.theme === next.theme &&
    previous.preference === next.preference &&
    previous.accent === next.accent &&
    previous.font === next.font &&
    document.documentElement.hasAttribute("data-theme")
  ) {
    return;
  }
  markSwitching();
  const root = document.documentElement;
  root.setAttribute("data-theme", next.theme);
  root.setAttribute("data-theme-preference", next.preference);
  root.setAttribute("data-accent", next.accent);
  root.setAttribute("data-chat-font", next.font);
  setMeta("color-scheme", next.theme);
  setMeta("theme-color", THEME_COLOR[next.theme]);
  if (previous.theme !== next.theme) themeListeners.forEach((listener) => listener());
}

/** The theme on screen now ("light" or "dark"), System resolved. */
export function resolvedTheme(): ResolvedTheme {
  return (applied ?? readApplied()).theme;
}

function subscribeTheme(listener: () => void): () => void {
  themeListeners.add(listener);
  return () => themeListeners.delete(listener);
}

/** resolvedTheme() for components: re-renders when the theme on screen changes (System following the OS included). */
export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribeTheme, resolvedTheme);
}

let started = false;

/**
 * Applies the cached preferences (theme-boot.js normally did already: then nothing changes), starts the System
 * listener and the storage listener. `onExternalChange` gets another tab's cached preferences; it applies nothing
 * itself (AuthContext merges them with this tab's pending changes first).
 */
export function startAppearanceEngine(onExternalChange: (preferences: Preferences) => void): void {
  if (started) return;
  started = true;
  applyAppearance(readCachedPreferences());
  if (typeof window.matchMedia === "function") {
    window.matchMedia(DARK_QUERY).addEventListener("change", () => {
      if (lastPreferences.theme === "system") applyAppearance(lastPreferences);
    });
  }
  window.addEventListener("storage", (event) => {
    if (event.key !== PREFERENCES_KEY && event.key !== null) return;
    try {
      if (event.storageArea !== window.localStorage) return;
    } catch {
      return;
    }
    onExternalChange(readCachedPreferences());
  });
}
