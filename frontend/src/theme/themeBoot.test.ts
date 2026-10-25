import { beforeEach, describe, expect, it } from "vitest";
import source from "../../public/theme-boot.js?raw";
import { setMediaQueries } from "../test/browser";
import { PREFERENCES_KEY } from "./appearance";

const root = document.documentElement;

function boot(cached?: unknown): void {
  if (cached !== undefined) {
    localStorage.setItem(PREFERENCES_KEY, typeof cached === "string" ? cached : JSON.stringify(cached));
  }
  new Function(source)();
}

function applied() {
  return {
    theme: root.getAttribute("data-theme"),
    preference: root.getAttribute("data-theme-preference"),
    accent: root.getAttribute("data-accent"),
    font: root.getAttribute("data-chat-font"),
    colorScheme: document.querySelector('meta[name="color-scheme"]')?.getAttribute("content"),
    themeColor: document.querySelector('meta[name="theme-color"]')?.getAttribute("content"),
  };
}

beforeEach(() => {
  for (const name of ["data-theme", "data-theme-preference", "data-accent", "data-chat-font"]) root.removeAttribute(name);
  document.head.innerHTML = '<meta name="color-scheme" content="light"><meta name="theme-color" content="#faf8f3">';
});

describe("public/theme-boot.js", () => {
  it("applies the defaults without a cache", () => {
    boot();
    expect(applied()).toEqual({
      theme: "light",
      preference: "system",
      accent: "clay",
      font: "default",
      colorScheme: "light",
      themeColor: "#faf8f3",
    });
  });

  it("applies the cached appearance", () => {
    boot({ theme: "dark", accent: "ocean", chat_font_size: "large", enter_to_send: true, default_model_id: null });
    expect(applied()).toEqual({
      theme: "dark",
      preference: "dark",
      accent: "ocean",
      font: "large",
      colorScheme: "dark",
      themeColor: "#262421",
    });
  });

  it("resolves System from the OS setting", () => {
    setMediaQueries((query) => query === "(prefers-color-scheme: dark)");
    boot({ theme: "system" });
    expect(applied()).toMatchObject({ theme: "dark", preference: "system", themeColor: "#262421" });
  });

  it.each([
    ["malformed JSON", "{oops"],
    ["a non-object", "42"],
    ["unknown values", { theme: "sepia", accent: "neon", chat_font_size: "huge" }],
  ])("falls back to the defaults for %s", (_, cached) => {
    boot(cached);
    expect(applied()).toMatchObject({ theme: "light", preference: "system", accent: "clay", font: "default" });
  });

  it("keeps the allowed values in sync with the theme engine", async () => {
    const { THEMES, ACCENTS, CHAT_FONT_SIZES } = await import("./appearance");
    for (const { id } of THEMES) expect(source).toContain(`"${id}"`);
    for (const { id } of ACCENTS) expect(source).toContain(`"${id}"`);
    for (const { id } of CHAT_FONT_SIZES) expect(source).toContain(`"${id}"`);
  });
});
