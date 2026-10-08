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

## P3 — Retire Groww/Scripbox from the running pipeline

**Only after P0 + P1 land.** Steps:
1. Stop scheduling the Scripbox/Groww crawlers (dev and prod).
2. Keep the code in the repo as **dev-only fetchers** for the diff-push
   pattern (document the procedure in README).
3. Confirm `fund_holdings` is now refreshed by the AMC-portfolio parser (P0)
   and the remaining aggregator-only fields (P2) are resolved.
4. `groww_rating` is already decided for **removal** (delete the column
   during cleanup).

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
