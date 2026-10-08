#!/usr/bin/env bash
# Apply the canonical migration list once during PostgreSQL initdb and record
# each content hash. Migration 007 is superseded by 008 and is intentionally
# excluded, matching PostgresStore.DEFAULT_MIGRATIONS.

set -euo pipefail

for migration in /migrations/[0-9][0-9][0-9]_*.sql; do
  name="$(basename "$migration")"
  case "$name" in
    007_*) continue ;;
  esac

  psql --set ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --file "$migration"

  checksum="$(sha256sum "$migration" | awk '{print $1}')"
  psql --set ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set migration_name="$name" \
    --set content_sha256="$checksum" <<'SQL'
INSERT INTO mf.schema_migrations (migration_name, content_sha256)
VALUES (:'migration_name', :'content_sha256')
ON CONFLICT (migration_name) DO UPDATE
SET content_sha256 = EXCLUDED.content_sha256,
    applied_at = now();
SQL
done
