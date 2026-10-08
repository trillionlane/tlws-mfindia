(function () {
  const $ = (id) => document.getElementById(id);
  const code = location.pathname.split("/").pop();
  let chart = null;
  // Rolling-returns + projection-calculator state (see renderRolling/renderSIP).
  let rollingPts = null, rollingWindow = 1, rollingChart = null;
  let sipChart = null, sipMode = "sip";
  // Pencil-mark annotations on the NAV chart: up to 2 clicked dates.
  let marks = [], chartLabels = [], chartValues = [];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmt(v, d) { return v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d == null ? 4 : d }); }
  function fmtInt(v) { return v == null ? "—" : Math.round(Number(v)).toLocaleString("en-IN"); }
  async function api(p) { const r = await fetch(p); if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }

  // Charts bake colors in at build time, so axis/grid chrome is read from the
  // current CSS theme at draw time (see the --chart-* vars in style.css).
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

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
      <div class="price-sub">NAV as of ${f.latest_nav_date || "—"} · ISIN ${esc(f.isin_primary || "—")} · Code ${f.amfi_scheme_code}</div>
      <div style="margin-top:10px"><button id="cmp-add" class="toplink" type="button"></button></div>`;
    wireCompareBtn();
  }

  function wireCompareBtn() {
    const btn = $("cmp-add");
    if (!btn) return;
    const paint = () => {
      btn.textContent = MFDCompare.has(code) ? "✓ In compare list" : "＋ Add to compare";
    };
    paint();
    btn.addEventListener("click", () => {
      if (!MFDCompare.has(code) && !MFDCompare.add(code)) {
        btn.textContent = "Compare list is full (max 4)";
        setTimeout(paint, 1600);
        return;
      }
      location.href = "/compare";
    });
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
      ["Base expense ratio", fx.base_expense_ratio != null ? fmt(fx.base_expense_ratio, 2) + "%" : "—"],
      ["Benchmark", fx.benchmark_name || fx.benchmark || "—"],
      ["Fund manager", fx.fund_manager_name || "—"],
      ["Risk (riskometer)", fx.risk_level || "—"],
      ["Inception", fx.inception_date || fx.launch_date || "—"],
      ["Scheme type", f.scheme_type || "—"],
      ["Plan", f.plan_type],
      ["Option", f.option_type === "UNKNOWN" ? "—" : f.option_type],
      ["Registrar agent", fx.registrar_agent || "—"],
      ["SEBI category", fx.sebi_category_name || "—"],
      ["Asset class", fx.asset_class || "—"],
      ["Taxability", fx.taxability || "—"],
      ["Min. SIP", fx.min_subsequent_investment_amount != null ? "₹" + fmtInt(fx.min_subsequent_investment_amount) : "—"],
      ["SIP allowed", fx.is_sip_allowed == null ? "—" : fx.is_sip_allowed ? "Yes" : "No"],
      ["ISIN (growth/payout)", f.isin_growth_or_div_payout || "—"],
      ["ISIN (reinvest)", f.isin_div_reinvest || "—"],
      ["Sub-type", fx.sub_type || "—"],
      ["Lock-in", fx.lock_in_period || "—"],
      ["Exit load", fx.exit_load_value || "—"],
      ["Portfolio turnover", fx.portfolio_turnover != null ? fmt(fx.portfolio_turnover, 1) + "%" : "—"],
      ["Sharpe ratio", fx.sharpe_ratio != null ? fmt(fx.sharpe_ratio, 2) : "—"],
      ["Beta", fx.beta != null ? fmt(fx.beta, 2) : "—"],
    ];
    $("facts").innerHTML = cells.map(([k, v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");
  }

  // ---- expense ratio history (from the Groww re-enrichment) ----
  let erChart = null;
  function renderErHistory(f) {
    const hist = f.facts && f.facts.expense_ratio_history;
    if (!Array.isArray(hist) || hist.length < 2) return;
    const pts = hist
      .filter((h) => h && h.as_on_date)
      .map((h) => ({
        date: String(h.as_on_date).slice(0, 10),
        base: h.base_expense_ratio != null ? Number(h.base_expense_ratio) : null,
        total: h.expense_ratio != null ? Number(h.expense_ratio) : null,
      }))
      .sort((a, b) => (a.date < b.date ? -1 : 1));
    if (pts.length < 2) return;
    // Prefer the base ER (the part a fund house actually controls); fall back to
    // the total where base is missing on every point.
    const useBase = pts.some((p) => p.base != null);
    const val = (p) => (useBase ? p.base : p.total);

    // Revision points: every date the value first appeared / changed.
    const revs = [];
    let prev = null;
    for (const p of pts) {
      const v = val(p);
      if (v == null) continue;
      if (prev == null) revs.push({ date: p.date, from: null, to: v });
      else if (v !== prev) revs.push({ date: p.date, from: prev, to: v });
      prev = v;
    }
    const changes = revs.filter((r) => r.from != null);
    const last = revs.length ? revs[revs.length - 1] : null;
    const fmtD = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN",
      { day: "2-digit", month: "short", year: "numeric" });

    let logHtml;
    if (changes.length) {
      logHtml = `<div class="erlog">` + changes.map((r) =>
        `<div class="erlog-row"><span class="when">${fmtD(r.date)}</span>` +
        `<span class="vals">${r.from.toFixed(2)}% → <b>${r.to.toFixed(2)}%</b></span>` +
        (r.to < r.from
          ? '<span class="badge regular">reduced</span>'
          : '<span class="badge" style="background:var(--down-bg);color:var(--down);border-color:transparent">increased</span>') +
        `</div>`).join("") + `</div>`;
    } else if (last) {
      logHtml = `<div style="color:var(--muted);font-size:13px">No revisions in the available history — ` +
        `${last.to.toFixed(2)}% throughout (since ${fmtD(revs[0].date)}).</div>`;
    } else {
      logHtml = "";
    }

    $("erhist-card").style.display = "";
    $("erhist").innerHTML =
      `<div style="height:130px;position:relative"><canvas id="erchart"></canvas></div>` + logHtml;
    if (erChart) erChart.destroy();
    erChart = new Chart($("erchart"), {
      type: "line",
      data: { labels: pts.map((p) => p.date), datasets: [{
        data: pts.map(val), borderColor: "#e8710a", borderWidth: 1.6,
        pointRadius: 0, stepped: true, fill: true,
        backgroundColor: "rgba(232,113,10,0.06)",
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => (c.parsed.y == null ? "—" : c.parsed.y.toFixed(2) + "%") } } },
        scales: {
          x: { ticks: { maxTicksLimit: 8, color: cssVar("--chart-tick") }, grid: { display: false } },
          y: { ticks: { color: cssVar("--chart-tick"), callback: (v) => v + "%" }, grid: { color: cssVar("--chart-grid") } },
        },
      },
    });
  }

  let holdingsAll = null, holdingsExpanded = false;
  function renderHoldings(f) {
    if (!f.holdings || !f.holdings.length) return;
    holdingsAll = f.holdings;
    holdingsExpanded = false;
    $("holdings-card").style.display = "";
    paintHoldings();
  }
  function paintHoldings() {
    const all = holdingsAll;
    const shown = holdingsExpanded ? all : all.slice(0, 5);
    const maxW = all[0].weight_pct || 1;
    const totalW = shown.reduce((a, h) => a + (h.weight_pct || 0), 0);
    const rows = shown.map((h) => {
      const w = h.weight_pct;
      const barW = w ? Math.min(100, (w / maxW) * 100) : 0;
      return `<div style="padding:8px 16px;border-top:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;font-size:14px;margin-bottom:4px">
          <span>${esc(h.company_name)}${h.sector_name ? ` <span style="color:var(--muted);font-size:12px">· ${esc(h.sector_name)}</span>` : ""}</span>
          <span style="font-variant-numeric:tabular-nums;font-weight:600">${w != null ? w.toFixed(2) + "%" : "—"}</span>
        </div>
        <div style="height:4px;background:var(--panel);border-radius:2px"><div style="height:4px;width:${barW}%;background:var(--accent);border-radius:2px"></div></div>
      </div>`;
    }).join("");
    const toggle = all.length > 5
      ? `<div style="padding:10px 16px 0"><button id="holdings-toggle" class="toplink" type="button" style="font-size:13px">${holdingsExpanded ? "Show top 5" : "Show all " + all.length}</button></div>`
      : "";
    $("holdings").innerHTML = rows + toggle +
      `<div style="padding:10px 16px;color:var(--muted);font-size:12px">Top ${shown.length} of ${all.length} shown · ${totalW.toFixed(1)}% of portfolio</div>`;
    const btn = $("holdings-toggle");
    if (btn) btn.onclick = () => { holdingsExpanded = !holdingsExpanded; paintHoldings(); };
  }

  function palette(n) {
    const base = ["#1a73e8", "#188038", "#e8710a", "#9334e6", "#d93025",
      "#0095f7", "#c237a2", "#f9b300", "#12b5cb", "#7f6100", "#649136", "#aa4b61"];
    const out = [];
    for (let i = 0; i < n; i++) out.push(base[i % base.length]);
    return out;
  }

  // Doughnut chart for one holdings-analysis split. The exact-value legend is
  // rendered as HTML below the canvas (Chart.js's built-in legend shows no %s).
  function doughnut(canvasId, items) {
    const colors = palette(items.length);
    return new Chart($(canvasId), {
      type: "doughnut",
      data: { labels: items.map((i) => i.label),
        datasets: [{ data: items.map((i) => i.value), backgroundColor: colors,
          borderColor: cssVar("--chart-fill-border"), borderWidth: 1 }] },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "58%",
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => ` ${c.label}: ${Number(c.parsed).toFixed(2)}%` } } },
      },
    });
  }
  function legendHtml(items) {
    const colors = palette(items.length);
    return `<div class="ac-legend">` + items.map((it, i) => `
      <div class="ac-legend-row">
        <span class="sw" style="background:${colors[i]}"></span>
        <span class="nm">${esc(it.label)}</span>
        <span class="pv">${it.value.toFixed(2)}%</span>
      </div>`).join("") + `</div>`;
  }

  let acChart = null, secChart = null;
  function renderAnalysis(f) {
    const ha = f.facts && f.facts.holdings_analysis;
    if (!ha || (!ha.asset_class && !ha.sector)) return;
    $("analysis-card").style.display = "";
    const toItems = (o) => o ? Object.entries(o)
      .map(([label, value]) => ({ label, value: Number(value) }))
      .filter((x) => x.value > 0)
      .sort((a, b) => b.value - a.value) : [];
    const ac = toItems(ha.asset_class);
    const sec = toItems(ha.sector);
    const block = (title, id, items) => {
      const body = items.length
        ? `<div class="ac-chart"><canvas id="${id}"></canvas></div>${legendHtml(items)}`
        : '<div style="color:var(--muted);font-size:13px;padding:14px 0">—</div>';
      return `<div class="ac-block"><div class="ac-title">${title}</div>${body}</div>`;
    };
    let html = `<div class="ac-grid">${block("Asset class", "acchart", ac)}${block("Sector", "sechart", sec)}</div>`;
    const meta = [];
    if (ha.source) meta.push(`Source ${esc(ha.source)}`);
    if (ha.computed_at) meta.push(`computed ${new Date(ha.computed_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}`);
    if (meta.length) html += `<div style="margin-top:16px;color:var(--muted);font-size:11px">${meta.join(" · ")}</div>`;
    $("analysis").innerHTML = html;
    if (ac.length) { if (acChart) acChart.destroy(); acChart = doughnut("acchart", ac); }
    if (sec.length) { if (secChart) secChart.destroy(); secChart = doughnut("sechart", sec); }
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

  // ---- pencil-mark annotations --------------------------------------------
  // Inline plugin: draws a dashed vertical line + dot at each marked index.
  const navMarkPlugin = {
    id: "navMarks",
    afterDatasetsDraw(c) {
      const ms = c.$marks || [];
      if (!ms.length) return;
      const { ctx, chartArea } = c;
      const meta = c.getDatasetMeta(0);
      ms.forEach((idx) => {
        const pt = meta.data[idx];
        if (!pt) return;
        ctx.save();
        ctx.strokeStyle = "#e8710a"; ctx.lineWidth = 1.2; ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(pt.x, chartArea.top); ctx.lineTo(pt.x, chartArea.bottom); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = "#e8710a";
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 3.5, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      });
    },
  };
  function addMark(idx) {
    if (marks.includes(idx)) marks = marks.filter((m) => m !== idx);
    else { marks.push(idx); if (marks.length > 2) marks.shift(); marks.sort((a, b) => a - b); }
    chart.$marks = marks; chart.update();
    paintMarks();
  }
  function clearMarks() { marks = []; if (chart) { chart.$marks = []; chart.update(); } paintMarks(); }
  function paintMarks() {
    const el = $("marks");
    if (!el) return;
    if (!marks.length) { el.style.display = "none"; return; }
    const items = marks.map((i) =>
      `<div class="mk-row"><span class="mk-date">${esc(chartLabels[i])}</span><span class="mk-nav">₹${fmt(chartValues[i], 2)}</span></div>`).join("");
    let between = "";
    if (marks.length === 2) {
      const [a, b] = marks;
      const pct = (chartValues[b] / chartValues[a] - 1) * 100;
      const up = pct >= 0;
      between = `<div class="mk-between ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(pct).toFixed(2)}% <span class="mk-sub">${esc(chartLabels[a])} → ${esc(chartLabels[b])}</span></div>`;
    }
    el.style.display = "";
    el.innerHTML = `<div class="mk-box">${items}${between}</div>
      <button id="marks-clear" class="toplink" style="font-size:12px" type="button">Clear marks</button>`;
    $("marks-clear").onclick = clearMarks;
  }

  // ---- derived analytics (risk + returns breakdown) ------------------------
  let chartMode = "line";
  function buildNavChart() {
    const labels = chartLabels, values = chartValues;
    if (!values.length) return;
    const common = {
      responsive: true, maintainAspectRatio: true, interaction: { mode: "index", intersect: false },
      onClick: (evt) => {
        const meta = chart.getDatasetMeta(0);
        let best = -1, bestD = Infinity;
        meta.data.forEach((pt, i) => { const d = Math.abs(pt.x - evt.x); if (d < bestD) { bestD = d; best = i; } });
        if (best >= 0) addMark(best);
      },
    };
    if (chart) chart.destroy();
    if (chartMode === "candles") {
      // NAV is a daily close only: each candle body spans previous -> current
      // NAV. No intraday data exists, so wicks are degenerate (zero length).
      const prev = [values[0], ...values.slice(0, -1)];
      const bars = values.map((v, i) => [Math.min(prev[i], v), Math.max(prev[i], v)]);
      const colors = values.map((v, i) => i === 0 ? "#dadce0"
        : v >= prev[i] ? "rgba(24,128,56,.75)" : "rgba(217,48,37,.75)");
      chart = new Chart($("chart"), {
        type: "bar",
        data: { labels, datasets: [{ data: bars, backgroundColor: colors, borderWidth: 0, borderSkipped: false }] },
        plugins: [navMarkPlugin],
        options: {
          ...common,
          plugins: { legend: { display: false }, tooltip: { callbacks: {
            label: (c) => {
              const i = c.dataIndex, chg = values[i] - prev[i];
              return `${labels[i]}: ₹${fmt(values[i], 2)} (${chg >= 0 ? "+" : ""}${chg.toFixed(2)})`;
            } } } },
          scales: {
            x: { ticks: { maxTicksLimit: 8, color: cssVar("--chart-tick") }, grid: { display: false } },
            y: { ticks: { color: cssVar("--chart-tick"), callback: (v) => "₹" + fmt(v, 2) }, grid: { color: cssVar("--chart-grid") } },
          },
        },
      });
    } else {
      const up = values.length > 1 && values[values.length - 1] >= values[0];
      const color = up ? "#188038" : "#d93025";
      chart = new Chart($("chart"), {
        type: "line",
        data: { labels, datasets: [{ data: values, borderColor: color, borderWidth: 1.8, pointRadius: 0, tension: 0.08, fill: true,
          backgroundColor: up ? "rgba(24,128,56,0.08)" : "rgba(217,48,37,0.08)" }] },
        plugins: [navMarkPlugin],
        options: {
          ...common,
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => "₹" + fmt(c.parsed.y) } } },
          scales: {
            x: { ticks: { maxTicksLimit: 8, color: cssVar("--chart-tick") }, grid: { display: false } },
            y: { ticks: { color: cssVar("--chart-tick"), callback: (v) => "₹" + fmt(v, 2) }, grid: { color: cssVar("--chart-grid") } },
          },
        },
      });
    }
    chart.$marks = marks.slice();
    paintMarks();
  }

  async function loadChart(years) {
    const p = years ? ("?years=" + years) : "";
    const data = await api(`/api/funds/${code}/nav${p}`);
    chartLabels = data.points.map((x) => x.date);
    chartValues = data.points.map((x) => x.nav);
    marks = [];                       // window changed -> old indices are stale
    buildNavChart();
  }

  // ---- derived analytics (risk + returns breakdown) ------------------------
  function renderRisk(a) {
    if (!a || a.too_short) return;
    $("risk-card").style.display = "";
    const cell = (k, v, cls) => `<div class="cell"><div class="k">${k}</div><div class="v ${cls || ""}">${esc(v)}</div></div>`;
    const pct = (v) => v == null ? "—" : v.toFixed(2) + "%";
    $("risk").innerHTML = [
      cell("Max drawdown", pct(a.max_drawdown_pct), "down"),
      cell("DD peak → trough", a.max_dd_peak_date + " → " + a.max_dd_trough_date),
      cell("Recovered", a.max_dd_recovery_date || "still underwater"),
      cell("Annual volatility", pct(a.annual_vol_pct)),
      cell("CAGR", a.cagr_pct != null ? (a.cagr_pct >= 0 ? "+" : "") + pct(a.cagr_pct) : "—"),
      cell("Sharpe", a.sharpe != null ? a.sharpe.toFixed(2) : "—"),
      cell("Sortino", a.sortino != null ? a.sortino.toFixed(2) : "—"),
      cell("Calmar", a.calmar != null ? a.calmar.toFixed(2) : "—"),
      cell("Win rate · days", a.win_rate_days_pct != null ? a.win_rate_days_pct.toFixed(0) + "%" : "—"),
      cell("Win rate · months", a.win_rate_months_pct != null ? a.win_rate_months_pct.toFixed(0) + "%" : "—"),
    ].join("");
    $("risk-note").textContent = `Computed from our ${a.points.toLocaleString("en-IN")} daily AMFI NAV points (${a.first_date} → ${a.as_of}); risk-free ${a.risk_free_pct}%. Ours, not the source-published Sharpe/volatility.`;
  }

  let yrChart = null;
  function renderBreakdown(a) {
    if (!a || a.too_short) return;
    $("breakdown-card").style.display = "";
    const yvals = a.yearly.map((y) => y.return_pct);
    if (yrChart) yrChart.destroy();
    yrChart = new Chart($("yrcanvas"), {
      type: "bar",
      data: { labels: a.yearly.map((y) => y.year), datasets: [{
        data: yvals, borderRadius: 3,
        backgroundColor: yvals.map((v) => v == null ? "#e0e3e7" : v >= 0 ? "rgba(24,128,56,.75)" : "rgba(217,48,37,.75)"),
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => c.parsed.y == null ? "—" : (c.parsed.y >= 0 ? "+" : "") + c.parsed.y.toFixed(2) + "%" } } },
        scales: {
          x: { ticks: { color: cssVar("--chart-tick") }, grid: { display: false } },
          y: { ticks: { color: cssVar("--chart-tick"), callback: (v) => v + "%" }, grid: { color: cssVar("--chart-grid") } },
        },
      },
    });
    renderHeatmap(a.monthly);
  }

  function renderHeatmap(monthly) {
    const el = $("heatmap");
    const years = Object.keys(monthly).sort();
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let maxAbs = 0;
    years.forEach((y) => months.forEach((_, i) => {
      const v = monthly[y][String(i + 1)];
      if (v != null) maxAbs = Math.max(maxAbs, Math.abs(v));
    }));
    if (maxAbs === 0) maxAbs = 1;
    const dark = document.documentElement.dataset.theme === "dark";
    // Low-alpha cells need a light-foreground fallback in dark mode: the
    // tinted-dark greens/reds used on white are unreadable on the dark card.
    const paint = (v) => {
      if (v == null) return { bg: "transparent", fg: "transparent" };
      const t = Math.abs(v) / maxAbs, alpha = 0.12 + 0.78 * t;
      return v >= 0
        ? { bg: `rgba(24,128,56,${alpha.toFixed(3)})`, fg: t > 0.45 ? "#fff" : (dark ? "#c7e5d2" : "#14532d") }
        : { bg: `rgba(217,48,37,${alpha.toFixed(3)})`, fg: t > 0.45 ? "#fff" : (dark ? "#f5c2be" : "#7f1d1d") };
    };
    let html = '<div class="hm"><div class="hm-corner"></div>';
    years.forEach((y) => { html += `<div class="hm-colhead">${y}</div>`; });
    months.forEach((m, i) => {
      html += `<div class="hm-rowhead">${m}</div>`;
      years.forEach((y) => {
        const v = monthly[y][String(i + 1)];
        const c = paint(v);
        const title = v == null ? "" : `${m} ${y}: ${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
        html += `<div class="hm-cell" style="background:${c.bg};color:${c.fg}" title="${esc(title)}">${v == null ? "" : v.toFixed(1)}</div>`;
      });
    });
    html += "</div>";
    el.innerHTML = html;
    el.querySelector(".hm").style.gridTemplateColumns = `44px repeat(${years.length}, minmax(42px, 1fr))`;
  }

  // ---- peer ranking / risk map / debt profile (Tier 3 + Tier 4) -----------
  function renderPeers(p) {
    const el = $("peers");
    if (!p || !p.category || !p.horizons || !Object.keys(p.horizons).length) return;
    const rows = [];
    for (const label of ["1Y", "3Y", "5Y"]) {
      const h = p.horizons[label];
      if (!h) continue;
      if (h.beats_pct == null) {
        rows.push(`<div class="peer-row"><span class="peer-h">${label}</span>
          <span class="peer-muted">ranked among too few peers (${h.peer_count || 0}) to score</span></div>`);
        continue;
      }
      const w = Math.max(2, Math.min(100, h.beats_pct));
      rows.push(`<div class="peer-row">
        <span class="peer-h">${label}</span>
        <div class="peer-track"><div class="peer-fill" style="width:${w}%"></div></div>
        <span class="peer-val">beats <b>${h.beats_pct.toFixed(0)}%</b> of ${h.peer_count} peers</span>
        <span class="peer-ret" style="color:var(${h.fund_return >= 0 ? "--up" : "--down"})">${h.fund_return >= 0 ? "+" : ""}${h.fund_return.toFixed(2)}%</span>
      </div>`);
    }
    $("peers-card").style.display = "";
    $("peers-cat").textContent = p.category;
    el.innerHTML = rows.join("");
    const scored = Object.values(p.horizons).filter((h) => h.beats_pct != null);
    $("peers-note").textContent = scored.length
      ? "Percentile is computed from our own fund_facts returns across all in-scope funds in this SEBI category (funds with fewer than 10 scored peers are not ranked)."
      : "Not enough peers in this category carry our return data to rank.";
  }

  let rmChart = null;
  function renderRiskMap(r) {
    const el = $("riskmap-card");
    if (!r || !r.category || !r.points || r.points.length < 3) { el.style.display = "none"; return; }
    el.style.display = "";
    $("riskmap-cat").textContent = r.category;
    const peerPts = r.points.filter((p) => !p.self).map((p) => ({
      x: p.vol, y: p["return"], r: 3 + Math.sqrt(Math.max(p.aum || 0, 1)) * 0.06,
      name: p.scheme_name, code: p.amfi_scheme_code, aum: p.aum,
    }));
    const self = r.points.find((p) => p.self);
    const selfDs = self ? [{
      label: self.scheme_name,
      data: [{ x: self.vol, y: self["return"], r: 8, name: self.scheme_name,
               code: self.amfi_scheme_code, aum: self.aum }],
      backgroundColor: "rgba(26,115,232,.85)", borderColor: cssVar("--chart-fill-border"), borderWidth: 1.5,
    }] : [];
    if (rmChart) rmChart.destroy();
    rmChart = new Chart($("rmchart"), {
      type: "bubble",
      data: { datasets: [
        { label: "Category peers", data: peerPts,
          backgroundColor: "rgba(95,99,104,.35)", borderColor: "rgba(95,99,104,.6)", borderWidth: 1 },
        ...selfDs,
      ] },
      options: {
        responsive: true, maintainAspectRatio: false,
        onClick: (evt) => {
          const pts = rmChart.getElementsAtEventForMode(evt, "nearest", { intersect: true }, true);
          const hit = pts && pts[0] && rmChart.data.datasets[pts[0].datasetIndex].data[pts[0].index];
          if (hit && hit.code && hit.code !== code) location.href = "/fund/" + hit.code;
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => {
          const d = c.raw;
          return ` ${d.name}: ${d.x.toFixed(1)}% vol · ${d.y >= 0 ? "+" : ""}${d.y.toFixed(1)}% CAGR` +
            (d.aum != null ? ` · ₹${Math.round(d.aum).toLocaleString("en-IN")} cr` : "");
        } } } },
        scales: {
          x: { title: { display: true, text: "Annualised volatility (%)", color: cssVar("--chart-tick"), font: { size: 11 } },
               ticks: { color: cssVar("--chart-tick"), callback: (v) => v + "%" }, grid: { color: cssVar("--chart-grid") } },
          y: { title: { display: true, text: "CAGR (%)", color: cssVar("--chart-tick"), font: { size: 11 } },
               ticks: { color: cssVar("--chart-tick"), callback: (v) => v + "%" }, grid: { color: cssVar("--chart-grid") } },
        },
      },
    });
    $("riskmap-note").textContent = "Each bubble is a peer in this SEBI category (size ≈ AUM). Blue is this fund. Top-left is the sweet spot: low volatility, high return. Click a peer to open it.";
  }

  function renderDebt(f) {
    const m = f.facts && f.facts.holdings_maturity;
    if (!m) return;
    const dur = m.macaulay_duration != null ? m.macaulay_duration : m.duration;
    const cells = [
      ["Macaulay duration", dur != null ? dur.toFixed(2) + " yrs" : "—"],
      ["Average YTM", m.avg_ytm != null ? m.avg_ytm.toFixed(2) + "%" : "—"],
      ["Avg. maturity", m.average_maturity_period != null ? m.average_maturity_period.toFixed(2) + " yrs" : "—"],
    ];
    if (!cells.some(([, v]) => v !== "—")) return;
    $("debt-card").style.display = "";
    $("debt").innerHTML = cells.map(([k, v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");
    $("debt-note").textContent = m.as_on_date ? `As of ${m.as_on_date}` : "";
  }

  function renderFactsNote(f) {
    const fx = f.facts || {};
    const el = $("facts-note");
    const note = fx.exit_load && fx.exit_load.note ? fx.exit_load.note : "";
    if (!note) { el.style.display = "none"; return; }
    el.style.display = "";
    el.textContent = "Exit load note: " + note + (fx.exit_load && fx.exit_load.as_on_date ? ` (as of ${fx.exit_load.as_on_date})` : "");
  }

  // ---- projection calculator (SIP / lumpsum) ------------------------------
  // Purely illustrative: a constant-rate future value, no fee/tax/step-up
  // modelling. The default rate is prefilled from the trailing 3Y CAGR.
  function parseAmt(id, fallback) {
    const el = $(id); const n = el ? parseFloat(el.value) : NaN;
    return isFinite(n) && n > 0 ? n : fallback;
  }
  function sipFuture(amt, yrs, ratePct) {
    const n = Math.max(1, Math.round(yrs)) * 12, i = ratePct / 100 / 12;
    if (i === 0) return { fv: amt * n, invested: amt * n };
    const fv = amt * ((Math.pow(1 + i, n) - 1) / i) * (1 + i);  // SIP-due (month-start)
    return { fv, invested: amt * n };
  }
  function lumpFuture(amt, yrs, ratePct) {
    return { fv: amt * Math.pow(1 + ratePct / 100, Math.max(0, yrs)), invested: amt };
  }
  function renderSIP() {
    const yrs = Math.min(40, Math.max(1, Math.round(parseAmt("sip-yrs", 10))));
    const rate = Math.min(30, Math.max(0, parseAmt("sip-rate", 12)));
    const isSIP = sipMode === "sip";
    const amt = isSIP ? parseAmt("sip-amt", 10000) : parseAmt("sip-lump", 100000);
    const r = isSIP ? sipFuture(amt, yrs, rate) : lumpFuture(amt, yrs, rate);
    const gain = r.fv - r.invested;
    const rows = [["Invested", "₹" + fmtInt(Math.round(r.invested))],
      ["Projected value", "₹" + fmtInt(Math.round(r.fv))],
      ["Projected gain", "₹" + fmtInt(Math.round(gain))],
      ["Return multiple", fmt(r.invested ? r.fv / r.invested : 0, 2) + "×"]];
    $("sip-result").innerHTML = rows.map(([k, v]) =>
      `<div class="cell"><div class="k">${k}</div><div class="v" style="font-size:18px">${v}</div></div>`).join("");
    $("sip-note").textContent = `Illustrative only — assumes a constant ${fmt(rate, 2)}% p.a. ` +
      (isSIP ? "with contributions invested at the start of each month (SIP-due), "
             : "compounded annually, ") +
      "reinvested gains, and no fees, taxes, or step-ups. Not a forecast; actual returns will vary.";
    const N = Math.max(1, Math.round(yrs));
    const labels = ["0"], val = [0];
    for (let y = 1; y <= N; y++) {
      labels.push(String(y));
      val.push(isSIP ? sipFuture(amt, y, rate).fv : lumpFuture(amt, y, rate).fv);
    }
    if (sipChart) sipChart.destroy();
    sipChart = new Chart($("sipcanvas"), {
      type: "line",
      data: { labels, datasets: [{ data: val, borderColor: "#1a73e8", borderWidth: 2,
        pointRadius: 0, fill: true, backgroundColor: "rgba(26,115,232,0.10)", tension: 0.15 }] },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => " ₹" + fmtInt(Math.round(c.parsed.y)) + " after " + c.label + " yr" } } },
        scales: { x: { grid: { display: false }, title: { display: true, text: "Years", color: cssVar("--chart-tick") } },
          y: { ticks: { color: cssVar("--chart-tick"), callback: (v) => "₹" + (v >= 1e7 ? (v / 1e7).toFixed(1) + "cr" : v >= 1e5 ? (v / 1e5).toFixed(1) + "L" : Math.round(v)) }, grid: { color: cssVar("--chart-grid") } } } },
    });
  }

  // ---- rolling returns (computed from the NAV series) ---------------------
  function rollingSeries(pts, years) {
    const span = years * 365.25, out = [];
    for (let j = Math.floor(span); j < pts.length; j++) {
      let i = j - Math.floor(span);
      if (i < 0) continue;
      // Walk back to the NAV closest to (j - span); trading-day gaps make exact rare.
      while (i > 0 && (new Date(pts[j].date) - new Date(pts[i].date)) / 86400000 > span + 6) i--;
      const a = pts[i].nav, b = pts[j].nav;
      if (a > 0 && b > 0) out.push({ date: pts[j].date, val: (Math.pow(b / a, 1 / years) - 1) * 100 });
    }
    return out;
  }
  function buildRollingChart() {
    if (!rollingPts || rollingPts.length < 2) return;
    const pts = rollingSeries(rollingPts, rollingWindow);
    if (!pts.length) {
      // Enough NAV for the card but not for this window (e.g. 3Y on a young fund):
      // keep the card so the user can switch back, and explain in the note.
      $("rolling-sub").textContent = `${rollingWindow}Y annualised`;
      $("rolling-note").textContent = `Not enough NAV history for a ${rollingWindow}Y rolling window.`;
      if (rollingChart) { rollingChart.destroy(); rollingChart = null; }
      return;
    }
    $("rolling-card").style.display = "";
    const vals = pts.map((p) => p.val);
    const best = Math.max(...vals), worst = Math.min(...vals);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    const above = vals.filter((v) => v > 0).length;
    $("rolling-sub").textContent = `${rollingWindow}Y annualised · ${pts.length} rolling windows`;
    $("rolling-note").textContent =
      `Best ${best.toFixed(2)}% · Worst ${worst.toFixed(2)}% · Average ${avg.toFixed(2)}% · ` +
      `${((above / vals.length) * 100).toFixed(0)}% of windows positive. Each point is the annualised ` +
      `return over the trailing ${rollingWindow} year(s) ending that date — the variability you would ` +
      `have experienced holding this fund, not a single point-to-point figure.`;
    if (rollingChart) rollingChart.destroy();
    rollingChart = new Chart($("rollingcanvas"), {
      type: "line",
      data: { labels: pts.map((p) => p.date), datasets: [{ data: vals,
        borderColor: "#1a73e8", borderWidth: 1.4, pointRadius: 0, fill: { target: { value: 0 } },
        above: "rgba(26,115,232,0.14)", below: "rgba(217,48,37,0.14)" }] },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => " " + (c.parsed.y >= 0 ? "+" : "") + c.parsed.y.toFixed(2) + "%" } } },
        scales: { x: { ticks: { maxTicksLimit: 8, color: cssVar("--chart-tick") }, grid: { display: false } },
          y: { ticks: { color: cssVar("--chart-tick"), callback: (v) => v + "%" }, grid: { color: cssVar("--chart-grid") },
               title: { display: true, text: `${rollingWindow}Y annualised`, color: cssVar("--chart-tick") } } } },
    });
  }
  async function renderRolling() {
    try {
      const d = await api(`/api/funds/${code}/nav?years=20`);
      rollingPts = (d.points || []).filter((p) => p.nav != null);
    } catch (e) { return; }
    if (!rollingPts || rollingPts.length < 2) return;
    // Cap to ~10y of daily points to bound the rolling computation.
    if (rollingPts.length > 2600) rollingPts = rollingPts.slice(rollingPts.length - 2600);
    buildRollingChart();
  }

  document.querySelectorAll("#ranges button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#ranges button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      loadChart(b.dataset.y || null);
    }));
  document.querySelectorAll("#chartmode button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#chartmode button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      chartMode = b.dataset.m;
      if (chartLabels.length) buildNavChart();
    }));

  document.querySelectorAll("#rolling-range button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#rolling-range button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      rollingWindow = Number(b.dataset.w) || 1;
      buildRollingChart();
    }));
  document.querySelectorAll("#sip-mode button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#sip-mode button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      sipMode = b.dataset.mode === "lumpsum" ? "lumpsum" : "sip";
      $("sip-amt-wrap").style.display = sipMode === "sip" ? "" : "none";
      $("sip-lump-wrap").style.display = sipMode === "lumpsum" ? "" : "none";
      renderSIP();
    }));
  ["sip-amt", "sip-yrs", "sip-rate", "sip-lump"].forEach((id) => $(id).addEventListener("input", renderSIP));
  renderSIP();

  // Full page (re)load. Called once on start and again on theme change so
  // every chart is rebuilt with the current --chart-* colors. Pencil marks
  // (module-level `marks`) survive a rebuild.
  async function init() {
    [erChart, acChart, secChart, chart, yrChart, rmChart, sipChart, rollingChart]
      .forEach((c) => { if (c) c.destroy(); });
    erChart = acChart = secChart = chart = yrChart = rmChart = sipChart = rollingChart = null;
    try {
      const fund = await api(`/api/funds/${code}`);
      renderHead(fund, null);
      renderFacts(fund);
      renderFactsNote(fund);
      renderErHistory(fund);
      renderAnalysis(fund);
      renderHoldings(fund);
      renderSiblings(fund);
      renderDebt(fund);
      const ret = await api(`/api/funds/${code}/returns`);
      const ana = await api(`/api/funds/${code}/analytics`);
      const peers = await api(`/api/funds/${code}/peers`);
      const riskmap = await api(`/api/funds/${code}/risk-reward`);
      renderReturns(ret);
      renderRisk(ana);
      renderBreakdown(ana);
      renderPeers(peers);
      renderRiskMap(riskmap);
      renderHead(fund, ret);
      // Prefill the calculator's default rate with the trailing 3Y CAGR, then draw.
      const cagr3 = ret && ret.horizons ? ret.horizons["3Y"] : null;
      if (cagr3 != null && isFinite(cagr3)) $("sip-rate").value = Math.max(0, Math.min(30, +cagr3.toFixed(2)));
      renderSIP();
      renderRolling();
      await loadChart(5);
    } catch (e) {
      $("head").innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  }
  window.__mfdThemeChanged = () => init();
  init();
})();
