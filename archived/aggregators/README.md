# Archived: Scripbox & Groww aggregators

Archived **2026-10-08**. The Scripbox and Groww connectors are no longer part
of the running pipeline. They are kept here for reference / future reuse, per
the source-of-record strategy in `docs/IMPROVEMENTS.md`: **official sources
only in prod** (AMFI + AMC disclosures); aggregators are dev-only and, when
ever needed, diff-pushed.

## Why archived
- **Compliance** — we no longer hold or disseminate the aggregator's own
  identity (`scripbox_fund_id`, `groww_slug`) or rating (`groww_rating`); those
  columns were dropped from the DB, API and UI (migration `sql/012`).
- **No accidental triggers** — these crawlers hit aggregator sites. The
  `make enrich-scripbox` / `enrich-groww` / `enrich-groww-all` targets were
  removed and `bootstrap_local.py` no longer loads bundled Scripbox evidence.

## What's here
- `scripts/` — CLI entry points: `enrich_scripbox`, `enrich_groww`,
  `reenrich_groww_pending`, `groww_safety_snapshot`, `verify_groww_integrity`,
  `restore_scripbox_ownership`
- `jobs/`    — crawl orchestration: `enrich_scripbox`, `enrich_groww`,
  `reenrich_groww_pending`
- `ingest/`  — HTTP clients: `scripbox_client`, `groww_client`
- `load/`    — payload → store mappers: `scripbox_to_store`, `groww_to_store`
- `tests/`   — `test_groww_to_store.py`

## To reactivate (if ever needed)
1. Move the modules back into the package:
   - `jobs/`    → `src/mfdataindia/jobs/`
   - `ingest/`  → `src/mfdataindia/ingest/`
   - `load/`    → `src/mfdataindia/load/`
   - `scripts/` → `scripts/`
   - `tests/`   → `tests/`
2. The DB columns `scripbox_fund_id`, `groww_slug`, `groww_rating` were dropped
   (migration 012). If the loaders reference them, either re-add them via a new
   forward migration or update the loaders to stop writing them.
3. Re-add any `make enrich-*` targets if desired (removed from the Makefile).
4. Run **only in dev**, and push results to prod as a **diff** — never as a
   scheduled prod job.