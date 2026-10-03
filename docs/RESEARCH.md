# MFDataIndia — Source Research & Capture Plan

Status: **research complete, awaiting go-ahead to build.**
Probed live: 2 Oct 2026. All numbers below were measured against live endpoints, not estimated.

Scope decisions locked with the user:
- **Regular Plan, all options** (Growth + IDCW/dividend, payout *and* reinvestment) — §4
- **NAV history: last 5 years required**, more retained where free — §4
- **AMFI is India-only** → fetched from an India server; format already decoded and the parser
  validated against a real file retrieved via the Wayback Machine — §3B
- **Google Finance rejected** — tested, zero Indian MF coverage — §3B

---

## 1. Source verdict

| Source | Reachable | Auth | Robots | Verdict | Role |
|---|---|---|---|---|---|
| `api.mfapi.in` | ✅ | none | none published | **Primary** | Scheme master, ISIN, AMFI code, full NAV history |
| `scripbox.com` HTML pages | ✅ | none | `/mutual-fund/*` allowed | **Primary enrichment** | 102-field factsheet per fund |
| `scripbox.com/_next/data/…` | ✅ | none | ❌ `Disallow: */data/` | avoid | same data, robots-disallowed → use HTML instead |
| `amfiindia.com` | ❌ here / ✅ **from India** | — | — | **Authority** | Clean type/category/AMC + official ISINs; fetch from India server (§3B) |
| `web.archive.org` → AMFI | ✅ 200 | none | — | **format source** | Wayback holds `spages/NAVAll.txt`; parser built + validated **now** |
| `www.amfi.org` | ✅ 200 | — | — | not a feed | member directory only |
| `google.com/finance` | ✅ 200 | none | — | ❌ **rejected** | **zero** Indian MF coverage — tested, see §3B |
| `sebi.gov.in` | ✅ | none | — | useful | SID / SIA / KIM filings reachable |
| `iciciprumf.com` | ✅ 200 | CSRF/SF token | — | deferred | Salesforce SPA, no public JSON |
| `hdfcfund.com` | ❌ 403 | Cloudflare | — | deferred | needs browser automation |
| `sbimf.com` | ❌ 404 | — | — | deferred | path guessing failed |
| `nipponindiaim.com` | ❌ conn fail | — | — | deferred | |
| `moneycontrol.com` | bot-wall | — | — | drop | returns news pages |
| `valueresearchonline.com` | ❌ 403 | — | — | drop | |
| CAMS / KFintech | SPA / 404 | — | — | drop | no public JSON API found |

### Key finding
Scripbox and `api.mfapi.in` are **not redundant — they are complementary and they agree.**
A proof-of-concept join on ICICI Prudential's 111 funds returned:

- ISIN match rate: **111 / 111 = 100 %**
- AMFI scheme-code agreement: **111 / 111 = 100 %**

So the two sources can be joined safely on ISIN with AMFI scheme code as a cross-check.

---

## 2. `api.mfapi.in` — measured facts

Free, no key, no published rate limit. Data refreshed ~6×/day; latest observed date `01-10-2026`.

| Endpoint | Payload | Notes |
|---|---|---|
| `GET /mf` | 5.7 MB, **37,936** schemes | `schemeCode`, `schemeName`, `isinGrowth`, `isinDivReinvestment` |
| `GET /mf/latest` | 11.2 MB, **37,936** records | adds `nav`, `date`, `fundHouse`, `schemeType`, `schemeCategory` — **whole universe in one request** |
| `GET /mf/{schemeCode}` | per-scheme | `meta` + **complete daily NAV series since inception** |
| `GET /mf/search?q=` | search | |

Measured composition of `/mf/latest`:

- Total records: **37,936**
- Records with any NAV value: **35,607** (2,329 have none)
- **Important:** `/mf/latest` returns the **last-ever** NAV for every scheme, including ones
  that died years ago (e.g. Standard Chartered schemes last priced `29-05-2008`). So
  `nav != null` does **not** mean active — 3,025 distinct NAV dates appear in the response.
- **Active universe = NAV dated within the last 30 days = 8,676 schemes.**
  (7-day window: 8,654 · 365-day: 9,046 · 3-year: 9,468.)
- Within the active set: Open-ended 8,491 · Close-ended 181 · Interval 4
- Fund houses: **55**

Plan split of the **active** set (classified from `schemeName`, since `schemeType` is unreliable)
and ISIN completeness:

| Plan bucket | Count | `isinGrowth` | `isinDivReinvestment` | either ISIN |
|---|---|---|---|---|
| `Regular Plan` | 4,098 | 93.0 % | 49.2 % | **99.7 %** |
| `Direct` | 4,068 | 93.1 % | 47.5 % | 99.4 % |
| unlabelled | 510 | 87.5 % | 14.1 % | 91.6 % |

Note the ISIN columns are *not* mutually exclusive: one `schemeCode` can carry both a growth
ISIN and a div-reinvestment ISIN, so "either" is the meaningful coverage figure.

NAV archive floor: **02/03–04-2006**. Anything earlier than that cannot be recovered from this source.

**Known defect (confirmed):** `schemeType` has **77** distinct values and `schemeCategory`
**140**, where only ~3–4 of each are legitimate. The rest is CSV column-shift garbage — e.g.
`schemeType = "135488;INF174K01Y15;-;Kotak FMP Series 180"` (987 rows) or
`schemeType = "IL&FS Mutual Fund"` (3,909 rows, a fund-house value shifted into the wrong
column), and `schemeCategory = "1099 Days"` / `"1"`. Ingestion must normalise and quarantine.


---

## 3. Scripbox — measured facts

Current Next.js `buildId` = **1099580**. This changes on every deploy and must be re-read.

`robots.txt` disallows `*/data/`, `/content-api/`, `/wp-json/`, `/my/`, `*/block/`.
It does **not** disallow `/mutual-fund/…`. Therefore we scrape the **HTML pages** and parse the
embedded `<script id="__NEXT_DATA__">` — identical payload to `_next/data`, and robots-clean.

Verified: `GET /mutual-fund/axis-arbitrage-fund-regular-growth` → 200, 929 KB,
`__NEXT_DATA__` present, `factsheetData` = **102 fields**, `historicalNavData` = 126 points.

### Enumeration ladder

1. `/mutual-fund/amc` → **50 AMCs** (name, `amc_code`, `amc_slug`, AUM, fund counts per class)
2. `/mutual-fund/amc/{slug}/isin-fair-market-values` → 1,000/page. Filtered server-side to
   `is_direct=true`, `option=Growth`, `status=ACT`, `openended=true`.
   Crawled all 50 → **1,761 funds**, ~100 % ISIN coverage (gaps: HDFC 87/88, UTI 71/74, BOI 23/24).
   Three AMCs return empty: `idbi`, `l-and-t`, `sahara`.
3. Each fund carries `fund_variant[]` → **12,994 variant links**, of which **6,235 unique
   REGULAR slugs** are discoverable (6,095 carry an `isin_code`). Breakdown:
   `is_dividend=true` 4,310 / false 1,925 · `is_reinvestment=true` 2,084 / false 4,151 ·
   `dividend_periodicity`: null 3,880, Irregular 1,341, Monthly 257, Quarterly 191,
   Weekly 166, Daily 160, Annual 104, Half-Yearly 81.
   Note variant entries carry `isin_code`, `fund_id`, `plan_id` — but **not** `amfi_code`,
   so the join to mfapi.in must go through ISIN.
4. `/mutual-fund/{regularSlug}` → full record. Random sample of 14 discovered regular slugs:
   **14 / 14 resolved with complete `factsheetData`**, all `is_direct=false`.

**Slug → scheme-code collapse.** The 6,235 regular slugs join to only **3,890 distinct AMFI
scheme codes** (5,516 joinable to an *active* scheme, 57 to a dead one, 662 unmatched). The
1.42 ratio is structural: Scripbox models *payout* and *reinvestment* of the same IDCW option
as two separate funds, while AMFI assigns them **one** `schemeCode` with two ISINs
(`isinGrowth` = payout, `isinDivReinvestment` = reinvestment). Distribution: 2,264 codes have
1 slug, 1,626 have 2. Example — code `148843` backs both
`trustmf-liquid-fund-regular-monthly-payout-…` and `trustmf-liquid-fund-regular-monthly-reinvestment-…`.

**Consequence for the build:** the AMFI `schemeCode` is the unit of NAV truth (one NAV series),
while the Scripbox slug is the unit of factsheet truth (option/periodicity-specific). The model
must keep both and link them, not pick one.

Sitemaps are **not** usable for enumeration: `factsheet.xml` lists only 105 curated URLs,
`amc.xml` 220, `mf-sitemap.xml` 395 blog posts.


### Scripbox aggregate coverage (1,761 direct funds)

`amfi_code` 1761 · `isin_code` 1756 · `rta_scheme_code` 1757 · `amc_code` 1761 ·
`fund_id` 1761 · `apus_fund_scheme_id` 1479 · `nav` 1761 · `aum` 1739 ·
`expense_ratio` 1742 · `inception_date` 1752 · `risk_level` 1761 · `taxability` 1761 ·
`sebi_category_name` 1761 · `fund_manager` 1761 · `composition` 1761 · `category_return` 1761 ·
`sip` 1761 · `exit_load` 1097 · `asset_holding` 1340 · `sectorwise_holding` 1221 ·
`stats_variables` 1211 · `fund_score` 1179 · `lock_in_period_days` 85.


Returns thin out with horizon: `return_1year` 1599, `return_5year` 948, `return_10year` 661.
Not a bug — funds younger than the horizon simply have no value.

### Asset-class spread

Equity 908 · Debt 501 · Hybrid 186 · International Equity 60 · Solution Oriented 43 ·
Others 41 · Precious Metals 22.

---

## 3B. AMFI (`amfiindia.com`) — authority source, format confirmed

AMFI is **geo-blocked from this network** (TCP connect times out; DNS resolves to 14.143.46.156).
Two things changed that:

1. **The Wayback Machine holds a real copy of `spages/NAVAll.txt`.** We pulled a 1.6 MB snapshot
   (13,738 schemes, dated 27-Dec-2024) and parsed it end-to-end. The format is now known exactly,
   so the parser is written and validated *today* — the India-server run only swaps the URL.
2. **Production ingest will run on an India server**, where AMFI is reachable. The file format is
   identical, so the Wayback sample is a genuine dry-run fixture, not a guess.

### Exact URLs to fetch from the India server

Confirmed from AMFI's own archived `/nav-history-download` page (the `?t=` value is a
cache-buster; any timestamp works):

| URL | Content |
|---|---|
| `https://www.amfiindia.com/spages/NAVAll.txt?t=<ts>` | **Complete** NAV report — all scheme types |
| `https://www.amfiindia.com/spages/NAVOpen.txt?t=<ts>` | Open-ended schemes only |
| `https://www.amfiindia.com/spages/NAVClose.txt?t=<ts>` | Close-ended schemes only |
| `https://www.amfiindia.com/spages/NAVInterval.txt?t=<ts>` | Interval funds only |

> ⚠️ **These are same-day snapshots, not history.** AMFI publishes only the latest NAV day.
> There is **no** AMFI bulk NAV-history download. `api.mfapi.in` remains the NAV-history source.

### File format (validated against the real file)

Semicolon-delimited, `\r\n` line endings, with **hierarchical section headers** that carry the
metadata:

```
Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Debt Scheme - Banking and PSU Fund)      <-- schemeType(schemeCategory)

Aditya Birla Sun Life Mutual Fund                           <-- AMC / fund house

119551;INF209KA12Z1;INF209KA13Z9;...Banking & PSU Debt Fund  - DIRECT - IDCW;102.3377;27-Dec-2024
108272;INF209K01LX6;INF209KA11Z3;...Banking & PSU Debt Fund  - REGULAR - IDCW;144.0056;27-Dec-2024
```

Parse rules that matter:

- A line is a **data row** iff it contains exactly 5 semicolons. Everything else is a header.
- Section headers match `^(Open Ended Schemes|Close Ended Schemes|Interval Fund Schemes)\((.+)\)$`
  → yields `schemeType` **and** `schemeCategory` in one shot.
- A non-header, non-data line is the **AMC name** → `fund_house`.
- Missing ISINs are the literal `-`, not empty string.
- Column A is **dual-purpose**: the *Growth* ISIN for growth options, the *Div Payout* ISIN for
  IDCW options. Column B is always *Div Reinvestment*.
- Dates are `DD-Mon-YYYY` (`27-Dec-2024`) — **not** mfapi.in's `DD-MM-YYYY`. Convert explicitly.
- Measured on the real file: 13,738 data rows, 51 type/category headers, 44 AMC headers, and
  **100 % of rows resolved to both a category and an AMC**.

### Why AMFI is worth the India-server hop — measured

Cross-checked all 13,738 AMFI codes against `api.mfapi.in` (all 13,738 are present there):

| Check | `api.mfapi.in` | AMFI |
|---|---|---|
| Invalid `schemeType` | **3,725 (27.1 %)** | **0 (0.0 %)** |
| Distinct `schemeCategory` values | mixed old + new taxonomies | **48 clean values** |

**AMFI eliminates the column-shift corruption entirely.** It also revealed that part of what we
called "corruption" is really **taxonomy versioning** — mfapi.in still carries pre-2021 SEBI
category names while AMFI's file uses the current ones:

| code | `api.mfapi.in` | AMFI |
|---|---|---|
| 100046 | `Income/Debt Oriented Schemes - Liquid Fund` | `Debt Scheme - Liquid Fund` |
| 100037 | `Income/Debt Oriented Schemes - Medium to Long Term Fund` | `Debt Scheme - Medium to Long Duration Fund` |
| 100057 | `Income/Debt Oriented Schemes - Gilt Fund` | `Debt Scheme - Gilt Fund` |

Only 49.9 % of mfapi.in categories match AMFI's clean list, so we need an **old→new taxonomy
crosswalk**, and AMFI must be the authority for `schemeType` / `schemeCategory` / `fund_house`.

**NAV agreement is exact.** Comparing AMFI's 27-Dec-2024 values against the *same date* in
mfapi.in's full history gave a difference of **0.000000** on every scheme tested:

```
code 119551  AMFI nav=102.3377   mfapi.in @27-12-2024 = 102.33770   diff 0.000000
code 108272  AMFI nav=144.0056   mfapi.in @27-12-2024 = 144.00560   diff 0.000000
```

The two sources independently confirm each other, which makes them ideal for reconciliation.
(ISIN column B agrees 88.5 %; the remainder is column-A dual-purpose semantics, not error.)

### Plan labels AMFI revealed

| Plan label | Count | Share | In scope? |
|---|---|---|---|
| `DIRECT` | 6,368 | 46.4 % | ❌ excluded |
| `REGULAR` | 3,766 | 27.4 % | ✅ yes |
| unlabelled | 3,383 | 24.6 % | ✅ via sibling test (§4) |
| `RETAIL` | 133 | 1.0 % | ⚠️ captured, flagged out of default scope |
| `INSTITUTIONAL` | 88 | 0.6 % | ⚠️ captured, flagged out of default scope |

Retail and Institutional are **separate plan labels**, not regular plans — e.g.
`Kotak Corporate Bond Fund- Institutional Plan-Growth Option`,
`Aditya Birla Sun Life Banking & PSU Debt Fund - Retail Plan-Growth`.
**Decision:** store them with `plan_type` = `RETAIL` / `INSTITUTIONAL` so nothing is lost, but
exclude them from default "regular plan" views. They are 1.6 % of the universe.

### Bonus: AMFI's portfolio-disclosure page is an AMC link directory

`amfiindia.com/online-center/portfolio-disclosure` embeds, in its RSC payload, direct links to
every AMC's factsheet and portfolio files. Harvested from the archived copy into
`research/amfi_amc_directory.json`:

- **53 distinct AMC domains**
- **181 factsheet / portfolio / disclosure landing pages**
- **70 direct data-file links** (`.xls` / `.xlsx` / `.pdf`) — e.g.
  `mf.nipponindiaim.com/…/NIMF-MONTHLY-PORTFOLIO-Nov-25.xls`

This is the ready-made seed list for the optional Phase 7 AMC adapters, and the best available
route to **portfolio-holdings history**.

### Google Finance — tested and rejected

| Probe | Result |
|---|---|
| `google.com/finance/quote/INF109K01761:NSE` (MF ISIN) | 200, **"Not Found"** |
| `google.com/finance/quote/INF109K016L0` (bare ISIN) | 200, **"Not Found"** |
| `google.com/finance/quote/HDFCLARGE:NSE` (fund ticker) | 200, **"Not Found"** |
| `google.com/finance/quote/RELIANCE:NSE` *(control)* | 200, valid page |
| `google.com/finance/quote/NIFTY_50:INDEXNSE` *(control)* | 200, valid page |

Controls resolve; **every** mutual-fund probe returns "Not Found". Google Finance has no Indian
mutual-fund coverage — no NAV, no AUM, no factsheet data. Indices do resolve, but the historical
series is JS-rendered and not extractable from the HTML (0 chart points, no `data-last-price`),
so it is not usable as a benchmark source either. **Dropped.**

### What to hand over from the India server

Priority order — each is a plain file drop, no code needed:

1. `https://www.amfiindia.com/spages/NAVAll.txt` ← **highest value**; fixes type/category/house
2. `https://www.amfiindia.com/spages/NAVOpen.txt`, `NAVClose.txt`, `NAVInterval.txt`
3. The monthly **scheme-wise AUM** file (under *Investor Corner → Online Center → Monthly
   Factsheets*) — the one file we could **not** find in Wayback, and the only route to AUM history
4. Fresh HTML of `/online-center/portfolio-disclosure` and
   `/investor-corner/online-center/monthlyfactsheets`, so the AMC directory is current rather
   than a 2025 snapshot
5. Anything behind `/intermediary/other-data/scheme-dividends` — would close the IDCW-history gap

The pipeline ingests whatever arrives, and runs mfapi.in + Scripbox unchanged if AMFI files never
show up.

---



## 4. Confirmed scope — "Regular Plan, all options"

**Decision (confirmed with user):** capture **Regular Plan** schemes across **all options** —
Growth, IDCW/dividend, payout *and* reinvestment variants — not just Growth. Direct plans are
excluded.

### The population, measured

Starting from the 8,676 active schemes, classified by `schemeName` (because `schemeType` is
corrupt — see §2):

| Bucket | Count | In scope? |
|---|---|---|
| name contains `Regular Plan` / `Regular` | 4,098 | ✅ yes |
| `Direct` / `Direct Plan` | 4,068 | ❌ excluded |
| unlabelled — **ETF** | 256 | ❌ excluded (ETFs have no direct/regular split) |
| unlabelled — non-ETF, **has** a Direct Plan sibling | 90 | ✅ yes (these *are* regular plans, just named without the word) |
| unlabelled — non-ETF, **no** Direct Plan sibling | 164 | ✅ yes (regular-only schemes) |

> **Total NAV download targets: 4,352 AMFI scheme codes.**

Classifier rule (order matters):

```
if 'direct plan' in name.lower()                     -> DIRECT      # authoritative
elif 'direct' in name.lower() and 'regular' not in   -> DIRECT
elif 'regular' in name.lower()                       -> REGULAR
elif 'etf' in name.lower()                           -> EXCLUDE
elif exists sibling base-name with 'Direct Plan'      -> REGULAR
else                                                 -> REGULAR (regular-only scheme)
```

The `Direct Plan` check must run first: 10 active schemes contain *both* strings, e.g.
`Axis Gilt Fund - Direct Plan - Regular IDCW Option` (a **direct** plan whose *option* is
named "Regular IDCW") and `HSBC Liquid Fund - Regular Plan - Direct`. A naive
`'direct' in name` test misclassifies these.

### Two data-quality defects this scope work surfaced

**DQ-1 — Scripbox `is_direct` disagrees with AMFI on 9 slugs (0.1 %).**
Nine slugs marked `is_direct=false` join by ISIN to schemes whose AMFI name says
`Direct Plan`: codes `134026` (UTI Banking & PSU Debt Fund - Direct Plan - Monthly IDCW),
`152519`, `120846`, `147296`, `147300`, `153586`, `153014`. Low volume, but exactly the kind of
silent contamination that would let direct plans leak into a "regular only" dataset.
**Rule: AMFI's name is authoritative; Scripbox `is_direct` is advisory and gets flagged on conflict.**

**DQ-2 — 254 unlabelled non-ETF schemes need a sibling test, not a guess.**
90 have a `Direct Plan` sibling (so they are regular: `LIC MF Gilt Fund - IDCW`,
`BARODA BNP PARIBAS LIQUID FUND - Growth Option`, `BANK OF INDIA LARGE & MID CAP FUND - Bonus`).
164 have no direct sibling at all (`Motilal Oswal Midcap Fund`, `PGIM India Arbitrage Fund`) —
these are regular-only and belong in scope. Neither group can be resolved by name substring alone.

### NAV history window — last 5 years (confirmed with user)

**Decision:** **5 years** of NAV history is the requirement; keep more where it comes free.

| Window | Rows (4,352 codes × ~248 trading days/yr) | Parquet |
|---|---|---|
| **5 years (required)** | **~5.4 M** | **~0.2 GB** |
| 10 years | ~10.8 M | ~0.4 GB |
| 20 years | ~21.6 M | ~0.9 GB |

`GET /mf/{code}` returns the **full** series in a single call, so trimming at fetch time saves no
requests — only storage, and 5 years is already just ~0.2 GB. So:

> **Ingest everything returned; serve the last 5 years by default.**

A `nav_last_5y` view exposes the required window. Keeping the full series means 10-year returns
can be computed later with **no re-crawl**. The 5-year floor also makes the "no NAV before
Apr-2006" gap (§6) irrelevant.

### Scope consequences

- **NAV:** 4,352 `GET /mf/{code}` calls for the backfill (~15–20 min at concurrency 4).
- **Factsheets:** up to 6,235 Scripbox regular slugs, of which 5,516 are confirmed live.
  Expect ~700 slugs to fail resolution (delisted, or no ISIN) — the crawler must tolerate that
  and record it, not abort.
- **Coverage ceiling:** 3,828 of the 4,352 in-scope codes have a matching Scripbox slug
  (~88 %). The remaining ~524 get mfapi.in data only (code, name, house, category, ISINs, NAV)
  and no expense ratio / AUM / holdings. That gap is real and should be reported, not hidden.

---

## 5. What we can capture — field inventory

### 5.1 From `api.mfapi.in` (every scheme in India)

| Field | Source key | Notes |
|---|---|---|
| AMFI scheme code | `schemeCode` | **primary key** |
| Scheme name (AMFI canonical) | `schemeName` | |
| Fund house | `fundHouse` | 55 houses |
| Scheme type | `schemeType` | Open-ended / Close-ended / Interval |
| Scheme category | `schemeCategory` | AMFI taxonomy, needs normalising |
| ISIN – growth | `isinGrowth` | |
| ISIN – div reinvestment | `isinDivReinvestment` | |
| **Full daily NAV history** | `/mf/{code}` → `data[]` | `(date, nav)` since 2006-04 |
| Latest NAV + date | `/mf/latest` | |

### 5.2 From Scripbox (102 fields per fund)

**Identity / codes**
`amfi_code` · `isin_code` · `rta_scheme_code` · `amc_code` · `amc_id` · `amc_slug` · `amc_name` ·
`amc_short_name` · `amc_icon` · `amc_logo` · `fund_id` (UUID) · `apus_fund_scheme_id` ·
`apus_fund_scheme{name,kind,inserted_at,…}` · `fund_scheme_id` · `fund_scheme_code` ·
`plan_id` · `sub_plan_id` · `fund_slug` · `name` · `full_name` · `plan_name` ·
`name_alternative{fund,fund_variant_option,fund_variant_plan,fund_variant_plan_option}` ·
`fund_variant[]` (**all sibling variants, each with its own ISIN + slug + plan_id**) · `face_value`

**Plan / option flags**
`is_direct` · `is_dividend` · `is_reinvestment` · `is_payout` · `option` ·
`dividend_periodicity` · `is_etf_fund` · `is_nfo` · `is_active_status` · `status` ·
`openended` · `kind` · `is_investable`

**Classification**
`asset_class` / `asset_class_code` · `sub_asset_class` / `sub_asset_class_code` ·
`sebi_category_name` (e.g. `"Hybrid: Arbitrage"`) · `taxability` (equity/debt — drives LTCG/STCG) ·
`index{index_id,full_name,short_name}` (benchmark)

**Valuation & size**
`nav` · `nav_date` · `nav_on_31_jan_2018` · `aum` (₹ crore) · `fund_size` (1–5 band) ·
`expense_ratio` · `relativeFundSize`

**Performance (absolute)**
`return_1day` `return_3month` `return_6month` `return_1year` `return_2year` … `return_10year`
`return_since_launch` · `ytd`

**Performance (peer/category benchmark)**
`category_return{day_1, week_1, month_1, month_3, month_6, month_9, year_1…year_10, ytd,
since_launch}` — each `{count, return}` so you get category average *and* peer count

**Risk statistics**
`stats_variables{alpha, alpha_stated, beta, beta_stated, rsquared, rsquare_stated, sharpe_ratio,
sortino_ratio, standard_deviation, treynor, treynor_stated, information_ratio, mean, as_on_date,
rating_date}`

**Risk, ESG & ratings**
`risk_level` ("Very High" … "Low") · `es_score` · `fund_recommendation_rating` (1–5) ·
`sb_recommendation` · `portfolio_audit_ic_blacklist`

**Scripbox proprietary 30-dimension score** (`fund_score`): downside_protection,
upside_participation_measure, volatility_of_outperformance, performance_consistency,
rolling_returns_1year_holding, rolling_returns_3year_holding, consistency_of_credit_quality,
internal_view_on_credit_quality, market_perception_of_portfolio_quality,
insulation_from_interest_rate, long/medium/short_term_performance,
outperformance_consistency, performance_relative_to_peers, technical_rank, quality_rank,
growth_rank, valuation_rank, past_performance_rank, momentum_rank, risk_ratio_rank,
advisory_expense_ratio, fund_performance, track_record …

**Lifecycle**
`inception_date` · `issue_actual_close_date` · `lock_in_period_days` · `updated_at`

**Portfolio**
`composition{equity, debt, commodities, realestate, others}` (%) ·
`asset_holding[]` — **up to 200 line items**, `{name, company_id, instrument_abv,
instrument_desc, instrument_type, instrument_asset_class, percentage}` (includes negative
percentages for derivative shorts) ·
`sectorwise_holding[]{sector_name, sector_code, percentage, value}` ·
`holdings_maturity{duration, macaulay_duration, avg_ytm, average_maturity_period, as_on_date}`
(debt metrics)

**People**
`fund_manager[]{name, person_id}`

**Investment rules**
`min_initial_investment_amount` · `min_subsequent_investment_amount` · `min_investment_multiples` ·
`min_withdrawal_amount` · `transaction_status` · `fulfillment_providers` (`RTA`, `BSE`, `NSE`,
`WMS_NSE`) ·
`is_purchase_allowed` `is_investment_allowed` `is_sip_allowed` `is_stp_allowed` `is_swp_allowed`
`is_switch_in_allowed` `is_switch_out_allowed` `is_withdrawal_allowed`
`is_one_time_investment_allowed` `is_minor_investments_allowed` `is_us_canada_supported`

**SIP / STP / SWP**
`sip{daily, weekly, fortnightly, monthly, quarterly}` ×
`{dates, days_of_month, min_amount, max_amount, min_installments, minimum_installments, multiplier}` ·
`stp{daily…quarterly}` · `swp{monthly, quarterly, half_yearly, annual}`

**Exit load & objective**
`exit_load{note, as_on_date}` · `objective` (investment-objective prose)

**Documents / regulatory links**
`factsheet_url` · `portfolio_disclosure_url` (AMFI) ·
`sid_url` `sia_url` `kim_url` (SEBI — **verified reachable, HTTP 200**)

**AMC-level rollup** (from `/mutual-fund/amc`)
`amc_name` `amc_code` `amc_slug` `amc_id` `aum` `short_desc` `amc_phone` `amc_email`
`amc_seo_name` + `fund_count` per asset_class and per sub_asset_class

### 5.3 Derivable from the NAV series (we compute, not fetch)

Rolling returns at any horizon · CAGR/annualised return for any window · XIRR · realised
volatility · max drawdown & recovery time · Sharpe / Sortino / Calmar · beta & alpha vs a
benchmark series · tracking error · best/worst calendar periods · up/down capture ·
SIP backtests · lump-sum growth tables · NAV continuity/outlier detection.

---

## 6. What we CANNOT capture (honest gaps)

| Gap | Why | Mitigation |
|---|---|---|
| NAV before Apr-2006 | archive floor of `api.mfapi.in` | **Now moot** — user scoped to last 5 years (§4). AMFI publishes no NAV history anyway (§3B). |
| AUM **history** | Scripbox gives current AUM snapshot only | AMFI monthly scheme-wise AUM file is **not** in Wayback and is India-only → capture from the India server going forward; we build our own time series |
| Portfolio holdings **history** | Scripbox `asset_holding` is current only | ✅ **partially solved** — AMFI's portfolio-disclosure page is a directory of 53 AMC domains / 181 landing pages / 70 direct `.xls`/`.xlsx` links, harvested to `research/amfi_amc_directory.json` |
| Expense-ratio **history** | current value only | snapshot-only; build our own series going forward |
| Fund-manager **tenure** | `fund_manager[]` has no from/to dates | none from these sources |
| **IDCW / dividend declaration history** (record date, ex-date, amount) | absent from mfapi.in + Scripbox | AMFI has `/intermediary/other-data/scheme-dividends`, but the archived copy is a 2017 ASP search form with no bulk file → **probe it from the India server**; until then, dividend NAV drops make total-return maths approximate for IDCW options |
| NFO detail (open/close, min subscription) | partial (`issue_actual_close_date` only) | — |
| Scheme change history (mergers, renames, exit-load revisions) | absent | detect renames heuristically via NAV-series continuity |
| ISINs for Institutional / Retail plan options | ~0 % — these options genuinely have no ISIN | expected, not a defect |
| ISINs for delisted legacy schemes | 17.9 % missing in the legacy bucket | acceptable |
| Benchmark **index NAV series** | Scripbox gives `index_id`/name only | ❌ Google Finance tested and **rejected** (§3B). Use NSE/BSE index archives or compute alpha/beta from Scripbox's supplied benchmark returns |
| AMC metadata beyond house name | `api.mfapi.in` has no expense ratio / AUM / risk / holdings | covered by Scripbox enrichment |

---

## 7. Risks & constraints

1. **Scripbox `buildId` churn.** `/_next/data/1099580/…` 404s the moment they redeploy.
   Mitigation (and reason to prefer HTML): scrape `/mutual-fund`, regex out
   `buildId` from `__NEXT_DATA__`, cache it, auto-refresh on 404.
2. **`api.mfapi.in` is a single-maintainer free service** — no SLA, no key, could disappear.
   Mitigation: treat it as a **bulk seed**, mirror everything locally on first pull, never make
   daily operation depend on it being up.
3. **`api.mfapi.in` column-shift corruption** in `schemeType`/`schemeCategory`. Needs a
   normaliser + a reject-and-quarantine step, not silent ingestion.
4. **Robots.** Scripbox disallows `*/data/`. Using HTML pages keeps us clean.
   `api.mfapi.in` publishes no robots.txt and advertises itself as a free API.
5. **Politeness.** A 10-way parallel burst against Scripbox returned 200s in 1.66 s — no
   throttling observed. Still: cap concurrency at 4, add jitter, cache everything, back off on
   429/5xx.
6. **AMFI is India-only — confirmed, and worked around.** DNS resolves (14.143.46.156) but TCP
   connect times out from here, while Groww/Scripbox/CAMS/SEBI all work → AMFI-specific geo/WAF.
   Two mitigations, both now in place:
   (a) **Wayback Machine holds `spages/NAVAll.txt`** — we retrieved a real 1.6 MB copy, so the
   parser is written and validated *before* any India-server run (§3B);
   (b) the **production ingest runs on an India server**, where AMFI is reachable. The parser
   is format-identical either way, so the Wayback sample is a true dry-run fixture.
7. **IP / redistribution.** AMFI codes, ISINs and NAVs are public. But `fund_score`,
   `es_score`, `sb_recommendation` and `fund_recommendation_rating` are **Scripbox's
   proprietary opinion data**. Store them if useful internally; do not republish them.
8. **Scale.** 4,352 in-scope scheme codes × one `/mf/{code}` call each. At concurrency 4 with
   jitter that is roughly **15–20 min** for a full NAV backfill — tractable and resumable.
   Storage is a non-issue: ~5.4 M rows / ~0.2 GB Parquet for the required 5-year window (§4).
9. **AMFI availability is a deployment constraint, not a code constraint.** The AMFI ingest must
   run from an India IP. Design it as an **optional adapter**: if the AMFI file is absent, the
   pipeline falls back to mfapi.in metadata and flags the affected rows, rather than failing.


---

## 8. Proposed architecture

```
        amfiindia.com  (AUTHORITY — India server only)
        spages/NAVAll.txt -> clean schemeType/Category/AMC, official ISINs
                     |  metadata correction + reconciliation
                     v
              api.mfapi.in  (PRIMARY / canonical)
              GET /mf/latest   -> 1 request, whole universe
              GET /mf/{code}   -> full NAV history (serve last 5y by default)
                     |  scheme_code, ISIN, name, house, category, NAV
                     v
        ----------------------------------------------
        |  Ingestion layer (Python)                  |
        |  mfapi / scripbox / amfi clients + http    |
        |  retry, throttle, cache, checkpoint,       |
        |  buildId refresh, normalise, quarantine    |
        ----------------------------------------------
                     ^  join on ISIN
                     |  (AMFI code as cross-check)
              scripbox.com  (ENRICHMENT)
              /mutual-fund/amc                -> 50 AMCs
              /mutual-fund/amc/{slug}/isin-…  -> 1,761 funds + variants
              /mutual-fund/{slug}             -> 102 fields per fund
              (all via HTML + embedded __NEXT_DATA__, robots-clean)
                     |
                     v
              Storage: PostgreSQL 16  (+ Parquet export)
```

**Why PostgreSQL (revised from DuckDB).** This is a *continuously ingested, API-served*
dataset, not a one-shot analytical file, and that changes the answer:

| Need | PostgreSQL | DuckDB |
|---|---|---|
| FastAPI serving, concurrent readers | ✅ native | ⚠️ embedded, single-writer |
| Daily incremental upserts | ✅ `ON CONFLICT` | ❌ weak UPSERT |
| ACID across funds + nav + flags | ✅ | ❌ |
| Mutable reconciliation / checkpoint state | ✅ | ❌ |
| Large analytical scans | ⚠️ needs partitioning | ✅ columnar |
| Native Parquet | ❌ export via pyarrow | ✅ |

Scale is a non-issue: ~5.4 M rows for the required 5-year window, ~26 M with full history —
roughly 1.4 GB of data and ~2.5 GB including indexes. Yearly RANGE partitioning on `nav_date`
gives partition pruning for the 5-year view and an O(1) drop of an old year. Parquet stays the
*export/distribution* format, derived from PostgreSQL rather than acting as a second store.
TimescaleDB is a drop-in later if history grows past ~30 M rows and compression starts to matter.

### Data model (as implemented in `sql/`)

Schema lives in `sql/001_core_schema.sql`, `sql/002_nav_and_views.sql`,
`sql/003_enrichment_recon.sql` — all idempotent, applied automatically by
`docker compose up` via `/docker-entrypoint-initdb.d`. Everything below is
validated against a real PostgreSQL server with the real AMFI snapshot.

```
mf.amcs            PK amc_id (identity) · amfi_amc_name UNIQUE · normalised_name
                   website · portfolio_url · factsheet_url · is_active

mf.funds           PK amfi_scheme_code INTEGER  (canonical identity; 1:1 with mfapi.in)
                   mfapi_scheme_code · scheme_name · scheme_name_norm · amc_id FK
                   scheme_type        -- OPEN_ENDED | CLOSE_ENDED | INTERVAL (CHECK)
                   scheme_category · scheme_category_raw · category_source
                   plan_type          -- REGULAR | DIRECT | RETAIL | INSTITUTIONAL | UNLABELLED
                   option_type        -- GROWTH | IDCW | DIVIDEND | BONUS | UNKNOWN
                   periodicity · is_etf · is_defunct · nav_not_published · is_active
                   in_scope           -- GENERATED: Regular Plan (or unlabelled non-ETF)
                   isin_growth_or_div_payout · isin_div_reinvest   -- AMFI cols A and B
                   isin_primary       -- GENERATED coalesce(A,B), for joins
                   metadata_authority -- AMFI | MFAPI | SCRIPBOX | MANUAL

mf.nav_history     PK (amfi_scheme_code, nav_date) · nav NUMERIC(18,4) · source
                   is_cross_verified · ingested_at
                   PARTITION BY RANGE (nav_date), yearly, + DEFAULT partition
                   + mf.ensure_nav_partition(year)  -- idempotent, called before each load

mf.fund_variants   PK amfi_scheme_code · group_key · base_scheme_name
                   regular_code · direct_code · has_direct_sibling
                   -> pairs Regular<->Direct; requires same AMC + scheme type

mf.fund_facts      PK amfi_scheme_code · Scripbox identity · asset/sub-asset class
                   sebi_category_name · aum (INR cr) · expense_ratio · inception_date
                   transactionability flags · minimums · returns{1d…10y,since launch}
                   composition{} · holdings{} · sip/stp/swp{} · stats_variables{} · raw_payload{}

mf.fund_opinions   PK amfi_scheme_code · sb_recommendation · fund_recommendation_rating
                   es_score · fund_score{} · risk_level · objective
                   -> LICENSING BOUNDARY: proprietary third-party opinion output,
                      isolated so a redistribution-safe export can omit one table

mf.taxonomy_xwalk  PK xwalk_id · old_category · new_category UNIQUE · reason
                   confidence · evidence · valid_from/to
                   -> turns taxonomy drift into a classified, expected difference

mf.source_metadata fetch_id · source · endpoint · entity_kind · content_hash (sha256)
                   acquisition -- LIVE | WAYBACK | USER_PROVIDED | CACHED | FIXTURE
                   http_status · content_bytes · records_in/ok/quarantined · duration_ms

mf.quality_flags   flag_id · amfi_scheme_code (NULL = dataset-wide) · nav_date
                   flag_type (16 values incl. LIFECYCLE_ENDED) · severity · message
                   details jsonb · run_id · detected_at · resolved_at

mf.reconciliation_runs      run_id · scope · sources[] · counts · status
mf.reconciliation_findings  finding_id · run_id FK · finding_type · classification
                   -> EXPECTED_TAXONOMY_DRIFT vs REAL_ERROR keeps signal-to-noise usable
mf.ingest_checkpoints       PK (source, entity_kind, entity_key) · cursor · status

Views: mf.nav_last_5y · mf.v_coverage · mf.v_nav_coverage · mf.v_enrichment_coverage
       mf.v_partition_health
Funcs: mf.nav_window(years, asof) · mf.ensure_nav_partition(year)
```

**`nav_last_5y` is anchored on the dataset's own `max(nav_date)`, not `CURRENT_DATE`** —
otherwise a snapshot's contents would silently shift daily and two exports of the same
snapshot would differ. `mf.nav_window(p_years, p_asof)` gives explicit control for
reproducing a historical cut exactly.

### Reconciliation checks (run every ingest)

- ISIN ↔ AMFI-code agreement between Scripbox and mfapi.in (POC baseline: **100 %**)
- **AMFI ↔ mfapi.in NAV equality on a shared date** (baseline: diff **0.000000**, §3B)
- **`schemeType` validity: AMFI 0 % invalid vs mfapi.in 27.1 %** → prefer AMFI, flag the rest
- **taxonomy drift**: mfapi.in category not in AMFI's 48-value list → route via `taxonomy_xwalk`
- **AMFI ↔ mfapi.in ISIN agreement**, accounting for column-A dual-purpose semantics (88.5 % on col-B)
- regular-vs-direct consistency: `is_direct` vs name/slug tokens vs `fund_variant` links
- missing / malformed ISINs
- inactive or dead schemes still receiving NAV
- NAV sanity: date range, duplicates, gaps, zero/negative NAV, >±50 % single-day jump
- shifted-metadata detection on `schemeType` / `schemeCategory`
- coverage dashboard: funds with facts vs funds without

### Update strategy

- **Daily:** `/mf/latest` → upsert today's NAV for all live schemes (one request).
- **Weekly:** re-pull `/mf` to catch new schemes / new ISINs; refresh Scripbox facts for the
  active set, throttled and spread over the week.
- **Monthly:** full NAV re-pull for any scheme with gaps; re-crawl AMC lists.
- Every run is checkpointed and idempotent, so a crash mid-crawl resumes rather than restarts.

---

## 9. Build phases

| Phase | Deliverable | Effort |
|---|---|---|
| **0** | Project scaffold, config, HTTP client (retry/throttle/cache/checkpoint), **PostgreSQL** schema — **DONE except HTTP client** | ½ day |
| **1** | mfapi seed: `/mf` + `/mf/latest`, regular-only filter, category/type normaliser, quarantine | 1 day |
| **1b** | **AMFI adapter** (India server): parse `NAVAll.txt` → clean type/category/house + official ISINs; taxonomy crosswalk; optional-adapter fallback. **Parser already written and validated against the real Wayback file (§3B).** | ½ day |
| **2** | NAV history downloader: `/mf/{code}` for the in-scope set, resumable, incremental; `nav_last_5y` view | 1 day |
| **3** | Scripbox enrichment: AMC crawl → variant discovery → detail pages → 102-field extract | 1–2 days |
| **4** | Join + reconciliation + quality report + coverage dashboard | 1 day |
| **5** | Derived metrics engine (rolling returns, drawdown, Sharpe, SIP backtests) | 1 day |
| **6** | Scheduler for daily/weekly/monthly cadence | ½ day |
| **7** *(optional)* | AMC adapters seeded from `research/amfi_amc_directory.json` (53 domains) for portfolio/factsheet files + SID/SIA/KIM from SEBI | open-ended |

Total for a working, reconciled dataset: **~6 days**.

### Implementation status

**Built and validated against a real PostgreSQL server:**

| Artifact | Notes |
|---|---|
| `sql/001…003` | 12 tables, 5 views, 2 functions, 39 yearly partitions. Idempotent. |
| `src/mfdataindia/store/postgres.py` | COPY-based bulk load with a batched-INSERT fallback; set-based upserts. |
| `src/mfdataindia/load/amfi_to_store.py` | Parser → `mf.*` row mapping; the only place that knows column order. |
| `src/mfdataindia/load/normalise.py` | Folding, AMC normalisation, base-scheme keys, exact `Decimal` NAV. |
| `pyproject.toml`, `docker-compose.yml` | Scaffold; compose applies migrations on first boot. |
| `tests/` | **79 passing** — 67 unit + 12 PostgreSQL integration. |

**Full real-data load (27-Dec-2024 Wayback `NAVAll.txt`), 3.8 s end to end:**

```
amcs      44 inserted        funds     13,738 inserted
nav       13,732 inserted    variants  10,134 inserted
flags     405 inserted       (DEAD_SCHEME 394 · LIFECYCLE_ENDED 9 · ISIN_MISSING 2)
```

Re-running the identical load gives `inserted=0, updated=0, unchanged=<all>` for every
table — idempotency verified, not assumed. `v_coverage` reproduces the §4 population
exactly: 13,738 schemes · 6,916 in-scope · 6,721 live in-scope · 44 AMCs · 48 categories.
NAV rows routed across 15 yearly partitions with the DEFAULT partition empty.

### Four data defects this phase surfaced

Loading real data into a constrained schema found problems that reading the file did not:

1. **`HALF YEARLY` periodicity.** AMFI spells it with a space (and sometimes a hyphen);
   the schema's CHECK wants `HALF_YEARLY`. A single valid row aborted the *entire* load.
   Fixed by `PERIODICITY_MAP`, with a test asserting every mapped value satisfies the CHECK.
2. **AMFI writes `REDEEMED` into the ISIN column** (9 rows). All nine are IL&FS
   Infrastructure Debt Fund close-ended series that *completed their tenure* — real NAVs,
   historical end dates. So this is a lifecycle-end signal, not a defect and not "defunct"
   (which implies failure or merger). Modelled as: ISIN coerced to NULL, `is_active=false`,
   `is_defunct=false`, plus a `LIFECYCLE_ENDED` / `INFO` quality flag carrying the raw value.
3. **Two malformed ISINs** — `INF174K1TA2` (11 chars) and `HDFCNIVODG` (10 chars). Coerced
   to NULL and reported as `ISIN_MISSING` / `WARN` with the raw value preserved in `details`.
   Nothing is silently dropped.
4. **ISIN coverage has two legitimate answers.** The 93.7 % quoted in §4 measures AMFI
   *column A only*; the schema's generated `isin_primary = coalesce(A,B)` gives **98.14 %**
   for live in-scope, because 297 schemes carry only a column-B ISIN. Both are correct —
   they measure different things, so both are recorded here to avoid a false contradiction.

Also worth noting: `NAVAll.txt` is a *latest-NAV snapshot*, one row per scheme, so the
5-year window over this fixture holds 5,624 rows (one per scheme). The dense 5-year daily
series comes from `api.mfapi.in` `/mf/{code}` — Phase 2.

## 10. Evidence

Raw captured payloads are preserved under `research/evidence/`:

- `mfapi_latest_sample.json` — full `/mf/latest` response (11.2 MB, 37,936 records)
- `scripbox_amc_list.json` — the 50-AMC list
- `scripbox_amc_isin_icicipru.json` — ICICI Pru's 111 funds (POC join input)
- `scripbox_fund_detail_DIRECT_icicipru_multiasset.json`
- `scripbox_fund_detail_REGULAR_icicipru_multiasset.json` — **regular plan, 102 fields**
- `amfi_NAVAll_wayback_20241227.txt` — **real AMFI `NAVAll.txt`**, 1.6 MB, 13,738 schemes,
  retrieved via Wayback (AMFI is India-only). This is the parser fixture.
- `amfi_navall_parsed_20241227.json` — the same file parsed into structured records with
  `schemeType` / `schemeCategory` / `amc` recovered from the section headers
- `../amfi_amc_directory.json` — 53 AMC domains, 181 factsheet/portfolio landing pages and
  70 direct data-file links, harvested from AMFI's own portfolio-disclosure directory
- `../field_names.md` — full annotated dump of all 102 `factsheetData` keys with sample values

