# MFDataIndia

Indian **Regular Plan** mutual fund dataset: scheme identity, variants, ISIN/AMFI codes,
metadata, and NAV history (last-5-years window), sourced **entirely from AMFI**
(the authoritative source) and enriched with Scripbox facts.

`api.mfapi.in` is intentionally **not** used for NAV (unreliable). AMFI is reachable
globally via `portal.amfiindia.com` — no India server required.

## Quickstart (local)

Requires Python 3.11+ and Docker (PostgreSQL is the system of record).

```bash
# 1. install Python deps
python3 -m pip install -e .[dev]

# 2. start PostgreSQL (system of record); the sql/ migrations auto-apply on first boot
docker compose up -d            # postgres:16 on 127.0.0.1:5432

# 3. point the app at it — required; there is no built-in default DSN
export MFDATAINDIA_DSN="host=127.0.0.1 port=5432 dbname=mfdataindia user=mfdata password=…"

# 4. start the API + UI (or just `make api` with MFDATAINDIA_DSN set)
PYTHONPATH=src python3 -m uvicorn mfdataindia.api.app:create_app --factory \
  --host 127.0.0.1 --port 8000   # serves http://127.0.0.1:8000

# 5. in another terminal, load data (1-year NAV window is a fast demo)
make bootstrap YEARS=1          # ~2 min
# or the full required 5-year window (resumable, ~3 h):
make backfill YEARS=5
```

Then open **http://127.0.0.1:8000** to browse funds, and **http://127.0.0.1:8000/docs** for the API.

Stop the database with `docker compose down` (add `-v` to also drop the `pgdata` volume).

## Components

| Piece | Path | Notes |
|---|---|---|
| Schema | `sql/001…003` | 12 tables, 5 views, 2 functions, yearly `nav_history` partitions. Idempotent. |
| AMFI client | `src/mfdataindia/ingest/amfi_client.py` | `fetch_navall()` + chunked `fetch_nav_history()`; retry/throttle/sha256 provenance. |
| Parsers | `src/mfdataindia/ingest/amfi_navall.py`, `amfi_nav_history.py` | both NAVAll layouts (6-col archive, 8-col current) + the history report. |
| Store | `src/mfdataindia/store/postgres.py` | COPY bulk load + batched-INSERT fallback, set-based upserts, checkpoints. |
| Backfill job | `src/mfdataindia/jobs/backfill_nav.py` | resumable via `mf.ingest_checkpoints`. |
| Enrichment | `src/mfdataindia/load/scripbox_to_store.py` | factsheetData → `fund_facts` + `fund_opinions` (licensing split). |
| API | `src/mfdataindia/api/` | FastAPI: funds / search / NAV / returns / amcs / categories. |
| UI | `src/mfdataindia/web/` | Google-Finance-style fund browser + detail page with NAV chart. |
| Database | `docker-compose.yml` | PostgreSQL 16 — system of record; `sql/` migrations auto-applied on first boot. |

## Database

**PostgreSQL is the system of record.** `docker compose up -d` starts PostgreSQL 16
and applies the numbered `sql/` migrations automatically on first boot (they are
idempotent, so re-applying later with `psql -f` is also safe). The app has **no
built-in default DSN** — set `MFDATAINDIA_DSN` so the API, ingest, and enrichment all
target the same server:

```bash
docker compose up -d          # postgres:16; applies sql/ migrations on first boot
export MFDATAINDIA_DSN="host=127.0.0.1 port=5432 dbname=mfdataindia user=mfdata password=…"
```

Compose credentials come from `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`
(defaults `mfdataindia` / `mfdata` / `mfdata`); data persists in the `pgdata` volume.
Stop with `docker compose down` (add `-v` to drop the volume).

## Daily NAV refresh

The dataset is kept current with an incremental backfill of the last trading day.
Unlike the multi-hour full backfill, this fetches AMFI's bulk NAV history report
for a single day — 3 requests (open-ended / close-ended / interval slices), a few
seconds of runtime, ~3.8k new rows on a trading day.

```bash
make nav-today    # fetches today's NAV (IST) — run after ~18:30 IST, once AMFI has published
```

Equivalent explicit form:

```bash
PYTHONPATH=src python3 scripts/backfill_nav.py --from-date 2026-10-08 --to-date 2026-10-08
```

**Automated**: a scheduled agent task (`MFDataIndia daily NAV refresh`) runs the same
incremental backfill automatically at **19:30 IST, Mon–Fri** — AMFI publishes the day's
NAV around 18:30 IST, so this catches the freshest published data. On weekends and
market holidays it is a safe no-op (AMFI publishes nothing, 0 rows inserted).

- Idempotent and resumable: each (day × scheme-type) chunk is checkpointed in
  `mf.ingest_checkpoints`; re-runs skip DONE chunks and never duplicate rows.
- Run it only **after** NAV publication (~18:30 IST). An early run marks the day's
  chunks DONE with no data, and later same-day runs will skip them. To recover,
  delete only that day's checkpoints and re-run:
  `DELETE FROM mf.ingest_checkpoints WHERE source='AMFI_HISTORY' AND entity_kind='NAV_HISTORY' AND entity_key LIKE '%:YYYY-MM-DD:YYYY-MM-DD';`

## Computed metrics (fill-if-missing)

Where the enrichment sources (Scripbox/Groww) have no value, `make compute`
derives it from our own AMFI NAV series — compute, don't scrape:

- `sharpe_ratio` (ratio) and `std_deviation` (percent): daily log returns over
  the fund's full history, annualised ×252 / ×√252, 6.5% risk-free rate
- `return_1day` … `return_10year`: NAV at or just before the horizon
  (30.44-day months); young funds stay NULL where no base NAV exists
- `return_since_launch`: only where the source-known inception falls inside
  our NAV data (a fund that started before 2008 would be mislabelled)

Conventions are identical to the API's on-the-fly analytics
(`/api/funds/{code}/analytics`, `/returns`), so a stored value always equals
the on-the-fly value. **Fill-if-missing only**: existing values are never
touched (`SET col = COALESCE(col, …)`; funds with no facts row get a
`source='COMPUTED'` row carrying only computed fields). Every fill is logged
in `mf.computed_fields_log` (method, window, as-of NAV date). Re-runs are
idempotent and pick up newly-published NAVs. `beta` is deliberately not
computed yet — it needs benchmark index history (see the status report).

## AMC factsheet enrichment (official source for beta/Sharpe/std-dev)

`make enrich-factsheet` fills the remaining NULL risk metrics from each AMC's
own monthly factsheet PDF — the **official** source of record, and the
intended standing source for these fields going forward (new funds included,
since every factsheet lists every live scheme).

- Source: ABSL today (parser pluggable per AMC:
  `mfdataindia/ingest/amc_factsheets/`, one parser class per AMC layout).
  ABSL's factsheet is one consolidated PDF per month, discovered through
  their Sitecore API (never hardcoded URLs).
- Fills `beta`, `sharpe_ratio`, `std_deviation` (v1 field set) for the
  Regular Growth variant — the variant the factsheet states its figures are
  for. **Fill-if-missing only**: a populated field is never overwritten.
- **Published NA is a value, not a gap**: ABSL publishes NA beta/Sharpe/
  std-dev for its debt and money-market funds. Those are recorded as
  "not fillable from this source" (honest coverage), never fabricated.
  `alpha` is not published by any AMC layout to date (finding, not a gap).
- Provenance: every fill is logged in `mf.factsheet_fields_log` (AMC,
  document period, NAV as-of date, PDF page, URL) and the fetch in
  `source_metadata` (source `AMC`, entity_kind `FACTSHEET`).
- Names: AMFI's "Banking & PSU" matches the factsheet's "Banking and PSU"
  via exact-fold + AND-normalised matching; spelling disambiguates, and
  unmatched names are counted, never guessed.

## API

```
GET /api/stats                       coverage summary
GET /api/funds?q=&amc=&category=&option=&page=&per_page=&sort=
GET /api/funds/{code}                full detail (identity, latest NAV, facts, variants)
GET /api/funds/{code}/nav?years=     NAV series for the chart
GET /api/funds/{code}/returns        returns computed from our own NAV series
GET /api/amcs · /api/categories · /api/options
```

## Tests

```bash
make test                                 # unit + parsers
MF_TEST_DSN=… pytest -m postgres          # + PostgreSQL integration
```

## Roadmap

Planned improvements live in [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) —
led by the **AMC monthly-portfolio parser** (the official source for full
holdings, which unblocks retiring the Scripbox/Groww aggregators) and extending
the AMC factsheet parser (risk metrics) to the other 52 AMCs. The governing
decision: **official sources only in prod** (AMFI + AMC disclosures);
aggregators are dev-only, diff-pushed whenever needed.

Aggregator identity is fully purged from the DB (migration 013): no
`scripbox*`/`groww_*` columns, no `SCRIPBOX`/`GROWW` source values, and no
aggregator opinions table. In their place, each fund family has **its own**
identity in `mf.fund_family` — `tlws_mf_id` (deterministic UUIDv5), `slug` and
`tags` — for related-news retrieval and stable URLs (`make build-family`;
returned as `family` on `/api/funds/{code}`). References to *Groww as a fund
house* (its funds, holdings of its funds, its benchmark indices) are legitimate
data and remain.
