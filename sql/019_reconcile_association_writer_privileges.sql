-- =============================================================================
-- MFDataIndia — reconcile late-provisioned association-writer privileges
-- Depends on: 018 (association tables)
--
-- The optional writer principal may be provisioned after migration 018 has
-- already been recorded. Reconcile its privileges in a new forward migration
-- so the live role cannot remain elevated but unusable in that sequence.
-- =============================================================================

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_association_writer') THEN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cloudsqlsuperuser') THEN
            EXECUTE 'REVOKE cloudsqlsuperuser FROM mfdata_association_writer';
        END IF;
        EXECUTE 'ALTER ROLE mfdata_association_writer NOCREATEROLE NOCREATEDB';
        EXECUTE format(
            'GRANT CONNECT ON DATABASE %I TO mfdata_association_writer',
            current_database()
        );
        EXECUTE 'GRANT USAGE ON SCHEMA mf TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT ON mf.fund_family TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT, INSERT ON mf.fund_family_association_state TO mfdata_association_writer';
        EXECUTE 'GRANT UPDATE (version, updated_at) ON mf.fund_family_association_state TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT, INSERT ON mf.fund_family_association_tags TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT, INSERT ON mf.association_tag_idempotency TO mfdata_association_writer';
    END IF;
END
$$;

COMMIT;
