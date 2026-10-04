#!/usr/bin/env bash
# MFDataIndia — local stack launcher.
#
#   ./scripts/up.sh            # start DB + API (http://127.0.0.1:8000)
#   ./scripts/up.sh --api-only # start only the API (assumes DB already up)
#
# Uses PGlite (embedded real PostgreSQL) under data/pglite. For a durable
# deployment, run real PostgreSQL via `docker compose up` and set MFDATAINDIA_DSN.

set -euo pipefail
cd "$(dirname "$0")/.."

PG_PORT="${MF_PG_PORT:-5433}"
API_PORT="${MF_API_PORT:-8000}"
export MFDATAINDIA_DSN="${MFDATAINDIA_DSN:-host=127.0.0.1 port=${PG_PORT} user=postgres dbname=postgres sslmode=disable}"
# PGlite does not implement the COPY sub-protocol; the store must use batched INSERT.
export MF_TEST_NO_COPY=1

API_ONLY=0
[ "${1:-}" = "--api-only" ] && API_ONLY=1

if [ "$API_ONLY" -eq 0 ]; then
  if nc -z 127.0.0.1 "$PG_PORT" 2>/dev/null; then
    echo "[up] PostgreSQL already listening on :$PG_PORT"
  else
    echo "[up] starting embedded PostgreSQL (PGlite) on :$PG_PORT ..."
    if [ ! -d db/node_modules ]; then
      echo "[up] installing PGlite (first run only) ..."
      (cd db && npm install --no-audit --no-fund --silent)
    fi
    mkdir -p data/pglite
    (cd db && nohup node server.mjs > ../data/pglite/server.log 2>&1 & echo $! > ../data/pglite/server.pid)
    # wait for readiness
    for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PG_PORT" 2>/dev/null && break; sleep 1; done
    echo "[up] DB ready (log: data/pglite/server.log)"
  fi
fi

echo "[up] starting API on http://127.0.0.1:${API_PORT}"
echo "[up] open  http://127.0.0.1:${API_PORT}"
PYTHONPATH=src exec python3 -m uvicorn mfdataindia.api.app:create_app --factory \
  --host 127.0.0.1 --port "$API_PORT"
