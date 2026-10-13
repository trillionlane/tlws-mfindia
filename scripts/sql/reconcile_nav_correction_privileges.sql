-- Run as the migration administrator AFTER explicitly provisioning the role.
-- Does not create a role, secret, identity or cloud resource.
DO $$ DECLARE membership record; BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_nav_correction_writer') THEN
        RAISE EXCEPTION 'dedicated correction principal is not provisioned';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_nav_correction_writer' AND rolsuper)
       OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='mf' AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname='mfdata_nav_correction_writer')) THEN
        RAISE EXCEPTION 'correction principal must not be superuser or own relations';
    END IF;
    FOR membership IN SELECT parent.rolname FROM pg_auth_members m
        JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member
        WHERE child.rolname='mfdata_nav_correction_writer' LOOP
        EXECUTE format('REVOKE %I FROM mfdata_nav_correction_writer',membership.rolname);
    END LOOP;
    ALTER ROLE mfdata_nav_correction_writer NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    REVOKE ALL ON ALL TABLES IN SCHEMA mf FROM mfdata_nav_correction_writer;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA mf FROM mfdata_nav_correction_writer;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf FROM mfdata_nav_correction_writer;
    -- Table-level REVOKE does not remove old column-specific grants.
    FOR membership IN SELECT c.relname,a.attname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='mf' AND c.relkind IN('r','p','v','f') AND a.attnum>0 AND NOT a.attisdropped LOOP
        EXECUTE format('REVOKE INSERT(%I),UPDATE(%I),REFERENCES(%I) ON mf.%I FROM mfdata_nav_correction_writer',
            membership.attname,membership.attname,membership.attname,membership.relname);
    END LOOP;
    REVOKE CREATE ON SCHEMA mf,public FROM mfdata_nav_correction_writer;
    EXECUTE format('REVOKE CREATE,TEMPORARY ON DATABASE %I FROM mfdata_nav_correction_writer',current_database());
    GRANT USAGE ON SCHEMA mf TO mfdata_nav_correction_writer;
    GRANT SELECT ON mf.nav_history,mf.funds,mf.amcs,mf.fund_variants,mf.fund_family,mf.fund_facts,
        mf.quality_flags,mf.source_metadata,mf.source_snapshot_runs,mf.dataset_summary,mf.nav_quality_assessments,
        mf.v_coverage,mf.v_enrichment_coverage,
        mf.nav_source_revisions,mf.nav_calculations,mf.nav_remediation_snapshots,mf.nav_remediation_proposals,mf.nav_correction_approvals,
        mf.nav_remediation_operations TO mfdata_nav_correction_writer;
    GRANT INSERT(amfi_scheme_code,source,fetched_at) ON mf.fund_facts TO mfdata_nav_correction_writer;
    GRANT INSERT,UPDATE ON mf.nav_calculations,mf.dataset_summary TO mfdata_nav_correction_writer;
    GRANT INSERT,UPDATE,DELETE ON mf.nav_quality_assessments TO mfdata_nav_correction_writer;
    GRANT INSERT ON mf.nav_remediation_operations TO mfdata_nav_correction_writer;
    GRANT SELECT ON mf.nav_remediation_decisions TO mfdata_nav_correction_writer;
    GRANT EXECUTE ON FUNCTION mf.correct_nav_batch(text,bigint,boolean),
        mf.refresh_dataset_summary(text,text) TO mfdata_nav_correction_writer;
END $$;
