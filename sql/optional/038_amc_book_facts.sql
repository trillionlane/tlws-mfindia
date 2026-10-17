-- Owner-only opt-in. No original relation or prior facts layer is written.
BEGIN;
CREATE SCHEMA mf_book_facts;
REVOKE ALL ON SCHEMA mf_book_facts FROM PUBLIC;
CREATE TABLE mf_book_facts.configuration (
    generation integer PRIMARY KEY CHECK(generation>0),
    configuration_hash text NOT NULL,
    document jsonb NOT NULL,
    CHECK(configuration_hash=encode(sha256(convert_to(mf_review.canonical_json(document),'UTF8')),'hex'))
);
CREATE TABLE mf_book_facts.sources (
    source_key text NOT NULL,
    content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    source_url text NOT NULL,
    media_type text NOT NULL CHECK(media_type IN('application/pdf','text/html','application/json')),
    byte_length integer NOT NULL CHECK(byte_length BETWEEN 1 AND 16777216),
    content bytea NOT NULL,
    PRIMARY KEY(source_key,content_hash),
    CHECK(octet_length(content)=byte_length AND encode(sha256(content),'hex')=content_hash)
);
CREATE TABLE mf_book_facts.batches (
    batch_id text PRIMARY KEY CHECK(batch_id ~ '^[0-9a-f]{64}$'),
    source_key text NOT NULL,
    content_hash text NOT NULL,
    identity_content_hash text NOT NULL,
    disclosure_as_of date NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=16777216),
    FOREIGN KEY(source_key,content_hash) REFERENCES mf_book_facts.sources,
    FOREIGN KEY(source_key,identity_content_hash) REFERENCES mf_book_facts.sources
);
CREATE TABLE mf_book_facts.runs (
    run_id text PRIMARY KEY CHECK(run_id ~ '^[0-9a-f]{64}$'),
    source_key text NOT NULL,
    execution_id text NOT NULL CHECK(execution_id ~ '^tlws-mf-official-facts-refresh-dev-[a-z0-9-]{1,40}$'),
    status text NOT NULL CHECK(status IN('updated','unchanged','failed')),
    observed_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=131072),
    UNIQUE(source_key,execution_id)
);
CREATE TABLE mf_book_facts.transitions (
    transition_id text PRIMARY KEY CHECK(transition_id ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL,
    CHECK(transition_id=encode(sha256(convert_to(mf_review.canonical_json(document-'transition_id'),'UTF8')),'hex'))
);
CREATE FUNCTION mf_book_facts.seal_input() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE spec jsonb; item jsonb; field jsonb; pair record; pop jsonb; ids jsonb; b record; n integer; BEGIN
    SELECT document->'source_specifications'->NEW.source_key INTO spec
        FROM mf_book_facts.configuration ORDER BY generation DESC LIMIT 1;
    IF spec IS NULL THEN RAISE EXCEPTION 'registered whole-AMC source required'; END IF;
    IF TG_TABLE_NAME='sources' THEN
        IF NOT ((NEW.media_type='application/pdf' AND substring(NEW.content FROM 1 FOR 5)=convert_to('%PDF-','UTF8') AND
            ((starts_with(NEW.source_url,spec->>'book_prefix') AND NEW.source_url ~ '/[A-Za-z0-9_.-]+\.pdf$')
             OR EXISTS(SELECT 1 FROM jsonb_array_elements(spec->'identity_documents') x
                WHERE x->>'url'=NEW.source_url AND x->>'sha256'=NEW.content_hash)))
            OR (NEW.byte_length<=1048576 AND ((NEW.media_type='text/html' AND NEW.source_url=spec->>'catalogue_landing')
                OR (NEW.media_type='application/json' AND NEW.source_url ~ (spec->>'catalogue_api_pattern'))))) IS TRUE THEN
            RAISE EXCEPTION 'original URL, media or identity pin not authorized';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='batches' THEN
        IF NOT (NEW.batch_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'batch_id'),'UTF8')),'hex')
            AND NEW.document->>'batch_id'=NEW.batch_id
            AND NEW.document->>'format'='official-amc-book-batch-v1'
            AND NEW.document->>'source_key'=NEW.source_key
            AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
            AND NEW.document->>'content_hash'=NEW.content_hash
            AND NEW.document->>'identity_content_hash'=NEW.identity_content_hash
            AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
            AND NEW.disclosure_as_of=(date_trunc('month',NEW.disclosure_as_of)+interval '1 month - 1 day')::date
            AND NEW.document->>'verification_status'='needs_verification'
            AND NEW.document->'source_rows_changed'='0'::jsonb
            AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
            AND NEW.document->'whole_factsheet_semantic_coverage'='false'::jsonb
            AND NEW.document->'adjusted_idcw_analytics_enabled'='false'::jsonb
            AND NEW.document->>'decoder_policy_hash'=spec->'parser_policy'->>'decoder_policy_hash'
            AND NEW.document->>'extraction_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(NEW.document->'observations')='array'
            AND jsonb_array_length(NEW.document->'observations') BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.content_hash=NEW.content_hash
                AND s.source_url=NEW.document->>'source_url' AND s.media_type='application/pdf' AND starts_with(s.source_url,spec->>'book_prefix'))
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(spec->'identity_documents') x WHERE x->>'sha256'=NEW.identity_content_hash)
        ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC batch source/policy/seal differs'; END IF;
        SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer),count(DISTINCT x->'identity'->>'code')
            INTO pop,n FROM jsonb_array_elements(NEW.document->'observations') x;
        IF n<>jsonb_array_length(NEW.document->'observations') OR
            NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'unique population seal required'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'observations') LOOP
            IF NOT (item->'identity'->>'amc'=spec->>'amc'
                AND (item->'identity'->>'plan'=spec->>'plan' OR (item->'result'->>'status'='gap' AND item->'result'->>'reason'='unsupported_current_identity'))
                AND item->'result'->>'status' IN('usable','gap')
                AND (item->'result'->>'status'='usable')=EXISTS(SELECT 1 FROM jsonb_each(item->'result'->'fields') k WHERE k.value->>'status'='accepted')
                AND (item->'result'->>'status'='usable' OR length(item->'result'->>'reason')>0)
                AND (SELECT array_agg(key ORDER BY key) FROM jsonb_each(item->'result'->'fields'))=ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','risk_level']::text[]
            ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC observation shape differs'; END IF;
            IF item->'result'->>'status'='usable' THEN
                IF NOT (jsonb_typeof(item->'result'->'identity_evidence')='array'
                    AND jsonb_array_length(item->'result'->'identity_evidence') BETWEEN 1 AND 10
                    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(item->'result'->'identity_evidence') e WHERE NOT (
                        e->>'code'=item->'identity'->>'code' AND item->'identity'->'isins' ? (e->>'isin')
                        AND e->>'source_hash'=NEW.identity_content_hash AND e->>'text_hash' ~ '^[0-9a-f]{64}$'
                        AND e->>'page' ~ '^[1-9][0-9]{0,2}$') IS TRUE)
                    AND item->'result'->>'detail_text_hash' ~ '^[0-9a-f]{64}$'
                    AND item->'result'->>'detail_page' ~ '^[1-9][0-9]{0,2}$'
                ) IS TRUE THEN RAISE EXCEPTION 'usable fields require exact code/ISIN page evidence'; END IF;
            END IF;
            FOR pair IN SELECT key,value FROM jsonb_each(item->'result'->'fields') LOOP
                field=pair.value;
                IF NOT (field->>'disclosure_as_of'=NEW.disclosure_as_of::text AND field->>'status' IN('accepted','unavailable')) IS TRUE THEN
                    RAISE EXCEPTION 'field status/date differs'; END IF;
                IF field->>'status'='unavailable' THEN
                    IF field->'value' IS DISTINCT FROM 'null'::jsonb OR COALESCE(length(field->>'reason'),0)=0 THEN
                        RAISE EXCEPTION 'unavailable field requires null and gap reason'; END IF;
                ELSE
                    IF NOT (length(field->>'source_anchor')>0 AND length(field->>'raw_value')>0
                        AND field->>'date_basis' IN('explicit_field_date','document_context_not_field_effective_date')
                        AND CASE WHEN field->>'date_basis'='explicit_field_date' THEN field->>'field_effective_date'=NEW.disclosure_as_of::text
                            ELSE field->'field_effective_date'='null'::jsonb END
                        AND CASE pair.key
                            WHEN 'benchmark_name' THEN field->>'unit'='name' AND field->>'scope'='fund' AND jsonb_typeof(field->'value')='string' AND length(btrim(field->>'value')) BETWEEN 1 AND 250
                            WHEN 'fund_managers' THEN field->>'unit'='names' AND field->>'scope'='fund' AND CASE WHEN jsonb_typeof(field->'value')='array' THEN jsonb_array_length(field->'value') BETWEEN 1 AND 15 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(field->'value') z WHERE jsonb_typeof(z)<>'string' OR length(btrim(z#>>'{}')) NOT BETWEEN 1 AND 150) ELSE false END
                            WHEN 'base_expense_ratio' THEN field->>'unit'='percent' AND field->>'scope'='regular_plan' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,2}(\.[0-9]{1,4})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 10 ELSE false END
                            WHEN 'aum' THEN field->>'unit'='INR_crore' AND field->>'scope'='fund' AND field->>'date_basis'='explicit_field_date' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,13}(\.[0-9]{1,6})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 1000000000000 ELSE false END
                            WHEN 'risk_level' THEN field->>'unit'='risk_category' AND field->>'scope'='fund' AND field->>'value' IN('Low','Low to Moderate','Moderate','Moderately High','High','Very High')
                            ELSE false END
                    ) IS TRUE THEN RAISE EXCEPTION 'accepted field type/unit/date/evidence differs'; END IF;
                END IF;
            END LOOP;
        END LOOP;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME<>'runs' THEN RAISE EXCEPTION 'unmodelled book input'; END IF;
    IF NOT (NEW.run_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'run_id'),'UTF8')),'hex')
        AND NEW.document->>'run_id'=NEW.run_id AND NEW.document->>'format'='official-amc-book-run-v1'
        AND NEW.document->>'source_key'=NEW.source_key AND NEW.document->>'execution_id'=NEW.execution_id
        AND NEW.document->>'status'=NEW.status AND (NEW.document->>'observed_at')::timestamptz=NEW.observed_at
        AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
        AND NEW.document->'source_rows_changed'='0'::jsonb AND NEW.document->'all_amc_data_complete'='false'::jsonb
    ) IS TRUE THEN RAISE EXCEPTION 'book run seal differs'; END IF;
    IF NEW.status='failed' THEN
        IF NEW.document->'batch_ids' IS DISTINCT FROM '[]'::jsonb THEN RAISE EXCEPTION 'failed receipt cannot publish'; END IF;
        RETURN NEW;
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'governed publication lock required'; END IF;
    SELECT jsonb_agg(jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,'scheme_name',f.scheme_name,
        'plan',f.plan_type,'option',f.option_type,'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
        ORDER BY f.amfi_scheme_code) INTO pop FROM mf.funds f JOIN mf.amcs a USING(amc_id)
        WHERE a.amfi_amc_name=spec->>'amc' AND f.in_scope;
    IF pop IS NULL OR jsonb_array_length(pop) NOT BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
        OR NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'complete current in-scope AMC population required'; END IF;
    SELECT jsonb_agg(batch_id ORDER BY disclosure_as_of DESC,batch_id),count(*) INTO ids,n
        FROM mf_book_facts.batches WHERE source_key=NEW.source_key AND NEW.document->'batch_ids' ? batch_id;
    IF ids IS DISTINCT FROM NEW.document->'batch_ids' OR n<>(spec->>'period_count')::integer OR
        (SELECT count(DISTINCT disclosure_as_of) FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id)<>n THEN
        RAISE EXCEPTION 'two distinct complete ordered periods required'; END IF;
    FOR b IN SELECT * FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id LOOP
        IF (SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer) FROM jsonb_array_elements(b.document->'observations') x) IS DISTINCT FROM pop
            OR b.disclosure_as_of>NEW.observed_at::date OR b.document->>'specification_hash'<>NEW.document->>'specification_hash' THEN
            RAISE EXCEPTION 'batch current identity/specification differs'; END IF;
    END LOOP;
    IF (SELECT dataset_version::text FROM mf.dataset_summary WHERE singleton) IS DISTINCT FROM NEW.document->>'dataset_version' THEN
        RAISE EXCEPTION 'run live dataset receipt differs'; END IF;
    -- Catalog originals are retained byte-for-byte in the same append-only DB.
    IF NOT (jsonb_typeof(NEW.document->'catalog_hashes')='array' AND jsonb_array_length(NEW.document->'catalog_hashes') BETWEEN (spec->>'minimum_catalogue_originals')::integer AND 3
        AND (SELECT count(DISTINCT content_hash) FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.media_type IN('text/html','application/json') AND NEW.document->'catalog_hashes' ? s.content_hash)=jsonb_array_length(NEW.document->'catalog_hashes')) IS TRUE THEN
        RAISE EXCEPTION 'retained catalogue originals required'; END IF;
    SELECT count(DISTINCT content_hash) INTO n FROM (
        SELECT content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id
        UNION SELECT identity_content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id
    ) originals;
    IF jsonb_array_length(NEW.document->'archives') IS DISTINCT FROM n THEN RAISE EXCEPTION 'all PDF original archives required'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'archives') LOOP
        IF NOT (item->>'uri'='gs://tlws_mf_data_source/'||(spec->>'archive_prefix')||(item->>'sha256')||'.pdf'
            AND item->>'generation' ~ '^[1-9][0-9]*$'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.content_hash=item->>'sha256' AND s.byte_length=(item->>'size')::integer)
        ) IS TRUE THEN RAISE EXCEPTION 'archive binding differs'; END IF;
    END LOOP;
    IF (SELECT count(DISTINCT x->>'sha256') FROM jsonb_array_elements(NEW.document->'archives') x WHERE x->>'sha256' IN(
        SELECT content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id
        UNION SELECT identity_content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id))<>n THEN
        RAISE EXCEPTION 'distinct complete archived originals required'; END IF;
    IF (NEW.status='unchanged') IS DISTINCT FROM EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.source_key=NEW.source_key
        AND r.status<>'failed' AND r.document->'batch_ids'=NEW.document->'batch_ids') THEN RAISE EXCEPTION 'new versus replay status differs'; END IF;
    RETURN NEW;
END $$;
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['configuration','sources','batches','runs','transitions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_book_facts.%I FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite()',name||'_immutable',name);
    END LOOP;
    FOREACH name IN ARRAY ARRAY['sources','batches','runs'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON mf_book_facts.%I FOR EACH ROW EXECUTE FUNCTION mf_book_facts.seal_input()',name||'_sealed',name);
    END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA mf_book_facts FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_book_facts FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_book_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_book_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_book_facts FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_book_facts TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_book_facts.configuration,mf_book_facts.batches,mf_book_facts.runs,mf_book_facts.transitions TO %I',principal);
            EXECUTE format('GRANT SELECT(source_key,content_hash,source_url,media_type,byte_length) ON mf_book_facts.sources TO %I',principal);
        END IF;
    END LOOP;
    GRANT SELECT,INSERT ON mf_book_facts.sources TO mfdata_verified_generator;
    GRANT INSERT ON mf_book_facts.batches,mf_book_facts.runs TO mfdata_verified_generator;
END $$;
COMMIT;
