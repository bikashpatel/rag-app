import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Preferences } from "../api/types";
import { setMediaQueries } from "../test/browser";
import { makePreferences } from "../test/fixtures";

type Appearance = typeof import("./appearance");

const DARK = "(prefers-color-scheme: dark)";
const root = document.documentElement;

/** A fresh module: what is applied and whether the engine started are module state. */
async function load(): Promise<Appearance> {
  vi.resetModules();
  return import("./appearance");
}

function meta(name: string): string | null {
  return document.querySelector(`meta[name="${name}"]`)?.getAttribute("content") ?? null;
}

beforeEach(() => {
  for (const name of ["data-theme", "data-theme-preference", "data-accent", "data-chat-font", "data-theme-switching"]) {
    root.removeAttribute(name);
  }
  document.head.innerHTML = '<meta name="color-scheme" content="light"><meta name="theme-color" content="#faf8f3">';
});

describe("normalizePreferences", () => {
  it("keeps valid values", async () => {
    const { normalizePreferences } = await load();
    const preferences: Preferences = {
      theme: "dark",
      accent: "ocean",
      chat_font_size: "large",
      enter_to_send: false,
      default_model_id: 5,
    };
    expect(normalizePreferences(preferences)).toEqual(preferences);
  });

  it("replaces each missing or invalid field with its default", async () => {
    const { normalizePreferences, DEFAULT_PREFERENCES } = await load();
    expect(normalizePreferences(null)).toEqual(DEFAULT_PREFERENCES);
    expect(normalizePreferences("dark")).toEqual(DEFAULT_PREFERENCES);
    expect(normalizePreferences({ theme: "sepia", accent: "ocean", enter_to_send: "yes" })).toEqual({
      ...DEFAULT_PREFERENCES,
      accent: "ocean",
    });
  });

  it.each([0, -1, 1.5, 2_147_483_648, "5", null])("drops the default model id %s", async (id) => {
    const { normalizePreferences } = await load();
    expect(normalizePreferences({ default_model_id: id }).default_model_id).toBeNull();
  });
});

describe("samePreferences", () => {
  it("compares every field", async () => {
    const { samePreferences } = await load();
    const base = makePreferences();
    expect(samePreferences(base, { ...base })).toBe(true);
    expect(samePreferences(base, { ...base, default_model_id: 3 })).toBe(false);
    expect(samePreferences(base, { ...base, enter_to_send: false })).toBe(false);
  });
});

describe("the preferences cache", () => {
  it("reads defaults when nothing or something unreadable is cached", async () => {
    const { readCachedPreferences, DEFAULT_PREFERENCES, PREFERENCES_KEY } = await load();
    expect(readCachedPreferences()).toEqual(DEFAULT_PREFERENCES);
    localStorage.setItem(PREFERENCES_KEY, "{not json");
    expect(readCachedPreferences()).toEqual(DEFAULT_PREFERENCES);
  });

  it("round-trips the preferences, writing only when they changed", async () => {
    const { cachePreferences, readCachedPreferences, PREFERENCES_KEY } = await load();
    const preferences = makePreferences({ theme: "dark", accent: "plum", default_model_id: 2 });
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    cachePreferences({ ...preferences, extra: "dropped" } as Preferences);
    cachePreferences(preferences);
    expect(setItem).toHaveBeenCalledTimes(1);
    expect(JSON.parse(localStorage.getItem(PREFERENCES_KEY) ?? "null")).toEqual(preferences);
    expect(readCachedPreferences()).toEqual(preferences);
  });

  it("ignores storage that refuses writes", async () => {
    const { cachePreferences } = await load();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    });
    expect(() => cachePreferences(makePreferences({ theme: "dark" }))).not.toThrow();
  });
});

describe("applyAppearance", () => {
  it("sets the attributes on <html> and both metas", async () => {
    const { applyAppearance, resolvedTheme } = await load();
    applyAppearance(makePreferences({ theme: "dark", accent: "sage", chat_font_size: "small" }));
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(root).toHaveAttribute("data-theme-preference", "dark");
    expect(root).toHaveAttribute("data-accent", "sage");
    expect(root).toHaveAttribute("data-chat-font", "small");
    expect(meta("color-scheme")).toBe("dark");
    expect(meta("theme-color")).toBe("#262421");
    expect(resolvedTheme()).toBe("dark");
  });

  it("resolves System from the OS setting", async () => {
    const { applyAppearance } = await load();
    setMediaQueries((query) => query === DARK);
    applyAppearance(makePreferences({ theme: "system" }));
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(root).toHaveAttribute("data-theme-preference", "system");
  });

  it("turns transitions off for two frames during a switch", async () => {
    const { applyAppearance } = await load();
    applyAppearance(makePreferences({ theme: "light" }));
    expect(root).toHaveAttribute("data-theme-switching");
    await waitFor(() => expect(root).not.toHaveAttribute("data-theme-switching"));
  });

  it("does nothing when what is on screen already matches", async () => {
    root.setAttribute("data-theme", "light");
    root.setAttribute("data-theme-preference", "light");
    root.setAttribute("data-accent", "clay");
    root.setAttribute("data-chat-font", "default");
    const { applyAppearance } = await load();
    applyAppearance(makePreferences({ theme: "light" }));
    expect(root).not.toHaveAttribute("data-theme-switching");
  });
});

describe("useResolvedTheme", () => {
  it("re-renders when the theme on screen changes", async () => {
    const { applyAppearance, useResolvedTheme } = await load();
    applyAppearance(makePreferences({ theme: "light" }));
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
    act(() => applyAppearance(makePreferences({ theme: "dark" })));
    expect(result.current).toBe("dark");
  });
});

describe("startAppearanceEngine", () => {
  it("applies the cached preferences once", async () => {
    const { startAppearanceEngine, PREFERENCES_KEY } = await load();
    localStorage.setItem(PREFERENCES_KEY, JSON.stringify(makePreferences({ theme: "dark", accent: "slate" })));
    startAppearanceEngine(() => {});
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(root).toHaveAttribute("data-accent", "slate");
    root.setAttribute("data-accent", "plum");
    startAppearanceEngine(() => {});
    expect(root).toHaveAttribute("data-accent", "plum");
  });

  it("follows the OS live while the theme is System, and not otherwise", async () => {
    const { startAppearanceEngine, applyAppearance } = await load();
    startAppearanceEngine(() => {});
    expect(root).toHaveAttribute("data-theme", "light");
    setMediaQueries((query) => query === DARK);
    expect(root).toHaveAttribute("data-theme", "dark");

    applyAppearance(makePreferences({ theme: "light" }));
    setMediaQueries(() => false);
    setMediaQueries((query) => query === DARK);
    expect(root).toHaveAttribute("data-theme", "light");
  });

  it("reports another tab's cached preferences", async () => {
    const { startAppearanceEngine, PREFERENCES_KEY } = await load();
    const onExternalChange = vi.fn();
    startAppearanceEngine(onExternalChange);
    const external = makePreferences({ theme: "dark", chat_font_size: "large" });
    localStorage.setItem(PREFERENCES_KEY, JSON.stringify(external));

    window.dispatchEvent(new StorageEvent("storage", { key: "rag.other", storageArea: localStorage }));
    expect(onExternalChange).not.toHaveBeenCalled();
    window.dispatchEvent(new StorageEvent("storage", { key: PREFERENCES_KEY, storageArea: sessionStorage }));
    expect(onExternalChange).not.toHaveBeenCalled();
    window.dispatchEvent(new StorageEvent("storage", { key: PREFERENCES_KEY, storageArea: localStorage }));
    expect(onExternalChange).toHaveBeenCalledWith(external);
  });
});
