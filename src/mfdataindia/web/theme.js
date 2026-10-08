/* Shared theme toggle for every page.
 *
 * The actual theme is applied BEFORE first paint by an inline snippet in each
 * page's <head> (localStorage, falling back to the OS preference) so there is
 * no flash. This file only wires the #theme-toggle button: it flips the
 * data-theme on <html>, persists the choice, and lets chart-heavy pages redraw
 * (they register window.__mfdThemeChanged, since canvas charts bake in colors
 * at build time).
 */
(function () {
  const KEY = "mfd-theme";
  const root = document.documentElement;

  function current() {
    return root.dataset.theme ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }

  function paint() {
    const t = current();
    root.dataset.theme = t;
    document.querySelectorAll("#theme-toggle").forEach((b) => {
      b.textContent = t === "dark" ? "☀️" : "🌙";
      b.title = "Switch to " + (t === "dark" ? "light" : "dark") + " mode";
    });
  }

  paint();
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("#theme-toggle");
    if (!btn) return;
    const next = current() === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch (_) { /* private mode */ }
    paint();
    if (typeof window.__mfdThemeChanged === "function") window.__mfdThemeChanged(next);
  });
})();