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

## P1 — NAV-series integrity and scheme lifecycle (open, 2026-10-10)

**Why:** the partner research UI renders whatever `nav_history` holds, and a
triage of the populated database found series that are structurally impossible
plus a scheme lifecycle the schema does not model. Both are visible to a
distributor as a wrong chart, not as a warning.

Measured on the 14,368-scheme dev database:

- **Frozen series — 8 codes** have >250 NAV points and **exactly one distinct
  value**. `139891` / `139892` (Franklin India Liquid Fund, REGULAR GROWTH and
  IDCW) sit at precisely `10.0000` for 1,525 consecutive observations
  (2021-10-04 → 2026-10-07). They render a flat line, 0.00% returns, and can
  never move in `movers`. Suspected ingest vector: the next-day liquid/overnight
  NAV path PR #19 (`fa2e2cd`) just tightened — worth re-checking whether these
  rows predate that guard.
- **Duplicated series across options:** `139889` (GROWTH) and `139890` (IDCW)
  carry byte-identical series (13.2720 → 18.1002, same 1,525 dates). An IDCW
  option that never diverges from its Growth twin is not a real payout history.
- **One name, many funds:** `Franklin India Liquid Fund` is 13 AMFI codes under a
  single `fund_variants.group_key`, with latest NAVs from ₹10.00 to ₹6,339.87.
  The family view collapses them into one row whose representative is whichever
  variant the pick rule chose, so "the same fund" is one arbitrary code.
  **Corrected earlier flag:** `100538` at ₹6,339.87 is *legitimate* — 1,830
  distinct daily values accreting 4,820.63 → 6,339.87 is what a long-running
  liquid fund looks like. The anomalies are the frozen/duplicated rows above.
- **Maturity is not modelled:** FMP `151566` ends at `10.0000` on 2026-09-09
  after `12.5916` the previous session — a −20.58% single-day move that is
  actually the redemption at face value. It is still `is_active = true`,
  `is_defunct = false`, so it is served as a live fund and its "Growth of
  ₹10,000" chart ends in a cliff. **191 of 4,291** served codes have no NAV in
  30+ days (168 in 180+).
- **IDCW metrics:** 2,324 of the 4,291 served codes are IDCW options, and every
  return / CAGR / Sharpe / drawdown is computed NAV-to-NAV from post-payout NAV.
  A payout therefore reads as a loss (the FMP sawtooth). Needs a decision
  before code: build a payout-rebased series, restrict risk metrics to
  Growth/IDCW-Reinvestment variants, or disclose "NAV-based, excludes dividends"
  and suppress the maturity reset.
- **Buyability is stored but not surfaced:** `fund_facts.status` /
  `transaction_status` are populated (`ACT/ALL` 3,461 · blank 409 · `ACT/TSUSP`
  260 · `TER/ALL` 41 · `SUSPACT/SUSP` 31 · `INC` 7) but no endpoint exposes them
  as a first-class "can a client transact in this today" signal.

**The what:**
1. A NAV-integrity check in `scripts/refresh_daily.py` (and a one-off audit
   script): flag zero-variance series above a point threshold, and identical
   series across two option variants of one family.
2. Derive a maturity/closed signal for `CLOSE_ENDED` schemes (last NAV far
   behind the dataset reference, or a terminal NAV reset back to face value) and
   expose it in `/api/funds/{code}` so consumers can badge "matured" instead of
   charting it as a crash.
3. Expose `status` / `transaction_status` through the detail payload as an
   explicit buyability flag.
4. Decide the IDCW methodology question above, then apply it consistently to
   charts, returns, analytics and movers.

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
  The batch borrow is restricted to the family-safe subset of its fact keys
  (`_BATCH_BORROW_FACTS_KEYS`), so `return_5year`/`sharpe_ratio`/`beta` —
  per-code NAV-series metrics — are never inherited; and `benchmark_name` is
  projected as `COALESCE(benchmark_name, benchmark)` (own row and borrowed
  row), so a raw-benchmark-only row is treated as complete AND still surfaces
  its benchmark.
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
  different AMCs stay separate families, batch never inherits per-scheme
  performance (Sharpe/return_5year/beta), a raw-benchmark-only row is treated
  as complete and surfaced via benchmark_name (COALESCE) in batch, no-group
  no-facts stays blank, rollback leaves no rows.

## API: plan scope — Regular is the served universe (done, 2026-10-10)

**Why:** this data feeds a distributor-facing product (TrillionInsights
partner research). Direct plans are a different commission model, not a product
choice, so a partner must never be *offered* one — yet the fund-detail sibling
navigation listed all three Direct variants of a scheme next to the Regular
ones, and any consumer that disabled `in_scope` (or typed a Direct code into the
typeahead) could surface them.

`mf.funds.in_scope` is a STORED generated column
(`plan_type = 'REGULAR' OR (plan_type = 'UNLABELLED' AND NOT is_etf AND
plan_source = 'NAME')`) that already excludes Direct, so **no existing response
changed** — but `in_scope` is a *curation* flag that `/api/funds` exposes as an
option and the sibling list does not apply at all. "We never show Direct" now
states itself instead of riding on an expression defined for another purpose.

- New policy in `queries.py`: `DEFAULT_PLAN_SCOPE = "regular"`, a closed
  `_PLAN_SCOPE_SQL` map of scope → SQL predicate template (`regular` → the
  served-plan rule, `direct` → `plan_type = 'DIRECT'`, `all` → `TRUE`),
  `plan_scope()` (normalises, raises `ValueError` on anything unknown) and
  `_plan_predicate()` (binds `{alias}` from call-site constants only, never from
  input). `RETAIL` / `INSTITUTIONAL` are excluded: a distributor sells neither.
- `plan` query parameter (default `regular`) on the discovery and navigation
  routes: `/api/funds`, `/api/fund-families`, `/api/funds/{code}` (scopes the
  `siblings` list), `/api/suggest`, `/api/movers`, `/api/movers/categories`.
  Resolved by `_plan_scope_or_422` **before** any DB work, so a typo is a 422
  rather than a silently widened or narrowed universe.
- Always scoped (no parameter, because widening them would be a footgun): peer
  sets in `/api/funds/{code}/peers` and `/risk-reward` (a percentile must not
  compare a Regular fund against Direct peers on the same book), and the facet
  counts `/api/amcs`, `/api/categories`, `/api/options` (a count must never
  promise rows the list will not return).
- Deliberately **not** filtered: explicit-identifier lookups —
  `/api/funds/{code}`, `/nav`, `/returns`, `/analytics`, `/api/funds/batch`,
  `/api/compare`, `/api/holdings-overlap`. A code is an identifier, and hiding
  one the caller named would be a lie; they all return `plan_type` so the
  consumer can badge it. The consequence is a useful one: opening a Direct code
  still works, and every hop it offers leads back into the served universe.
- **Typeahead ranking bug found and fixed while here:** the exact-match sort key
  was `(code = q OR isin_primary = q)`. `isin_primary` is NULL for schemes
  without an ISIN, `FALSE OR NULL` is NULL, and Postgres sorts NULL **first**
  under `DESC` — so all 19 live ISIN-less codes (every FMP / close-ended
  re-issue) jumped above far more relevant matches on every query. That is
  exactly why "hdf" offered *HDFC FMP 1269D March 2023* before *HDFC Aggressive
  Hybrid Fund*. Wrapped in `coalesce(..., false)`, plus a final
  `amfi_scheme_code` tie-break so same-named variants no longer come back in
  arbitrary order.
- Verified live against the 14,368-scheme dev database: `/api/suggest?q=hdf`
  now leads with HDFC Aggressive Hybrid Fund / Arbitrage / Balanced Advantage
  (all Regular); `/api/funds/151566` returns 3 siblings (was 6, incl. 3 Direct);
  `/api/funds/151567` (Direct) still resolves but offers only its Regular twins;
  `/api/funds?in_scope=false` returns 9,873 rows with zero Direct where
  `plan=all` returns 14,127; `plan=bogus` → 422. Totals for the served universe
  are identical before/after (4,291 funds, 1,821 families).
- Tests: `tests/test_plan_scope.py` (populated-DB safe: 9002xx range, AMC
  900002, one always-rolled-back transaction, fails if identifiers pre-exist) —
  default lists no Direct/Retail even with `in_scope=False`, `plan=all` widens,
  `plan=direct` narrows, blank-plan feed row is not pulled back in, family view
  is Regular-represented with `variant_count` from served variants only,
  siblings exclude Direct (and include it only when asked), a Direct page offers
  Regular twins, suggest never returns Direct and cannot resolve a Direct code,
  the ISIN-less scheme no longer ranks first, ordering is deterministic, movers
  and category movers honour the scope where `in_scope` alone would not, facet
  counts use the served scope, **populated-DB zero-change guard**, rollback
  leaves no rows. Pure policy + 422 tests in `tests/test_queries.py`;
  OpenAPI documentation of `plan` asserted in `tests/test_openapi_contract.py`.
- Contract: `contracts/mfdataindia-openapi-v1.json` re-exported (66 added lines
  = the `plan` parameter on six routes; no path or response-shape change, so
  consumers that do not send `plan` are unaffected).

**Review round 1 — [P1] the scope admitted plan-*unknown* rows (fixed same day).**
`regular` was written as `plan_type IN ('REGULAR', 'UNLABELLED')`, but
`UNLABELLED` is not one population. `resolve_plan` (`amfi_navall.py`) returns it
for three genuinely different reasons — `NAME` (legacy feed had *no* Plan column,
so the name is the only signal and unlabelled means "written without the word
Regular"), `COLUMN_BLANK` (the feed **has** a Plan column and left it empty: the
plan is unknown) and `COLUMN_UNRECOGNISED` (a Plan cell we cannot classify) — and
the rule additionally exempts ETFs. Measured on the live feed, the shortcut
admitted **10,050 rows where the authoritative rule admits 4,345**: 5,705
`UNLABELLED + COLUMN_BLANK`, 5,573 of them still live. It doubled the served
universe, and it surfaced exactly where this PR deliberately bypasses curation
(`/api/funds?in_scope=false`, sibling navigation) — which also explains why the
zero-change guard never caught it: every *default* response already filters
`in_scope`, so the column masked the predicate's own error.

Fix: `_SERVED_PLAN_SQL` reproduces the generated column verbatim. Three guards
keep the two definitions from drifting, because the scope intentionally cannot
read `in_scope`:

1. `test_regular_scope_admits_exactly_what_curation_admits` evaluates the emitted
   predicate against the column with `IS DISTINCT FROM` over every row visible in
   the test transaction — the synthetic group on CI, all 14,368 rows locally.
   This is the behavioural guard.
2. `test_regular_scope_predicate_matches_the_generated_column_text` reads the
   `GENERATED ALWAYS AS (…) STORED` expression out of `sql/001_core_schema.sql`
   and compares it with the test's own independent transcription of the rule.
3. The fixture now carries one row per UNLABELLED sub-case — `900205`
   COLUMN_BLANK, `900209` COLUMN_UNRECOGNISED, `900208` NAME-but-ETF — plus
   `900210` REGULAR-but-ETF to prove the carve-out was not over-applied. Listing,
   typeahead and sibling navigation now assert the exact served code set, in both
   `in_scope=True` and `in_scope=False` modes.

Negative control: reverting the predicate to the reviewed-against form fails
exactly three tests — the parametrised `in_scope=False` listing, sibling
navigation, and the equivalence guard — so the new assertions are proven to catch
the reported bug rather than merely passing alongside it.

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
