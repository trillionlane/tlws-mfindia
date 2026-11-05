-- Current method2 seal. Historical numbered migration 037 remains immutable.
-- Only the existing direct owner installer may replace this private trigger.
CREATE OR REPLACE FUNCTION mf_multi_facts.seal_input() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE cfg jsonb; ph text; item jsonb; ident jsonb; row_count integer; field jsonb; field_name text; listing_hash text; BEGIN
    SELECT registry->'sources'->1,policy_hash INTO cfg,ph FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1;
    IF cfg IS NULL THEN RAISE EXCEPTION 'registered source configuration required'; END IF;
    IF TG_TABLE_NAME='sources' THEN
        IF NOT (NEW.source_url=cfg->'identity_source'->>'url' AND NEW.content_hash=cfg->'identity_source'->>'sha256'
            OR NEW.source_url=cfg->>'listing_url' AND NEW.byte_length<=1048576
            OR NEW.source_url ~ '^https://files[.]hdfcfund[.]com/s3fs-public/20[0-9]{2}-[01][0-9]/HDFC(%20| )MF(%20| )Factsheet(%20| )-(%20| )[A-Za-z]+(%20| )20[0-9]{2}[.]pdf$'
        ) IS TRUE THEN RAISE EXCEPTION 'dynamic original outside fixed issuer authority'; END IF;
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
            AND NEW.document->>'identity_hash'=encode(sha256(convert_to(mf_review.canonical_json(cfg->'identity'),'UTF8')),'hex')
            AND NEW.document->>'engine_hash'=cfg->>'parser_engine_hash'
            AND NEW.document->>'source_url'=(SELECT source_url FROM mf_multi_facts.sources WHERE content_hash=NEW.content_hash)
            AND NEW.document->>'identity_source_url'=cfg->'identity_source'->>'url'
            AND NEW.document->'code'=to_jsonb(NEW.amfi_scheme_code)
            AND NEW.identity_content_hash=cfg->'identity_source'->>'sha256'
            AND NEW.disclosure_as_of=(date_trunc('month',NEW.disclosure_as_of)+interval '1 month - 1 day')::date
            AND NEW.document->'reported_context'->'discovery'->>'actual_reporting_date'=NEW.disclosure_as_of::text
            AND NEW.document->'reported_context'->'discovery'->>'listing_url'=cfg->>'listing_url'
            AND NEW.document->'reported_context'->'discovery'->'identity_document_date_is_not_monthly_fact_date'='true'::jsonb
            AND NEW.document->>'verification_status'='needs_verification'
            AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
            AND NEW.document->'fields'->'risk_level'->>'status'='unavailable'
            AND NEW.document->'fields'->'risk_level'->'value'='null'::jsonb
            AND (SELECT array_agg(key ORDER BY key) FROM jsonb_each(NEW.document->'fields'))=ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','risk_level']::text[]
        ) IS TRUE THEN RAISE EXCEPTION 'snapshot registered source/cohort/policy differs'; END IF;
        listing_hash:=NEW.document->'reported_context'->'discovery'->>'listing_sha256';
        IF NOT EXISTS(SELECT 1 FROM mf_multi_facts.sources WHERE content_hash=listing_hash
            AND source_url=cfg->>'listing_url' AND byte_length<=1048576) THEN
            RAISE EXCEPTION 'dynamic issuer discovery original missing'; END IF;
        IF NOT (jsonb_array_length(NEW.document->'extraction'->'pages')=24
            AND (NEW.document->'selected_panel_pages'->>0)::integer BETWEEN 1 AND 23
            AND (NEW.document->'selected_panel_pages'->>1)::integer=(NEW.document->'selected_panel_pages'->>0)::integer+1
            AND jsonb_array_length(NEW.document->'identity_extraction'->'pages')=1
            AND NEW.document->'identity_extraction'->'pages'->0->>'page'='1'
            AND NEW.document->'extraction'->>'source_hash'=NEW.content_hash
            AND NEW.document->'identity_extraction'->>'source_hash'=NEW.identity_content_hash
        ) IS TRUE THEN RAISE EXCEPTION 'dynamic bounded extraction evidence differs'; END IF;
        FOR field_name,field IN SELECT key,value FROM jsonb_each(NEW.document->'fields') LOOP
            IF NOT (field->>'disclosure_as_of'=NEW.disclosure_as_of::text
                AND field->>'status' IN('accepted','unavailable')
                AND (field->>'status'='accepted' AND field->'value'<>'null'::jsonb
                    AND field->>'raw_value' IS NOT NULL AND field->>'source_anchor' IS NOT NULL
                    AND field->'reason'='null'::jsonb
                    OR field->>'status'='unavailable' AND field->'value'='null'::jsonb
                    AND field->>'reason' IS NOT NULL AND field->'unit'='null'::jsonb AND field->'scope'='null'::jsonb)
            ) IS TRUE THEN RAISE EXCEPTION 'dynamic field status or provenance differs'; END IF;
            IF field->>'status'='accepted' THEN
                IF field_name IN('aum','base_expense_ratio') THEN
                    IF NOT (jsonb_typeof(field->'value')='string' AND field->>'value' ~ '^[0-9]+([.][0-9]+)?$'
                        AND field->>'date_basis'='explicit_field_date' AND field->>'field_effective_date'=NEW.disclosure_as_of::text
                        AND (field_name='aum' AND field->>'unit'='INR_crore' AND field->>'scope'='fund' AND (field->>'value')::numeric>=0
                          OR field_name='base_expense_ratio' AND field->>'unit'='percent' AND field->>'scope'='regular_plan' AND (field->>'value')::numeric BETWEEN 0 AND 10)
                    ) IS TRUE THEN RAISE EXCEPTION 'dynamic reported decimal boundary differs'; END IF;
                ELSIF field_name='benchmark_name' THEN
                    IF NOT (jsonb_typeof(field->'value')='string' AND length(field->>'value') BETWEEN 1 AND 500
                        AND field->>'unit'='name' AND field->>'scope'='fund') IS TRUE THEN
                        RAISE EXCEPTION 'dynamic benchmark boundary differs'; END IF;
                ELSIF field_name='fund_managers' THEN
                    IF NOT (jsonb_typeof(field->'value')='array' AND jsonb_array_length(field->'value') BETWEEN 1 AND 10
                        AND field->>'unit'='names' AND field->>'scope'='fund'
                        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(field->'value') x WHERE jsonb_typeof(x)<>'string' OR length(x#>>'{}') NOT BETWEEN 1 AND 100)
                        AND (SELECT count(DISTINCT x) FROM jsonb_array_elements(field->'value') x)=jsonb_array_length(field->'value')
                        AND (SELECT jsonb_agg(x->'name' ORDER BY n) FROM jsonb_array_elements(NEW.document->'reported_context'->'fund_managers') WITH ORDINALITY t(x,n))=field->'value'
                    ) IS TRUE THEN RAISE EXCEPTION 'dynamic complete manager panel differs'; END IF;
                ELSE RAISE EXCEPTION 'dynamic accepted field not authorized'; END IF;
            END IF;
        END LOOP;
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
              AND s.document->>'policy_hash'=ph
              AND s.document->>'engine_hash'=cfg->>'parser_engine_hash'
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
                AND EXISTS(SELECT 1 FROM mf_multi_facts.snapshots s WHERE NEW.document->'snapshot_ids' ? s.snapshot_id AND item->>'sha256' IN(s.content_hash,s.identity_content_hash))
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
