/* Shared compare cart (topbar badge + per-page add buttons). */
(function () {
  "use strict";
  const KEY = "mfd_compare_cart";
  const MAX = 4;

  function read() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(v) ? v.map(Number).filter(Number.isFinite).slice(0, MAX) : [];
    } catch (e) { return []; }
  }
  function write(codes) {
    try { localStorage.setItem(KEY, JSON.stringify(codes.slice(0, MAX))); } catch (e) { /* private mode */ }
    updateBadge();
  }
  function updateBadge() {
    const el = document.getElementById("compare-count");
    if (!el) return;
    const n = read().length;
    el.textContent = n > 0 ? n : "";
    el.style.display = n > 0 ? "inline-block" : "none";
  }

  window.MFDCompare = {
    max: MAX,
    get: read,
    has: (code) => read().includes(Number(code)),
    // Returns false when the cart is full and the fund is not already in it.
    add: (code) => {
      const codes = read();
      if (codes.includes(Number(code))) return true;
      if (codes.length >= MAX) return false;
      codes.push(Number(code));
      write(codes);
      return true;
    },
    remove: (code) => write(read().filter((c) => c !== Number(code))),
    set: (codes) => write(codes.map(Number)),
  };
  updateBadge();
})();