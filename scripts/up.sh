#!/usr/bin/env bash
# MFDataIndia — local stack launcher.
#
#   ./scripts/up.sh            # start Postgres (docker compose) + API (http://127.0.0.1:8000)
#   ./scripts/up.sh --api-only # start only the API (assumes Postgres already up)
#
# PostgreSQL is the system of record. Honors $MFDATAINDIA_DSN if you point it at
# your own Postgres; otherwise starts the docker-compose instance on :5432.

set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT="${MF_API_PORT:-8000}"
export MFDATAINDIA_DSN="${MFDATAINDIA_DSN:-postgres://mfdataindia:mfdataindia@localhost:5432/mfdataindia}"

API_ONLY=0
[ "${1:-}" = "--api-only" ] && API_ONLY=1

if [ "$API_ONLY" -eq 0 ]; then
  echo "[up] starting PostgreSQL via docker compose ..."
  docker compose up -d
  echo -n "[up] waiting for postgres "
  for _ in $(seq 1 60); do
    if docker compose exec -T db pg_isready -U mfdataindia >/dev/null 2>&1; then
      echo " ok"; break
    fi
    echo -n "."; sleep 1
  done
  docker compose exec -T db pg_isready -U mfdataindia >/dev/null 2>&1 || {
    echo " ERROR: postgres did not become ready"; exit 1; }
fi

echo "[up] DSN: $MFDATAINDIA_DSN"
echo "[up] starting API on http://127.0.0.1:${API_PORT}"
echo "[up] open  http://127.0.0.1:${API_PORT}"
echo "[up] stop everything with: docker compose down"
PYTHONPATH=src exec python3 -m uvicorn mfdataindia.api.app:create_app --factory \
  --host 127.0.0.1 --port "$API_PORT" --reload
