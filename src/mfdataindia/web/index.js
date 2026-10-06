(function () {
  const $ = (id) => document.getElementById(id);
  const state = { q: "", amc: "", category: "", option: "", sort: "name", page: 1, per_page: 50, family: false };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmt(v, digits) {
    if (v == null) return "—";
    return Number(v).toLocaleString("en-IN", { maximumFractionDigits: digits == null ? 4 : digits });
  }
  // Days since the NAV date (date-only strings), clamped to >= 0 for future-dated rows.
  function navAgeDays(dateStr) {
    if (!dateStr) return null;
    const t = new Date();
    const today = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    const d = new Date(dateStr.slice(0, 10) + "T00:00:00");
    return Math.max(0, Math.round((today - d) / 86400000));
  }
  function ageCell(dateStr) {
    if (!dateStr) return "—";
    const age = navAgeDays(dateStr);
    const cls = age == null ? "" : age <= 7 ? "fresh" : age <= 30 ? "aging" : "stale";
    return `<span class="navage ${cls}" title="${age} days ago">${dateStr}</span>`;
  }
  function badgeFor(f) {
    const parts = [];
    parts.push(`<span class="badge ${f.plan_type === "REGULAR" ? "regular" : f.plan_type === "DIRECT" ? "direct" : ""}">${esc(f.plan_type)}</span>`);
    if (f.option_type && f.option_type !== "UNKNOWN")
      parts.push(`<span class="badge ${f.option_type === "IDCW" ? "idcw" : ""}">${esc(f.option_type)}</span>`);
    if (f.is_defunct) parts.push('<span class="badge defunct">defunct</span>');
    return parts.join(" ");
  }

  async function api(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  // ---- shareable URL state -------------------------------------------------
  // Filters are mirrored to query params so a filtered view can be bookmarked
  // or shared. replaceState keeps the browser history clean.
  const SORT_KEYS = ["name", "nav", "aum", "expense", "return_5y"];
  function applyUrlState() {
    const p = new URLSearchParams(location.search);
    state.q = (p.get("q") || "").slice(0, 200);
    state.amc = p.get("amc") || "";
    state.category = p.get("cat") || "";
    state.option = p.get("opt") || "";
    if (SORT_KEYS.includes(p.get("sort"))) state.sort = p.get("sort");
    const page = parseInt(p.get("page"), 10);
    if (page >= 1) state.page = page;
    state.family = p.get("family") === "1";
  }
  function syncUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.amc) p.set("amc", state.amc);
    if (state.category) p.set("cat", state.category);
    if (state.option) p.set("opt", state.option);
    if (state.sort !== "name") p.set("sort", state.sort);
    if (state.page > 1) p.set("page", state.page);
    if (state.family) p.set("family", "1");
    const qs = p.toString();
    history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  }

  async function loadStats() {
    try {
      const s = await api("/api/stats");
      $("statbar").innerHTML =
        `<span><b>${s.in_scope_live.toLocaleString()}</b> live Regular-Plan schemes</span>` +
        `<span><b>${s.amcs}</b> fund houses</span>` +
        `<span><b>${(s.nav_rows || 0).toLocaleString()}</b> NAV points</span>` +
        `<span>NAV to <b>${s.nav_last || "—"}</b></span>`;
    } catch (e) { $("statbar").innerHTML = `<span class="error">${esc(e.message)}</span>`; }
  }

  async function loadFilters() {
    const [amcs, cats, opts] = await Promise.all([
      api("/api/amcs"), api("/api/categories"), api("/api/options")]);
    $("f-amc").innerHTML = '<option value="">All fund houses</option>' +
      amcs.map((a) => `<option value="${esc(a.amfi_amc_name)}">${esc(a.amfi_amc_name)} (${a.live_funds})</option>`).join("");
    $("f-cat").innerHTML = '<option value="">All categories</option>' +
      cats.map((c) => `<option value="${esc(c.scheme_category)}">${esc(c.scheme_category)} (${c.live_funds})</option>`).join("");
    $("f-opt").innerHTML = '<option value="">All options</option>' +
      opts.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
    // Reflect URL-restored state into the controls (a value with no matching
    // option is left at "" — the list query still honours state).
    if (state.amc) $("f-amc").value = state.amc;
    if (state.category) $("f-cat").value = state.category;
    if (state.option) $("f-opt").value = state.option;
    if (state.sort !== "name") $("f-sort").value = state.sort;
    if (state.family) $("f-family").checked = true;
    if (state.q) $("q").value = state.q;
  }

  async function loadList() {
    syncUrl();
    $("list").innerHTML = '<div class="loading">Loading funds…</div>';
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.amc) p.set("amc", state.amc);
    if (state.category) p.set("category", state.category);
    if (state.option) p.set("option", state.option);
    p.set("sort", state.sort); p.set("page", state.page); p.set("per_page", state.per_page);
    const endpoint = state.family ? "/api/fund-families" : "/api/funds";
    try {
      const data = await api(endpoint + "?" + p.toString());
      renderList(data);
      renderPager(data);
    } catch (e) { $("list").innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  }

  function renderList(data) {
    if (!data.results.length) { $("list").innerHTML = '<div class="empty">No funds match.</div>'; return; }
    const rows = data.results.map((f) => `
      <tr data-code="${f.amfi_scheme_code}">
        <td>
          <div class="fund-name">${esc(f.scheme_name)}${f.variant_count > 1 ? `<span class="vcount">+${f.variant_count - 1} variants</span>` : ""}</div>
          <div class="fund-sub">${esc(f.amfi_amc_name)} · ${esc(f.scheme_category)}</div>
        </td>
        <td>${badgeFor(f)}</td>
        <td class="nav">${f.latest_nav == null ? "—" : "₹" + fmt(f.latest_nav)}</td>
        <td class="num">${ageCell(f.latest_nav_date)}</td>
        <td class="num">${f.return_5year == null ? "—" : fmt(f.return_5year, 1) + "%"}</td>
        <td class="num">${f.expense_ratio == null ? "—" : fmt(f.expense_ratio, 2) + "%"}</td>
        <td class="num">${f.aum == null ? "—" : "₹" + Math.round(f.aum).toLocaleString("en-IN") + " cr"}</td>
      </tr>`).join("");
    $("list").innerHTML = `
      <table class="fundlist">
        <thead><tr><th>Fund</th><th>Plan / Option</th><th class="nav">Latest NAV</th><th class="num">As of</th><th class="num">5Y</th><th class="num">ER</th><th class="num">AUM</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    document.querySelectorAll("#list tbody tr").forEach((tr) =>
      tr.addEventListener("click", () => (location.href = "/fund/" + tr.dataset.code)));
  }

  function renderPager(data) {
    const pages = Math.max(1, Math.ceil(data.total / data.per_page));
    $("pager").innerHTML = `
      <button id="pg-prev" ${data.page <= 1 ? "disabled" : ""}>← Prev</button>
      <span>Page ${data.page} of ${pages} · ${data.total.toLocaleString()} funds</span>
      <button id="pg-next" ${data.page >= pages ? "disabled" : ""}>Next →</button>`;
    $("pg-prev").onclick = () => { if (state.page > 1) { state.page--; loadList(); } };
    $("pg-next").onclick = () => { if (state.page < pages) { state.page++; loadList(); } };
  }

  // ---- autocomplete ----
  let sugTimer, sugItems = [], sugActive = -1;
  const qInput = $("q"), sugBox = $("suggest-box");

  function hideSuggest() { sugBox.style.display = "none"; sugItems = []; sugActive = -1; }

  function hl(text, q) {
    const i = text.toLowerCase().indexOf((q || "").toLowerCase());
    if (i < 0 || !q) return text;
    return text.slice(0, i) + "<b>" + text.slice(i, i + q.length) + "</b>" + text.slice(i + q.length);
  }

  function goSuggest(code) { hideSuggest(); location.href = "/fund/" + code; }

  function paintActive() {
    sugBox.querySelectorAll(".suggest-item").forEach((el, i) =>
      el.classList.toggle("active", i === sugActive));
  }

  async function loadSuggest() {
    const q = qInput.value.trim();
    if (!q) { hideSuggest(); return; }
    try {
      const items = await api("/api/suggest?q=" + encodeURIComponent(q) + "&limit=10");
      if (!items.length) { hideSuggest(); return; }
      sugItems = items; sugActive = -1;
      sugBox.innerHTML = items.map((it, i) => `
        <div class="suggest-item" data-i="${i}" data-code="${it.amfi_scheme_code}">
          <div>
            <div class="nm">${hl(esc(it.scheme_name), q)}</div>
            <div class="mt">${esc(it.amfi_amc_name)} · ${esc(it.option_type)} · ${it.amfi_scheme_code}</div>
          </div>
          <span class="badge ${it.plan_type === "REGULAR" ? "regular" : ""}">${esc(it.plan_type)}</span>
        </div>`).join("");
      sugBox.style.display = "";
      sugBox.querySelectorAll(".suggest-item").forEach((el) =>
        el.addEventListener("mousedown", (e) => { e.preventDefault(); goSuggest(+el.dataset.code); }));
    } catch (e) { hideSuggest(); }
  }

  qInput.addEventListener("input", () => { clearTimeout(sugTimer); sugTimer = setTimeout(loadSuggest, 180); });
  qInput.addEventListener("keydown", (e) => {
    const open = sugBox.style.display !== "none";
    if (e.key === "ArrowDown" && open) { e.preventDefault(); sugActive = Math.min(sugActive + 1, sugItems.length - 1); paintActive(); }
    else if (e.key === "ArrowUp" && open) { e.preventDefault(); sugActive = Math.max(sugActive - 1, 0); paintActive(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (open && sugActive >= 0) goSuggest(sugItems[sugActive].amfi_scheme_code);
      else { hideSuggest(); state.q = qInput.value.trim(); state.page = 1; loadList(); }
    }
    else if (e.key === "Escape") hideSuggest();
  });
  qInput.addEventListener("blur", () => setTimeout(hideSuggest, 150));
  $("f-amc").onchange = (e) => { state.amc = e.target.value; state.page = 1; loadList(); };
  $("f-cat").onchange = (e) => { state.category = e.target.value; state.page = 1; loadList(); };
  $("f-opt").onchange = (e) => { state.option = e.target.value; state.page = 1; loadList(); };
  $("f-sort").onchange = (e) => { state.sort = e.target.value; state.page = 1; loadList(); };
  $("f-family").onchange = (e) => { state.family = e.target.checked; state.page = 1; loadList(); };

  // ---- top movers ----
  const mstate = { period: "1m", direction: "gainers" };
  async function loadMovers() {
    $("movers").innerHTML = '<div class="loading">Loading…</div>';
    try {
      const data = await api(`/api/movers?period=${mstate.period}&direction=${mstate.direction}&limit=10`);
      if (!data.results.length) { $("movers").innerHTML = '<div class="empty">No data.</div>'; return; }
      $("movers").innerHTML = '<div class="movers-list">' + data.results.map((f) => {
        const up = (f.pct_change || 0) >= 0;
        return `<div class="mover-row" data-code="${f.amfi_scheme_code}">
          <div>
            <div class="nm">${esc(f.scheme_name)}</div>
            <div class="sub">${esc(f.amfi_amc_name)} · ${esc(f.option_type)} · ₹${fmt(f.latest_nav, 2)}</div>
          </div>
          <span class="pct" style="color:var(--${up ? "up" : "down"})">${up ? "▲" : "▼"} ${Math.abs(f.pct_change || 0).toFixed(2)}%</span>
        </div>`;
      }).join("") + "</div>";
      document.querySelectorAll("#movers .mover-row").forEach((el) =>
        el.addEventListener("click", () => (location.href = "/fund/" + el.dataset.code)));
    } catch (e) { $("movers").innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  }
  document.querySelectorAll("#movers-period button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#movers-period button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); mstate.period = b.dataset.p; loadMovers();
    }));
  document.querySelectorAll("#movers-dir button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#movers-dir button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); mstate.direction = b.dataset.d; loadMovers();
    }));

  applyUrlState();
  loadStats(); loadFilters(); loadList(); loadMovers();
})();
