-- =============================================================================
-- MFDataIndia — least-privilege NAV-quality audit principal
-- Depends on: 016 (nav_quality_assessments), 018 (fund_family)
--
-- The audit reads authoritative source tables but may write only the derived
-- assessment table. Cloud SQL built-in users initially inherit
-- cloudsqlsuperuser, so both provisioning and this migration strip it before
-- any audit job is admitted.
-- =============================================================================

BEGIN;

DO $$
BEGIN
    -- Migration 016 temporarily allowed the ingestion principal to maintain
    -- this derived table. DATA-01 moves that responsibility to a distinct
    -- runtime, so remove the old write path even when the optional audit
    -- principal has not been provisioned yet.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_ingest') THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON mf.nav_quality_assessments FROM mfdata_ingest';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_audit') THEN
        RAISE NOTICE 'mfdata_audit is not provisioned; grants remain dormant';
        RETURN;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cloudsqlsuperuser') THEN
        EXECUTE 'REVOKE cloudsqlsuperuser FROM mfdata_audit';
    END IF;
    EXECUTE 'ALTER ROLE mfdata_audit NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS';
    EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM mfdata_audit', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO mfdata_audit', current_database());
    EXECUTE 'REVOKE CREATE ON SCHEMA public FROM mfdata_audit';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA mf FROM mfdata_audit';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA mf FROM mfdata_audit';
    EXECUTE 'GRANT USAGE ON SCHEMA mf TO mfdata_audit';
    EXECUTE 'GRANT SELECT ON mf.nav_history, mf.funds, mf.fund_variants, '
            'mf.dataset_summary, mf.fund_family, mf.nav_quality_assessments '
            'TO mfdata_audit';
    EXECUTE 'GRANT INSERT, UPDATE, DELETE ON mf.nav_quality_assessments TO mfdata_audit';
END
$$;

COMMIT;
