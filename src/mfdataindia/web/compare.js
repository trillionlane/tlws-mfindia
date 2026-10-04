(function () {
  const $ = (id) => document.getElementById(id);
  const COLORS = ["#1a73e8", "#188038", "#d93025", "#e8710a"];
  const state = { selected: [], years: 1 };
  let chart = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmt(v, d) { return v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d == null ? 2 : d }); }
  async function api(p) { const r = await fetch(p); if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }

  // ---- autocomplete to add funds ----
  let sugTimer, sugItems = [], sugActive = -1;
  const qInput = $("cq"), sugBox = $("c-suggest");
  function hideSuggest() { sugBox.style.display = "none"; sugItems = []; sugActive = -1; }

  function addFund(it) {
    if (state.selected.some((s) => s.code === it.amfi_scheme_code)) { hideSuggest(); return; }
    if (state.selected.length >= 4) return;
    state.selected.push({ code: it.amfi_scheme_code, name: it.scheme_name, amc: it.amfi_amc_name });
    hideSuggest();
    qInput.value = "";
    renderChips();
    refresh();
  }
  function removeFund(code) {
    state.selected = state.selected.filter((s) => s.code !== code);
    renderChips();
    refresh();
  }

  function renderChips() {
    $("chips").innerHTML = state.selected.map((s, i) => `
      <span class="chipfund"><span class="dot" style="background:${COLORS[i % COLORS.length]}"></span>${esc(s.name)}
        <button data-code="${s.code}" title="Remove">×</button></span>`).join("");
    $("chips").querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => removeFund(+b.dataset.code)));
  }

  async function loadSuggest() {
    const q = qInput.value.trim();
    if (!q) { hideSuggest(); return; }
    try {
      const items = await api("/api/suggest?q=" + encodeURIComponent(q) + "&limit=8");
      if (!items.length) { hideSuggest(); return; }
      sugItems = items; sugActive = -1;
      sugBox.innerHTML = items.map((it, i) => `
        <div class="suggest-item" data-i="${i}">
          <div>
            <div class="nm">${esc(it.scheme_name)}</div>
            <div class="mt">${esc(it.amfi_amc_name)} · ${esc(it.option_type)} · ${it.amfi_scheme_code}</div>
          </div>
        </div>`).join("");
      sugBox.style.display = "";
      sugBox.querySelectorAll(".suggest-item").forEach((el) =>
        el.addEventListener("mousedown", (e) => { e.preventDefault(); addFund(sugItems[+el.dataset.i]); }));
    } catch (e) { hideSuggest(); }
  }

  qInput.addEventListener("input", () => { clearTimeout(sugTimer); sugTimer = setTimeout(loadSuggest, 180); });
  qInput.addEventListener("keydown", (e) => {
    const open = sugBox.style.display !== "none";
    if (e.key === "ArrowDown" && open) { e.preventDefault(); sugActive = Math.min(sugActive + 1, sugItems.length - 1); paintActive(); }
    else if (e.key === "ArrowUp" && open) { e.preventDefault(); sugActive = Math.max(sugActive - 1, 0); paintActive(); }
    else if (e.key === "Enter") { e.preventDefault(); if (open && sugActive >= 0) addFund(sugItems[sugActive]); }
    else if (e.key === "Escape") hideSuggest();
  });
  qInput.addEventListener("blur", () => setTimeout(hideSuggest, 150));
  function paintActive() {
    sugBox.querySelectorAll(".suggest-item").forEach((el, i) => el.classList.toggle("active", i === sugActive));
  }

  // ---- chart + table ----
  async function refresh() {
    const has = state.selected.length > 0;
    $("chart-card").style.display = has ? "" : "none";
    $("table-card").style.display = has ? "" : "none";
    if (!has) return;
    const codes = state.selected.map((s) => s.code).join(",");
    const data = await api(`/api/compare?codes=${codes}&years=${state.years || ""}`);
    renderChart(data);
    renderTable(data);
  }

  function renderChart(data) {
    // union of all dates -> shared category axis; each fund aligned (nulls for gaps)
    const dates = [...new Set(data.funds.flatMap((f) => f.points.map((p) => p.date)))].sort();
    const datasets = data.funds.map((f, i) => {
      const map = {};
      f.points.forEach((p) => { map[p.date] = p.value; });
      const color = COLORS[i % COLORS.length];
      return {
        label: f.fund.scheme_name,
        data: dates.map((d) => (map[d] != null ? map[d] : null)),
        borderColor: color, backgroundColor: color, borderWidth: 1.8,
        pointRadius: 0, tension: 0.05, spanGaps: true,
      };
    });
    if (chart) chart.destroy();
    chart = new Chart($("cchart"), {
      type: "line",
      data: { labels: dates, datasets },
      options: {
        responsive: true, maintainAspectRatio: true,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "top", labels: { color: "#5f6368", boxWidth: 10 } },
          tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y == null ? "—" : c.parsed.y.toFixed(2)}` } } },
        scales: {
          x: { ticks: { maxTicksLimit: 8, color: "#5f6368" }, grid: { display: false } },
          y: { ticks: { color: "#5f6368" }, grid: { color: "#eef0f2" } },
        },
      },
    });
  }

  function renderTable(data) {
    const horizons = ["1M", "3M", "6M", "1Y", "3Y", "5Y"];
    const heads = data.funds.map((f, i) =>
      `<th><span class="fdot" style="background:${COLORS[i % COLORS.length]}"></span>${esc(f.fund.scheme_name)}</th>`).join("");
    const rows = horizons.map((h) => {
      const cells = data.funds.map((f) => {
        const v = f.returns ? f.returns[h] : null;
        if (v == null) return "<td>—</td>";
        const up = v >= 0;
        return `<td style="color:var(--${up ? "up" : "down"});font-variant-numeric:tabular-nums">${up ? "+" : ""}${v.toFixed(2)}%</td>`;
      }).join("");
      return `<tr><td>${h}</td>${cells}</tr>`;
    }).join("");
    $("ctable").innerHTML = `<table class="cmp"><thead><tr><th>Return</th>${heads}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  document.querySelectorAll("#c-ranges button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#c-ranges button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.years = b.dataset.y === "" ? null : parseFloat(b.dataset.y);
      refresh();
    }));

  // deep link: /compare?codes=123,456
  const pre = new URLSearchParams(location.search).get("codes");
  if (pre) {
    const codes = pre.split(",").map((c) => parseInt(c, 10)).filter((c) => Number.isFinite(c)).slice(0, 4);
    Promise.all(codes.map((c) => api("/api/funds/" + c))).then((funds) => {
      funds.forEach((f) => state.selected.push({ code: f.amfi_scheme_code, name: f.scheme_name, amc: f.amfi_amc_name }));
      renderChips();
      refresh();
    }).catch(() => {});
  }
})();
