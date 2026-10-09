# MFDataIndia — Improvements / Backlog

Version-controlled backlog of planned improvements. Each item states the
*why*, the *what*, and how it fits the source-of-record strategy below.
`RESEARCH.md` holds the source research; `README.md` documents what ships
today. This file is the forward-looking "next" list.

## Source-of-record strategy (decision, 2026-10-08)

**Official sources only in prod:** AMFI (NAV + identity) and each AMC's own
disclosures (factsheet + monthly portfolio). Aggregators (**Scripbox /
Groww**) are **dev-only tools** — never scheduled or auto-triggered in prod.

- If an aggregator value is ever needed, the pattern is: **fetch in dev →
  produce a diff (only new/changed values) → insert just that diff into
  prod.** Fragile / ToS-sensitive scraping stays confined to dev.
- Initial prod load is a **pure DB migration** (`pg_dump`/`pg_restore`) of the
  existing dev dataset — prod is not rebuilt from source.

This backlog is what makes "official sources only" fully achievable.

---

## P0 — AMC monthly-portfolio parser (full holdings)  ⭐

**Why (the one real gap):** `fund_holdings` (228,185 rows; 3,724 funds; up to
~1,500 rows for index funds) is the *full* portfolio, but **today only the
Groww + Scripbox crawlers keep it current** (`groww_to_holdings` = full list;
`scripbox` = top-10). If those connectors stop running, holdings go **stale**
(not deleted) month over month, because portfolios turn over monthly.

**What the official source is:** each AMC's **monthly portfolio disclosure** —
the SEBI-mandated document listing *every* security (name, quantity, value,
weight). It is a separate "Portfolio / Monthly-Portfolio" download, distinct
from the monthly factsheet. We already have the **directory of all 53 AMCs'**
portfolio links in `research/amfi_amc_directory.json`; the parser does not
exist yet.

**The what:** build an AMC-portfolio parser (one class per AMC, same design as
the factsheet parser in `mfdataindia/ingest/amc_factsheets/`) that:
- downloads the AMC's latest monthly portfolio disclosure,
- parses the full per-scheme security list,
- **replaces** `fund_holdings` for that scheme (a full snapshot — a wholesale
  replace per fund is correct, unlike the aggregator top-10),
- records provenance (AMC, portfolio period, as-of date, source URL).

**Payoff:** full holdings become an **official source**. This is what lets us
retire Groww/Scripbox for holdings with no loss (see P3).

**Ordering:** P0 should land *before* we stop running the aggregator crawlers
in dev, so there's never a window where full holdings have no refresh path.

---

## P1 — Extend the AMC factsheet parser to the other AMCs

ABSL is done (POC: beta/Sharpe/std-dev; 17 fills, verified against a pre-run
pg_dump). The other 52 AMCs each have a **different factsheet layout**, so this
is incremental (parser class per AMC), not a one-off.

- **Start with the big-5** (SBI, ICICI Prudential, HDFC, Kotak, Nippon) —
  they cover the most funds.
- Same POC-then-fill discipline per AMC (parse → dry-run → verify against a
  pre-run `pg_dump` → fill).
- Expect a similar share of **published-NA** for debt funds (ABSL: 28 of 67).
- Factsheet fields available beyond risk metrics (AUM, base ER, inception,
  benchmark, manager, CAGR returns, exit load, riskometer, scheme rating,
  **turnover**, top-10, asset/sector/credit allocation, duration) — the v1
  field set fills only beta/Sharpe/std-dev; widen it per AMC as layouts allow.

## P2 — Aggregator-only fields

A small set of fields are not in a single-month factsheet and are currently
Groww-only: `registrar_agent`, `expense_ratio_history` (~84 funds each).
`portfolio_turnover` (48) may also be Groww-only today, but the factsheet
carries turnover too, so it is likely closable via P1. Decide per field:
(a) close via the AMC factsheet, (b) keep a **thin, dev-only** Groww fetch +
diff-push, or (c) drop the column.

## P3 — Retire Groww/Scripbox

**Done (2026-10-08):**
- Connectors **archived** under `archived/aggregators/` (scripts, jobs,
  clients, loaders, test) so they cannot be accidentally triggered. The
  `make enrich-scripbox` / `enrich-groww` / `enrich-groww-all` targets are
  removed, and `bootstrap_local.py` no longer loads bundled Scripbox evidence.
- **Aggregator references purged from the DB** (migration 013, compliance —
  we do not hold/disseminate the aggregator's own identity/provenance):
  - dropped `fund_facts` cols: `scripbox_fund_id`, `groww_slug`,
    `groww_rating`, `groww_return_stats`, `groww_fetched_at`,
    `groww_source_mode`, `groww_inherited_from`, `raw_payload`, `fund_slug`,
    `fund_variant` (superseded by AMFI-derived `mf.fund_variants`)
  - `fund_facts.source` `SCRIPBOX`/`GROWW` → neutral `LEGACY` (3,943 rows)
  - dropped `mf.fund_opinions` (aggregator editorial artifact; not in API/UI)
  - purged 13,943 dead `GROWW`/`SCRIPBOX` `ingest_checkpoints` rows
  - stripped the `"source":"GROWW"` key from `holdings_analysis` jsonb;
    rebuilt `fund_facts`/`source_metadata`/`funds` CHECKs without aggregator
    names; recreated `v_fund_data_status` + `v_enrichment_coverage` cleanly
- **Our own fund identity** `mf.fund_family` (1,906 families): `tlws_mf_id`
  (deterministic UUIDv5 of `group_key`), `slug`, `tags` — for related-news
  retrieval and stable URLs. Built by `scripts/build_fund_family.py`
  (`make build-family`, idempotent), returned as `family` on
  `/api/funds/{code}`.
- Verified: zero `scripbox` strings in any `mf` table/column/value/constraint;
  `groww` remains only as a **fund house** (its 217 funds, holdings of its
  funds, its benchmark indices, manager bios) — legitimate data, kept.

**Remaining (depends on P0):** replace the aggregator *refresh* of
`fund_holdings` with the AMC monthly-portfolio parser, so the archived
crawlers are no longer the only thing keeping full holdings current.

---

## UI: dark mode + front-page category movers (done, 2026-10-08)

- **Dark mode** on all three pages (index / fund / compare). The theme is
  applied before first paint by an inline head snippet (no flash); the topbar
  toggle persists to `localStorage` and defaults to the OS preference.
  Everything is driven by CSS custom properties — the dark palette is one
  `:root[data-theme="dark"]` block. Chart.js canvases bake colors in at build
  time, so `fund.js`/`compare.js` read `--chart-tick`/`--chart-grid`/
  `--chart-fill-border` at draw time and re-run their init on toggle
  (`window.__mfdThemeChanged`).
- **Front page** now leads with **Top movers by category**: one tile per broad
  family (Equity, Debt, Hybrid, Index, ETF, FoF, Solution) with a per-tile
  **Gainers/Losers toggle (default Gainers)** showing top-5 for the selected
  period (1D/1W/1M/3M/1Y), above the existing filters. Backed by
  `GET /api/movers/categories` — one window scan (same semantics as
  `/api/movers`), grouped in Python by `queries.fund_family()` which maps
  AMFI's mixed current/legacy `scheme_category` strings to families.
- **Variant collapsing:** plan/option variants of one scheme (GROWTH/IDCW, and
  multiple IDCW payout periodicities — each its own AMFI code) move
  identically and would stack the list (e.g. 3× "BANK OF INDIA ARBITRAGE
  FUND"). `category_movers` collapses them onto one row per
  `mf.fund_variants` family: the biggest absolute mover is the representative
  (GROWTH wins ties), with a `variants` count rendered as a "×N variants"
  badge. Tile fund counts are distinct schemes (Hybrid 444 → 189).
- Known data artifact (pre-existing, same in `/api/movers`): matured close-ended
  FMPs show their NAV reset-to-par at maturity as a large "loser" (e.g. an HDFC
  FMP that matured within the window).

## UI: fund-detail page reorganized into sections (done, 2026-10-08)

The fund page was one flat column of 15 equal-weight cards (~5,167px tall) with
no way to jump, and the most basic facts (AUM, expense ratio, benchmark,
manager, inception, risk) were buried ~75% down, *below* the SIP calculator and
two returns charts. Reorganized into **five labelled sections** with a **sticky
section nav** (all 15 components kept):

- **Overview** — a compact **key-facts strip** (AUM, expense ratio, benchmark,
  fund manager, inception, risk) moved to the top, then the NAV chart (hero)
  and the 1M–5Y returns chips.
- **Performance** — returns breakdown (calendar-year bars + month-by-year
  heatmap) and rolling returns.
- **Risk & Peers** — risk & behaviour (drawdown/volatility/Sharpe/Sortino/
  Calmar/win-rate), category standing, category risk-reward map, debt profile.
- **Holdings** — holdings analysis (asset-class + sector donuts) and top
  holdings.
- **Details & tools** — **collapsed by default** behind a toggle: the full
  24-field key-facts grid, the SIP/lumpsum projection calculator, expense-ratio
  history, and plan variants.

Mechanics: the nav is `position: sticky` under the topbar (offset 66px) and
highlights the section in view via a scroll-spy (`fund.js#setupFundNav`); nav
links jump using CSS `scroll-behavior: smooth` + `scroll-margin-top` on each
section (no click handler needed). Because the SIP + expense-ratio charts are
created while their section is `hidden` (0 width), `setupDetailsToggle` calls
`chart.resize()` on expand so they render at full size. Verified headlessly:
nav/sections/strip present, scroll-spy tracks Overview→Risk→Holdings, nav
click scrolls, Details collapse/expand resizes the charts, and dark mode is
clean. Default page height drops 5,167px → 3,853px (Details collapsed).

## API: batch fund lookup (done, 2026-10-08)

- `GET /api/funds/batch?ids=100033,INF209K01LV0,999999` — one comma-separated
  list mixing **AMFI scheme codes and ISINs**, max **50** per request (51+ →
  422, empty → 422). Numeric inputs are codes; anything else is upper-cased and
  matched against either ISIN column. Returns funds in request order,
  de-duplicated, each tagged with `matched_by` (every input that resolved to it)
  plus a `not_found` list of inputs that resolved to nothing. Per fund: identity
  + `isin_primary`, latest NAV + date, AUM / expense ratio / 5y return / sharpe
  / beta / risk level / manager / benchmark, `in_scope`, `is_defunct`.
- Registered **before** `/api/funds/{code}` — FastAPI matches in registration
  order and "batch" would 422 the int `{code}` parameter otherwise.

## API: variant-group facts fallback + family default (done, 2026-10-09)

**Why:** same-named schemes exist under multiple AMFI codes (re-issues), and
AMC factsheets attribute fund-level facts to only *some* of the codes in a
`fund_variants` group. E.g. "Franklin India Liquid Fund" has 13 codes; facts
live on `100538`/`100546`/`100547`/`100548` but not on the newer `139889`
tranche, so `/fund/139889` rendered a blank facts sheet even though the fund
itself was fully enriched. The fund list also showed all 13 rows.

- `GET /api/funds/{code}`: when the exact code has no usable fund-level facts
  (no row, or a row where AUM / expense ratio / benchmark / benchmark_name /
  manager / inception are all NULL), the facts are borrowed from a sibling
  restricted to re-issues of the **same plan/option/periodicity within the same
  AMC and scheme category** (lowest AMFI code among those). `group_key` alone
  is not trusted: it is a documented heuristic that can collide across AMCs,
  and cross-plan/option/periodicity borrowing would leak variant-specific
  facts. Only the **family-safe fund-level fields** (AUM, expense ratio,
  benchmark, manager, inception, classification, ratings) are borrowed; per-
  scheme fields (SIP, transaction status, exit load, published returns, risk
  ratios) always stay the code's own. The response gains `facts_source_code`
  (borrowed code, `null` when the code has its own facts).
- `GET /api/funds/batch`: same fallback per code, resolved in one batched
  window-function query (no N+1); each fund gains `facts_source_code`. The
  "has its own facts" test is one shared predicate (`_FACTS_IDENTITY_FIELDS`)
  across detail, batch, and both sibling SQL filters, so the endpoints agree.
- Fund list defaults to scheme-family grouping: the index page now uses
  `/api/fund-families` by default (one row per family, `+N variants` badge,
  representative = GROWTH/REGULAR lowest code). The family identity is
  `group_key` **plus AMC and scheme classification**, so same-named funds from
  different AMCs stay separate and `variant_count` never mixes AMCs. The
  "Group by scheme family" toggle and `?family=0` opt back into per-variant
  rows. `/api/funds` itself is unchanged — de-dup is served at the API level,
  not the UI.
- Fund detail shows a disclosure note in the Details section when facts are
  borrowed: "Fund-level facts are shown from sibling scheme #… (same fund
  family)".
- Verified: `/fund/139889` (REG/GROWTH) shows AUM ₹6,082 cr, ER 0.20%,
  benchmark, manager, inception, risk (borrowed from `100538`, a same
  plan/option re-issue) while keeping its **own** Sharpe and returns and
  **not** inheriting `100538`'s exit load / SIP; a Direct plan of the same
  family does **not** borrow Regular-plan facts; a Monthly IDCW does **not**
  borrow a Quarterly sibling's facts.
- Tests: `tests/test_variant_facts_fallback.py` — safe on populated DBs
  (900xxx code range, single always-rolled-back transaction, fails if the
  identifiers pre-exist; verified zero row-count change on the 14,368-scheme
  dev DB): exact-code unchanged, same-plan/option re-issue picked, Direct never
  borrows Regular (detail + batch), code-specific fields (SIP / exit load /
  returns) never inherited, all-NULL row treated as missing, sibling usable via
  expense_ratio alone, own per-code Sharpe survives the borrow, Monthly IDCW
  never borrows a Quarterly sibling (detail + batch), same-named funds from
  different AMCs stay separate families, no-group no-facts stays blank,
  rollback leaves no rows.

## Schema: `apply_migrations` now equals a fresh compose boot (done, 2026-10-08)

- `DEFAULT_MIGRATIONS` was missing 008 and 010–013, so any DB built via
  `apply_migrations` (the integration-test path) lacked `mf.fund_family` and
  still carried the purged aggregator columns + `fund_opinions` — i.e.
  non-compliant. All current migrations (except superseded 007) are now core,
  so any such DB ends in exactly the same schema as a fresh compose boot
  (whose initdb runs every file in `sql/`).
- The stale `test_migrations_are_idempotent` (hardcoded 3-file list; a full
  re-apply is not a supported operation once the one-way purges 012/013 exist)
  was replaced with a test that a fresh apply yields the complete post-purge
  schema (fund_family present, every aggregator column/table gone, key views
  intact).

---

## Cross-cutting (not source-specific)

- **CI:** GitHub Actions running the unit suite on push (none today).
- **Monitoring:** data-freshness alert (weekday with `max(nav_date)` > 2 days
  old) + uptime check + structured logs (prod plan).
- **Backups:** nightly `pg_dump -Fc` off-box + periodic restore test (prod
  plan).

> Deployment specifics (host, Dockerfile, compose, TLS, schedulers, backups)
> live in the prod-deployment plan, not here — this file is the data /
> improvement roadmap.
