-- 000_schema_migrations.sql
-- Forward-only migration ledger. A restored production snapshot is baselined
-- explicitly after its schema manifest is verified; historical migrations are
-- never replayed blindly against an existing schema.

CREATE SCHEMA IF NOT EXISTS mf;

CREATE TABLE IF NOT EXISTS mf.schema_migrations (
    migration_name text PRIMARY KEY,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE mf.schema_migrations IS
    'Forward-only migration ledger. Checksums make edited applied migrations fail closed.';
