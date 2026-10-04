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
  }

  async function loadList() {
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
        <td class="num">${f.latest_nav_date || "—"}</td>
        <td class="num">${f.return_5year == null ? "—" : fmt(f.return_5year, 1) + "%"}</td>
        <td class="num">${f.aum == null ? "—" : "₹" + Math.round(f.aum).toLocaleString("en-IN") + " cr"}</td>
      </tr>`).join("");
    $("list").innerHTML = `
      <table class="fundlist">
        <thead><tr><th>Fund</th><th>Plan / Option</th><th class="nav">Latest NAV</th><th class="num">As of</th><th class="num">5Y</th><th class="num">AUM</th></tr></thead>
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

  let timer;
  $("q").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = e.target.value.trim(); state.page = 1; loadList(); }, 250);
  });
  $("f-amc").onchange = (e) => { state.amc = e.target.value; state.page = 1; loadList(); };
  $("f-cat").onchange = (e) => { state.category = e.target.value; state.page = 1; loadList(); };
  $("f-opt").onchange = (e) => { state.option = e.target.value; state.page = 1; loadList(); };
  $("f-sort").onchange = (e) => { state.sort = e.target.value; state.page = 1; loadList(); };
  $("f-family").onchange = (e) => { state.family = e.target.checked; state.page = 1; loadList(); };

  loadStats(); loadFilters(); loadList();
})();
