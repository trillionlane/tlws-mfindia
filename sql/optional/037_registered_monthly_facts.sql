-- Explicit owner-only opt-in. Frozen pilot schemas and source tables are untouched.
BEGIN;
CREATE SCHEMA mf_multi_facts;
REVOKE ALL ON SCHEMA mf_multi_facts FROM PUBLIC;
CREATE TABLE mf_multi_facts.configuration (
    generation integer PRIMARY KEY CHECK(generation>0),
    registry_hash text NOT NULL,
    registry jsonb NOT NULL,
    policy_hash text NOT NULL,
    CHECK(registry_hash=encode(sha256(convert_to(mf_review.canonical_json(registry),'UTF8')),'hex'))
);
CREATE TABLE mf_multi_facts.sources (
    content_hash text PRIMARY KEY CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    source_url text NOT NULL,
    byte_length integer NOT NULL CHECK(byte_length BETWEEN 1 AND 8388608),
    content bytea NOT NULL,
    CHECK(octet_length(content)=byte_length AND encode(sha256(content),'hex')=content_hash)
);
CREATE TABLE mf_multi_facts.snapshots (
    snapshot_id text PRIMARY KEY CHECK(snapshot_id ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL,
    content_hash text NOT NULL REFERENCES mf_multi_facts.sources,
    identity_content_hash text NOT NULL REFERENCES mf_multi_facts.sources,
    disclosure_as_of date NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=1048576)
);
CREATE TABLE mf_multi_facts.runs (
    run_id text PRIMARY KEY CHECK(run_id ~ '^[0-9a-f]{64}$'),
    execution_id text NOT NULL UNIQUE CHECK(execution_id ~ '^tlws-mf-official-facts-refresh-dev-[a-z0-9-]{1,40}$'),
    status text NOT NULL CHECK(status IN('updated','unchanged','failed')),
    observed_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=65536)
);
CREATE TABLE mf_multi_facts.transitions (
    transition_id text PRIMARY KEY CHECK(transition_id ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    CHECK(transition_id=encode(sha256(convert_to(mf_review.canonical_json(document-'transition_id'),'UTF8')),'hex'))
);
CREATE FUNCTION mf_multi_facts.seal_input() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE cfg jsonb; ph text; item jsonb; ident jsonb; row_count integer; BEGIN
    SELECT registry->'sources'->1,policy_hash INTO cfg,ph FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1;
    IF cfg IS NULL THEN RAISE EXCEPTION 'registered source configuration required'; END IF;
    IF TG_TABLE_NAME='sources' THEN
        IF NOT EXISTS(SELECT 1 FROM jsonb_each(jsonb_build_object('edition',cfg->'edition','identity',cfg->'identity_source')) x
            WHERE x.value->>'url'=NEW.source_url AND x.value->>'sha256'=NEW.content_hash) THEN
            RAISE EXCEPTION 'original not authorized by reviewed source registry';
        END IF;
    ELSIF TG_TABLE_NAME='snapshots' THEN
        IF NEW.snapshot_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'snapshot_id'),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'registered snapshot seal differs'; END IF;
        IF NOT (NEW.document->>'format'='registered-monthly-facts-v1'
            AND NEW.document->>'snapshot_id'=NEW.snapshot_id
            AND NEW.document->>'source_key'=cfg->>'key'
            AND NEW.document->>'policy_hash'=ph
            AND NEW.document->'identity'=cfg->'identity'
            AND NEW.document->>'content_hash'=NEW.content_hash
            AND NEW.document->>'identity_content_hash'=NEW.identity_content_hash
            AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
            AND NEW.document->>'source_url'=cfg->'edition'->>'url'
            AND NEW.document->>'identity_source_url'=cfg->'identity_source'->>'url'
            AND NEW.document->'code'=to_jsonb(NEW.amfi_scheme_code)
            AND NEW.content_hash=cfg->'edition'->>'sha256'
            AND NEW.identity_content_hash=cfg->'identity_source'->>'sha256'
            AND NEW.disclosure_as_of=(cfg->'edition'->>'disclosure_as_of')::date
            AND NEW.document->>'verification_status'='needs_verification'
            AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
            AND (SELECT jsonb_object_agg(key,value->'value') FROM jsonb_each(NEW.document->'fields') WHERE key<>'risk_level')=cfg->'reviewed_values'
            AND NOT EXISTS(SELECT 1 FROM jsonb_each(NEW.document->'fields') x WHERE x.key<>'risk_level' AND x.value->>'status' IS DISTINCT FROM 'accepted')
            AND NEW.document->'fields'->'risk_level'->>'status'='unavailable'
            AND NEW.document->'fields'->'risk_level'->'value'='null'::jsonb
            AND (SELECT array_agg(key ORDER BY key) FROM jsonb_each(NEW.document->'fields'))=ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','risk_level']::text[]
        ) IS TRUE THEN RAISE EXCEPTION 'snapshot registered source/cohort/policy differs'; END IF;
    ELSIF TG_TABLE_NAME='runs' THEN
        IF NEW.run_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'run_id'),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'registered run content seal differs'; END IF;
        IF NOT (NEW.document->>'format'='registered-facts-run-v1'
            AND NEW.document->>'run_id'=NEW.run_id
            AND NEW.document->>'execution_id'=NEW.execution_id
            AND NEW.document->>'status'=NEW.status
            AND (NEW.document->>'observed_at')::timestamptz=NEW.observed_at
            AND NEW.document->>'source_key'=cfg->>'key'
            AND NEW.document->>'policy_hash'=ph
            AND NEW.document->'source_rows_changed'='0'::jsonb
            AND NEW.document->'all_amc_data_complete'='false'::jsonb
        ) IS TRUE THEN RAISE EXCEPTION 'registered run seal differs'; END IF;
        IF NEW.status='failed' THEN
            IF NEW.document->'snapshot_ids' IS DISTINCT FROM '[]'::jsonb THEN
                RAISE EXCEPTION 'failed run cannot publish'; END IF;
            RETURN NEW;
        END IF;
        IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
            RAISE EXCEPTION 'another governed data operation owns publication'; END IF;
        SELECT count(*) INTO row_count FROM mf_multi_facts.snapshots s
            WHERE NEW.document->'snapshot_ids' ? s.snapshot_id
              AND s.document->'identity'=cfg->'identity'
              AND s.amfi_scheme_code=(cfg->'cohort'->>0)::integer
              AND s.disclosure_as_of<=NEW.observed_at::date;
        IF row_count<>1 OR NEW.document->'snapshot_ids' IS NULL OR jsonb_array_length(NEW.document->'snapshot_ids')<>1 THEN
            RAISE EXCEPTION 'complete registered cohort receipt required'; END IF;
        SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
            'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
            'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM (SELECT DISTINCT btrim(x) AS i
                FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
            INTO ident FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=(cfg->'cohort'->>0)::integer;
        IF ident IS DISTINCT FROM cfg->'identity' THEN RAISE EXCEPTION 'current registered scheme identity changed'; END IF;
        IF jsonb_array_length(NEW.document->'archives') IS DISTINCT FROM 2 THEN RAISE EXCEPTION 'both immutable archives required'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'archives') LOOP
            IF NOT (item->>'uri'='gs://tlws_mf_data_source/'||(cfg->>'archive_prefix')||(item->>'sha256')||'.pdf'
                AND item->>'generation' ~ '^[1-9][0-9]*$'
                AND EXISTS(SELECT 1 FROM mf_multi_facts.sources s WHERE s.content_hash=item->>'sha256' AND s.byte_length=(item->>'size')::integer)
            ) IS TRUE THEN RAISE EXCEPTION 'archive receipt binding differs'; END IF;
        END LOOP;
        IF (SELECT count(DISTINCT value->>'sha256') FROM jsonb_array_elements(NEW.document->'archives'))<>2 THEN
            RAISE EXCEPTION 'distinct monthly and identity originals required'; END IF;
        IF (NEW.status='unchanged') IS DISTINCT FROM EXISTS(SELECT 1 FROM mf_multi_facts.runs r
            WHERE r.status<>'failed' AND r.document->'snapshot_ids'=NEW.document->'snapshot_ids') THEN
            RAISE EXCEPTION 'new versus unchanged publication differs'; END IF;
    ELSE RAISE EXCEPTION 'unmodelled registered source input'; END IF;
    RETURN NEW;
END $$;
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['configuration','sources','snapshots','runs','transitions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_multi_facts.%I FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite()',name||'_immutable',name);
    END LOOP;
    FOREACH name IN ARRAY ARRAY['sources','snapshots','runs'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON mf_multi_facts.%I FOR EACH ROW EXECUTE FUNCTION mf_multi_facts.seal_input()',name||'_sealed',name);
    END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA mf_multi_facts FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_multi_facts FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_multi_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_multi_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_multi_facts FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_multi_facts TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_multi_facts.configuration,mf_multi_facts.snapshots,mf_multi_facts.runs,mf_multi_facts.transitions TO %I',principal);
            EXECUTE format('GRANT SELECT(content_hash,source_url,byte_length) ON mf_multi_facts.sources TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT SELECT,INSERT ON mf_multi_facts.sources TO mfdata_verified_generator;
        GRANT INSERT ON mf_multi_facts.snapshots,mf_multi_facts.runs TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
