-- Additive operational receipts and owner-only projection recovery history.
BEGIN;
CREATE SCHEMA mf_fact_refresh;
REVOKE ALL ON SCHEMA mf_fact_refresh FROM PUBLIC;
CREATE TABLE mf_fact_refresh.catalogs (
    catalog_id text PRIMARY KEY CHECK(catalog_id ~ '^[0-9a-f]{64}$'),
    source_url text NOT NULL CHECK(source_url LIKE 'https://mutualfund.adityabirlacapital.com/%'),
    media_type text NOT NULL CHECK(media_type IN('text/html','application/json')),
    content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    content bytea NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 1048576),
    CHECK(content_hash=encode(sha256(content),'hex'))
);
CREATE TABLE mf_fact_refresh.runs (
    run_id text PRIMARY KEY CHECK(run_id ~ '^[0-9a-f]{64}$'),
    execution_id text NOT NULL UNIQUE CHECK(length(execution_id) BETWEEN 1 AND 120),
    observed_at timestamptz NOT NULL,
    status text NOT NULL CHECK(status IN('updated','unchanged','completed_with_field_gaps','failed')),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=262144),
    CHECK((document->>'execution_id'=execution_id AND document->>'status'=status AND document->>'run_id'=run_id) IS TRUE)
);
CREATE INDEX fact_refresh_time ON mf_fact_refresh.runs(observed_at DESC,run_id);
CREATE TABLE mf_fact_refresh.publications (
    publication_id text PRIMARY KEY REFERENCES mf_fact_refresh.runs(run_id),
    content_hash text NOT NULL REFERENCES mf_monthly_facts.sources,
    disclosure_as_of date NOT NULL,
    snapshot_ids jsonb NOT NULL CHECK(jsonb_typeof(snapshot_ids)='array' AND jsonb_array_length(snapshot_ids)=2),
    UNIQUE(content_hash,disclosure_as_of,snapshot_ids)
);
CREATE TABLE mf_fact_refresh.transitions (
    transition_id text PRIMARY KEY CHECK(transition_id ~ '^[0-9a-f]{64}$'),
    from_generation integer NOT NULL CHECK(from_generation>0),
    to_generation integer NOT NULL UNIQUE CHECK(to_generation>from_generation),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    CHECK((document->>'transition_id'=transition_id AND (document->>'from_generation')::integer=from_generation AND (document->>'to_generation')::integer=to_generation) IS TRUE),
    CHECK(transition_id=encode(sha256(convert_to(mf_review.canonical_json(document-'transition_id'),'UTF8')),'hex'))
);
CREATE TRIGGER refresh_runs_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_fact_refresh.runs
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER refresh_transitions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_fact_refresh.transitions
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER refresh_catalogs_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_fact_refresh.catalogs
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER refresh_publications_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_fact_refresh.publications
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE FUNCTION mf_fact_refresh.seal_input() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF TG_TABLE_NAME='catalogs' THEN
        IF NEW.catalog_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(jsonb_build_object(
            'source_url',NEW.source_url,'media_type',NEW.media_type,'content_hash',NEW.content_hash)),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'official catalog seal differs';
        END IF;
    ELSIF TG_TABLE_NAME='runs' THEN
        IF NEW.run_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'run_id'),'UTF8')),'hex')
           OR (NEW.document->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at
           OR NOT (NEW.document->>'format'='official-facts-refresh-run-v1'
                   AND NEW.document->>'policy_hash'='f9b15e40c8b8f9f367b1fed7dd7dd9afc5fa0e140b75b8fa524ce7083555fdda'
                   AND NEW.document->'source_rows_changed'='0'::jsonb) IS TRUE THEN
            RAISE EXCEPTION 'source refresh run seal/policy differs';
        END IF;
    ELSE RAISE EXCEPTION 'unmodelled refresh input'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER refresh_catalog_sealed BEFORE INSERT ON mf_fact_refresh.catalogs FOR EACH ROW EXECUTE FUNCTION mf_fact_refresh.seal_input();
CREATE TRIGGER refresh_run_sealed BEFORE INSERT ON mf_fact_refresh.runs FOR EACH ROW EXECUTE FUNCTION mf_fact_refresh.seal_input();
CREATE FUNCTION mf_fact_refresh.publish_run() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ids jsonb; checksum text; as_of date; matched integer; codes integer[]; BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'another governed data operation owns the publication lock';
    END IF;
    IF NEW.status='failed' THEN RETURN NEW; END IF;
    IF NOT (NEW.document->>'mode'='scheduled_refresh' OR
            (NEW.document->>'mode'='retained_bootstrap' AND session_user IN('postgres','mfdata_admin'))) IS TRUE THEN
        RAISE EXCEPTION 'approved source refresh or direct owner bootstrap required';
    END IF;
    IF NEW.document->>'mode'='scheduled_refresh' AND NOT (
        jsonb_array_length(NEW.document->'catalog_ids')=2 AND
        (SELECT count(*) FROM mf_fact_refresh.catalogs c WHERE NEW.document->'catalog_ids' ? c.catalog_id)=2
    ) IS TRUE THEN RAISE EXCEPTION 'retained official catalog evidence required'; END IF;
    ids=NEW.document->'snapshot_ids'; checksum=NEW.document->>'content_hash';
    as_of=(NEW.document->>'disclosure_as_of')::date;
    IF jsonb_typeof(ids) IS DISTINCT FROM 'array' OR jsonb_array_length(ids)<>2 THEN
        RAISE EXCEPTION 'complete two-variant source cohort required';
    END IF;
    SELECT count(*)::integer,array_agg(s.amfi_scheme_code ORDER BY s.amfi_scheme_code)
    INTO matched,codes FROM mf_monthly_facts.snapshots s JOIN mf.funds f ON f.amfi_scheme_code=s.amfi_scheme_code JOIN mf.amcs a USING(amc_id)
    WHERE ids ? s.snapshot_id AND s.content_hash=checksum AND s.disclosure_as_of=as_of
      AND s.policy_hash='813c1649be01699cb1d4d205bb5d0b844faa72a08c487be333e0793212cd0f0b'
      AND s.document->'identity'=jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM (SELECT DISTINCT btrim(x) AS i
            FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb));
    IF matched<>2 OR codes<>ARRAY[103173,103174]::integer[] THEN
        RAISE EXCEPTION 'publication snapshot source/date/current identity differs';
    END IF;
    IF NEW.status='unchanged' THEN
        IF NOT EXISTS(SELECT 1 FROM mf_fact_refresh.publications p WHERE p.content_hash=checksum AND p.disclosure_as_of=as_of AND p.snapshot_ids @> ids AND ids @> p.snapshot_ids) THEN
            RAISE EXCEPTION 'unchanged requires an existing exact publication';
        END IF;
    ELSE
        IF EXISTS(SELECT 1 FROM mf_fact_refresh.publications p WHERE p.content_hash=checksum AND p.disclosure_as_of=as_of AND p.snapshot_ids @> ids AND ids @> p.snapshot_ids) THEN
            RAISE EXCEPTION 'existing publication must be reported unchanged';
        END IF;
        INSERT INTO mf_fact_refresh.publications VALUES(NEW.run_id,checksum,as_of,ids);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER refresh_run_publication AFTER INSERT ON mf_fact_refresh.runs FOR EACH ROW EXECUTE FUNCTION mf_fact_refresh.publish_run();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_fact_refresh FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_fact_refresh FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_fact_refresh FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_fact_refresh FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_fact_refresh FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_fact_refresh TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_fact_refresh.runs,mf_fact_refresh.publications,mf_fact_refresh.transitions TO %I',principal);
            EXECUTE format('GRANT SELECT(catalog_id,source_url,media_type,content_hash) ON mf_fact_refresh.catalogs TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT INSERT ON mf_fact_refresh.runs TO mfdata_verified_generator;
        GRANT SELECT,INSERT ON mf_fact_refresh.catalogs TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
