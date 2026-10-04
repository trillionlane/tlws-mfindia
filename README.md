# MFDataIndia

Indian **Regular Plan** mutual fund dataset: scheme identity, variants, ISIN/AMFI codes,
metadata, and NAV history (last-5-years window), sourced **entirely from AMFI**
(the authoritative source) and enriched with Scripbox facts.

`api.mfapi.in` is intentionally **not** used for NAV (unreliable). AMFI is reachable
globally via `portal.amfiindia.com` — no India server required.

## Quickstart (local, self-contained)

Requires Python 3.11+, Node 18+ (for the embedded database engine).

```bash
# 1. install Python deps
python3 -m pip install -e .[dev]

# 2. start the stack (embedded PostgreSQL + API + UI)
./scripts/up.sh                 # serves http://127.0.0.1:8000

# 3. in another terminal, load data (1-year NAV window is a fast demo)
make bootstrap YEARS=1          # ~2 min
# or the full required 5-year window (resumable, ~3 h):
make backfill YEARS=5
```

Then open **http://127.0.0.1:8000** to browse funds, and **http://127.0.0.1:8000/docs** for the API.

Stop with `make db-stop` (sends SIGTERM so the embedded database flushes cleanly).

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
| Local DB | `db/server.mjs` | PGlite (real PostgreSQL 18), data in `data/pglite/`. |

## Database

The local stack uses **PGlite** (embedded real PostgreSQL 18) — no container needed.
For a durable/production deployment, run real PostgreSQL:

```bash
docker compose up -d          # applies sql/ migrations on first boot
export MFDATAINDIA_DSN="host=127.0.0.1 port=5432 dbname=mfdataindia user=mfdata password=…"
```

> PGlite keeps its working set in memory and flushes to `data/pglite/` on clean
> shutdown (SIGTERM). Always stop it via `make db-stop`, never `kill -9`.

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
