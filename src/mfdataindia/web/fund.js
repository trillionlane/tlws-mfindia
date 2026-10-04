(function () {
  const $ = (id) => document.getElementById(id);
  const code = location.pathname.split("/").pop();
  let chart = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmt(v, d) { return v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d == null ? 4 : d }); }
  function fmtInt(v) { return v == null ? "—" : Math.round(Number(v)).toLocaleString("en-IN"); }
  async function api(p) { const r = await fetch(p); if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }

  function badgeFor(f) {
    const parts = [];
    parts.push(`<span class="badge ${f.plan_type === "REGULAR" ? "regular" : f.plan_type === "DIRECT" ? "direct" : ""}">${esc(f.plan_type)}</span>`);
    if (f.option_type && f.option_type !== "UNKNOWN") parts.push(`<span class="badge ${f.option_type === "IDCW" ? "idcw" : ""}">${esc(f.option_type)}</span>`);
    if (f.periodicity) parts.push(`<span class="badge">${esc(f.periodicity)}</span>`);
    if (f.is_defunct) parts.push('<span class="badge defunct">defunct</span>');
    if (!f.in_scope) parts.push('<span class="badge">out of scope</span>');
    return parts.join(" ");
  }

  function renderHead(f, ret) {
    const h = ret && ret.horizons ? ret.horizons : {};
    let change = "";
    if (h["1Y"] != null) {
      const v = h["1Y"]; const up = v >= 0;
      change = `<span class="chip ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(v).toFixed(2)}% (1Y)</span>`;
    }
    $("head").innerHTML = `
      <h1>${esc(f.scheme_name)}</h1>
      <div class="sub">${esc(f.amfi_amc_name)} · ${esc(f.scheme_category)} · ${badgeFor(f)}</div>
      <div class="price-row">
        <div class="price">₹${fmt(f.latest_nav)}</div>
        ${change}
      </div>
      <div class="price-sub">NAV as of ${f.latest_nav_date || "—"} · ISIN ${esc(f.isin_primary || "—")} · Code ${f.amfi_scheme_code}</div>`;
  }

  function renderReturns(ret) {
    const order = ["1M", "3M", "6M", "1Y", "3Y", "5Y"];
    $("returns").innerHTML = order.map((k) => {
      const v = ret.horizons ? ret.horizons[k] : null;
      const txt = v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
      return `<div class="cell"><div class="k">${k}</div><div class="v" style="color:var(--${v == null ? "muted" : v >= 0 ? "up" : "down"})">${txt}</div></div>`;
    }).join("");
  }

  function renderFacts(f) {
    const fx = f.facts || {};
    const cells = [
      ["AUM", fx.aum != null ? "₹" + fmtInt(fx.aum) + " cr" : "—"],
      ["Expense ratio", fx.expense_ratio != null ? fmt(fx.expense_ratio, 2) + "%" : "—"],
      ["Benchmark", fx.benchmark_name || fx.benchmark || "—"],
      ["Fund manager", fx.fund_manager_name || "—"],
      ["Risk (riskometer)", fx.risk_level || "—"],
      ["Inception", fx.inception_date || fx.launch_date || "—"],
      ["Scheme type", f.scheme_type || "—"],
      ["Plan", f.plan_type],
      ["Option", f.option_type === "UNKNOWN" ? "—" : f.option_type],
      ["SEBI category", fx.sebi_category_name || "—"],
      ["Asset class", fx.asset_class || "—"],
      ["Taxability", fx.taxability || "—"],
      ["Min. SIP", fx.min_subsequent_investment_amount != null ? "₹" + fmtInt(fx.min_subsequent_investment_amount) : "—"],
      ["SIP allowed", fx.is_sip_allowed == null ? "—" : fx.is_sip_allowed ? "Yes" : "No"],
      ["ISIN (growth/payout)", f.isin_growth_or_div_payout || "—"],
      ["ISIN (reinvest)", f.isin_div_reinvest || "—"],
    ];
    $("facts").innerHTML = cells.map(([k, v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");
  }

  function renderHoldings(f) {
    if (!f.holdings || !f.holdings.length) return;
    $("holdings-card").style.display = "";
    const totalW = f.holdings.reduce((a, h) => a + (h.weight_pct || 0), 0);
    const rows = f.holdings.map((h) => {
      const w = h.weight_pct;
      const barW = w ? Math.min(100, (w / (f.holdings[0].weight_pct || 1)) * 100) : 0;
      return `<div style="padding:8px 16px;border-top:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;font-size:14px;margin-bottom:4px">
          <span>${esc(h.company_name)}${h.sector_name ? ` <span style="color:var(--muted);font-size:12px">· ${esc(h.sector_name)}</span>` : ""}</span>
          <span style="font-variant-numeric:tabular-nums;font-weight:600">${w != null ? w.toFixed(2) + "%" : "—"}</span>
        </div>
        <div style="height:4px;background:var(--panel);border-radius:2px"><div style="height:4px;width:${barW}%;background:var(--accent);border-radius:2px"></div></div>
      </div>`;
    }).join("");
    $("holdings").innerHTML = rows +
      `<div style="padding:10px 16px;color:var(--muted);font-size:12px">Top ${f.holdings.length} shown · ${totalW.toFixed(1)}% of portfolio</div>`;
  }

  function renderSiblings(f) {
    if (!f.siblings || f.siblings.length < 2) return;
    $("siblings-card").style.display = "";
    $("siblings").innerHTML = f.siblings.map((s) => {
      const cur = s.amfi_scheme_code == f.amfi_scheme_code;
      return `<div style="padding:8px;display:flex;gap:10px;align-items:center;border-top:1px solid var(--border)">
        <a href="/fund/${s.amfi_scheme_code}" style="${cur ? "font-weight:700" : ""}">${esc(s.scheme_name)}</a>
        <span class="badge ${s.plan_type === "REGULAR" ? "regular" : s.plan_type === "DIRECT" ? "direct" : ""}">${esc(s.plan_type)}</span>
        <span class="badge">${esc(s.option_type)}</span>
        ${cur ? '<span class="badge">this</span>' : ""}
      </div>`;
    }).join("");
  }

  async function loadChart(years) {
    const p = years ? ("?years=" + years) : "";
    const data = await api(`/api/funds/${code}/nav${p}`);
    const labels = data.points.map((x) => x.date);
    const values = data.points.map((x) => x.nav);
    const up = values.length > 1 && values[values.length - 1] >= values[0];
    const color = up ? "#188038" : "#d93025";
    if (chart) chart.destroy();
    chart = new Chart($("chart"), {
      type: "line",
      data: { labels, datasets: [{ data: values, borderColor: color, borderWidth: 1.8, pointRadius: 0, tension: 0.08, fill: true,
        backgroundColor: up ? "rgba(24,128,56,0.08)" : "rgba(217,48,37,0.08)" }] },
      options: {
        responsive: true, maintainAspectRatio: true, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => "₹" + fmt(c.parsed.y) } } },
        scales: {
          x: { ticks: { maxTicksLimit: 8, color: "#5f6368" }, grid: { display: false } },
          y: { ticks: { color: "#5f6368", callback: (v) => "₹" + fmt(v, 2) }, grid: { color: "#eef0f2" } },
        },
      },
    });
  }

  document.querySelectorAll("#ranges button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#ranges button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      loadChart(b.dataset.y || null);
    }));

  (async () => {
    try {
      const fund = await api(`/api/funds/${code}`);
      renderHead(fund, null);
      renderFacts(fund);
      renderHoldings(fund);
      renderSiblings(fund);
      const ret = await api(`/api/funds/${code}/returns`);
      renderReturns(ret);
      renderHead(fund, ret);
      await loadChart(5);
    } catch (e) {
      $("head").innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  })();
})();
