#!/usr/bin/env bash
#
# Migrate MFDataIndia data from the embedded PGlite engine to a standalone
# PostgreSQL (via Podman).
#
# How it works
#   1. Uses a pg_dump that matches PGlite's server version (>= 18). Because
#      pg_dump refuses to run when the client is older than the server, we run
#      it inside a `postgres:18` container that connects to the host PGlite.
#   2. Dumps the `mf` schema (DDL) and data separately, then loads both into a
#      fresh `postgres:18` target container so types/partitions/functions line
#      up exactly with the source.
#
# Requirements
#   - podman with a running machine (podman machine start)
#   - PGlite running on 127.0.0.1:5433   (make db-server / node db/server.mjs)
#   - host reachable from containers as host.containers.internal
#
# Usage
#   ./scripts/migrate_pglite_to_postgres.sh
#
set -euo pipefail

PGHOST='host.containers.internal'   # how a container reaches the macOS host
PGPORT=5433                         # PGlite port
TARGET_CONTAINER='mf-postgres-prod'
TARGET_IMG='postgres:18'
TARGET_DB='mfdataindia'
TARGET_PWD='secret'

echo "==> 1/5 Exporting schema from PGlite ($PGHOST:$PGPORT) ..."
podman run --rm "$TARGET_IMG" \
  pg_dump -h "$PGHOST" -p "$PGPORT" -U postgres -d postgres \
  --schema-only --no-owner --no-privileges --schema=mf > /tmp/mf_schema.sql
echo "    schema: $(wc -l < /tmp/mf_schema.sql) lines"

echo "==> 2/5 Exporting data from PGlite ..."
podman run --rm "$TARGET_IMG" \
  pg_dump -h "$PGHOST" -p "$PGPORT" -U postgres -d postgres \
  --data-only --no-owner --no-privileges --schema=mf --format=plain > /tmp/mf_data.sql
echo "    data: $(du -h /tmp/mf_data.sql | awk '{print $1}')"

echo "==> 3/5 Creating a fresh $TARGET_IMG target container ($TARGET_CONTAINER) ..."
# Recreate the target so the DDL loads into a clean database. If you have data
# you must NOT lose, review this step before running.
podman stop "$TARGET_CONTAINER" 2>/dev/null || true
podman rm   "$TARGET_CONTAINER" 2>/dev/null || true
podman run --name "$TARGET_CONTAINER" \
  -e POSTGRES_PASSWORD="$TARGET_PWD" -e POSTGRES_DB="$TARGET_DB" \
  -p 5432:5432 -d "$TARGET_IMG" >/dev/null

echo "==> 4/5 Loading schema + data ..."
for i in $(seq 1 30); do
  podman exec "$TARGET_CONTAINER" pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 1
done
podman exec -i "$TARGET_CONTAINER" psql -U postgres -d "$TARGET_DB" \
  -v ON_ERROR_STOP=1 < /tmp/mf_schema.sql
podman exec -i "$TARGET_CONTAINER" psql -U postgres -d "$TARGET_DB" \
  -v ON_ERROR_STOP=1 < /tmp/mf_data.sql
echo "    loaded."

echo "==> 5/5 Verifying row counts ..."
podman exec "$TARGET_CONTAINER" psql -U postgres -d "$TARGET_DB" -t -c "
  SELECT 'nav_history', count(*) FROM mf.nav_history
  UNION ALL SELECT 'funds', count(*) FROM mf.funds
  UNION ALL SELECT 'fund_facts', count(*) FROM mf.fund_facts
  UNION ALL SELECT 'fund_holdings', count(*) FROM mf.fund_holdings;
"

rm -f /tmp/mf_schema.sql /tmp/mf_data.sql
echo "✅ Migration complete. Start the app with:  ./start.sh"
