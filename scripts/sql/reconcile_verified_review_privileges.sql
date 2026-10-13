-- Explicit opt-in, administrator-only, AFTER separately approved role creation.
-- No identity creation, no existing role/grant changes, no source DML grants.
DO $$ DECLARE principal text; membership record; relation record; BEGIN
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            RAISE EXCEPTION 'required optional principal % is absent',principal;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal AND rolsuper)
           OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname IN('mf','mf_review') AND pg_get_userbyid(c.relowner)=principal) THEN
            RAISE EXCEPTION 'optional principal % must not own relations or be superuser',principal;
        END IF;
        FOR membership IN SELECT parent.rolname FROM pg_auth_members m
            JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member
            WHERE child.rolname=principal LOOP
            EXECUTE format('REVOKE %I FROM %I',membership.rolname,principal);
        END LOOP;
        EXECUTE format('ALTER ROLE %I NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',principal);
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf,mf_review FROM %I',principal);
        -- Table-level REVOKE does not remove separately granted column ACLs.
        FOR relation IN SELECT n.nspname,c.relname,string_agg(quote_ident(a.attname),',' ORDER BY a.attnum) AS columns
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            WHERE n.nspname IN('mf','mf_review') AND c.relkind IN('r','p','v','m')
            GROUP BY n.nspname,c.relname LOOP
            EXECUTE format('REVOKE ALL PRIVILEGES (%s) ON TABLE %I.%I FROM %I',relation.columns,relation.nspname,relation.relname,principal);
        END LOOP;
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA mf,mf_review FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf,mf_review FROM %I',principal);
        EXECUTE format('REVOKE CREATE ON SCHEMA mf,mf_review,public FROM %I',principal);
        EXECUTE format('REVOKE CREATE,TEMPORARY ON DATABASE %I FROM %I',current_database(),principal);
        EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I',current_database(),principal);
        EXECUTE format('GRANT USAGE ON SCHEMA mf,mf_review TO %I',principal);
        EXECUTE format('GRANT SELECT ON mf.funds,mf.fund_facts,mf.nav_history,mf.nav_source_revisions,'
            'mf.dataset_summary,mf.source_snapshot_runs,mf.quality_flags,mf.nav_quality_assessments TO %I',principal);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA mf_review TO %I',principal);
    END LOOP;
    GRANT INSERT ON mf_review.policies,mf_review.snapshots,mf_review.results TO mfdata_verified_generator;
    GRANT EXECUTE ON FUNCTION mf_review.append_event(integer,text,text,text,text,text,jsonb) TO mfdata_verified_admin;
END $$;
