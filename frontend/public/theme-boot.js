// Applies the cached appearance before the first paint (index.html loads it blocking, after the color-scheme and
// theme-color metas). Keep the allowed values in sync with src/theme/appearance.ts and SPEC.md section 6
// (Preferences), and the two colours with --bg in src/styles/global.css.
(function () {
  var root = document.documentElement, p = null;
  try { p = JSON.parse(localStorage.getItem("rag.preferences") || "null"); } catch (e) { p = null; }
  function pick(value, allowed, fallback) { return allowed.indexOf(value) >= 0 ? value : fallback; }
  function meta(name, content) {
    var el = document.querySelector('meta[name="' + name + '"]');
    if (el) el.setAttribute("content", content);
  }
  p = p && typeof p === "object" ? p : {};
  var theme = pick(p.theme, ["system", "light", "dark"], "system");
  var dark = theme === "dark" ||
    (theme === "system" && !!window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.setAttribute("data-theme", dark ? "dark" : "light");
  root.setAttribute("data-theme-preference", theme);
  root.setAttribute("data-accent", pick(p.accent, ["clay", "ocean", "sage", "plum", "slate"], "clay"));
  root.setAttribute("data-chat-font", pick(p.chat_font_size, ["small", "default", "large"], "default"));
  meta("color-scheme", dark ? "dark" : "light");
  meta("theme-color", dark ? "#262421" : "#faf8f3");
})();
