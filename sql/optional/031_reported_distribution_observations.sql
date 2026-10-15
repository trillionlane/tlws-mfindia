-- Optional reported observations only. No source DML or analytics eligibility.
BEGIN;
CREATE SCHEMA mf_distributions;
REVOKE ALL ON SCHEMA mf_distributions FROM PUBLIC;
CREATE TABLE mf_distributions.sources (
    source_id text PRIMARY KEY CHECK(source_id ~ '^[0-9a-f]{64}$'),
    content bytea NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 16777216),
    metadata jsonb NOT NULL CHECK(jsonb_typeof(metadata)='object')
);
CREATE TABLE mf_distributions.snapshots (
    snapshot_id text PRIMARY KEY CHECK(snapshot_id ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL CHECK(amfi_scheme_code=101763),
    history_source_id text NOT NULL REFERENCES mf_distributions.sources,
    notice_source_id text NOT NULL REFERENCES mf_distributions.sources,
    registry_source_id text NOT NULL REFERENCES mf_distributions.sources,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object')
);
CREATE FUNCTION mf_distributions.seal_source() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'distribution source operation busy';
    END IF;
    IF NOT (
        NEW.metadata->>'role' IN('history','notice','registry')
        AND NEW.metadata - ARRAY['role','source'] = '{}'::jsonb
        AND NEW.metadata#>>'{source,content_hash}'=encode(sha256(NEW.content),'hex')
        AND substring(NEW.content FROM 1 FOR 5)=convert_to('%PDF-','UTF8')
    ) IS TRUE OR NEW.source_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.metadata),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'distribution source seal differs';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION mf_distributions.seal_snapshot() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE doc jsonb:=NEW.document; current_identity jsonb; item jsonb; role text; key text; source jsonb; announcement jsonb; BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'distribution snapshot operation busy';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM mf_distributions.snapshots WHERE snapshot_id=NEW.snapshot_id)
       AND (SELECT count(*) FROM mf_distributions.snapshots WHERE amfi_scheme_code=NEW.amfi_scheme_code)>=100 THEN
        RAISE EXCEPTION 'distribution snapshot capacity exceeded';
    END IF;
    SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(v ORDER BY v) FROM
          (SELECT DISTINCT btrim(x) AS v FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL AND btrim(x)<>'') s),'[]'::jsonb))
        INTO current_identity FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=NEW.amfi_scheme_code;
    IF NOT (
        doc->>'format'='reported-idcw-observations-v1'
        AND doc->>'snapshot_id'=NEW.snapshot_id AND doc->>'code'=NEW.amfi_scheme_code::text
        AND doc->>'policy_hash'=NEW.policy_hash
        AND NEW.policy_hash='9f56fce56365f7b014fdf7f4e718896b50b7545fe235eabaf0384624bc97581d'
        AND doc->>'engine_code_hash' ~ '^[0-9a-f]{64}$'
        AND doc->'identity'=current_identity AND doc#>'{request,identity}'=current_identity
        AND doc->>'identity_hash'=encode(sha256(convert_to(mf_review.canonical_json(current_identity),'UTF8')),'hex')
        AND current_identity->>'plan'='REGULAR' AND current_identity->>'option'='IDCW'
        AND doc->>'coverage_state'='unknown'
        AND doc->'complete_event_window_proven'='false'::jsonb
        AND doc->'analytics_eligible'='false'::jsonb AND doc->'freshness_certified'='false'::jsonb
        AND doc->'universe_complete'='false'::jsonb AND doc->'source_data_replaced'='false'::jsonb
        AND doc#>>'{window,start}'=doc#>>'{request,proposed_start}'
        AND doc#>>'{window,end}'=doc#>>'{request,proposed_end}'
        AND (doc#>>'{window,end}')::date-(doc#>>'{window,start}')::date BETWEEN 0 AND 365
        AND jsonb_typeof(doc->'events')='array' AND jsonb_array_length(doc->'events')<=20
        AND doc ? 'conditional_announcement'
    ) IS TRUE OR NEW.snapshot_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(doc-'snapshot_id'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'distribution snapshot seal differs';
    END IF;
    FOREACH role IN ARRAY ARRAY['history','notice','registry'] LOOP
        key:=CASE role WHEN 'history' THEN NEW.history_source_id WHEN 'notice' THEN NEW.notice_source_id ELSE NEW.registry_source_id END;
        SELECT metadata INTO source FROM mf_distributions.sources WHERE source_id=key;
        IF NOT (source->>'role'=role AND source->'source'=doc#>ARRAY['request',role]
          AND source->'source'=((doc#>ARRAY['source_lineage',role])-ARRAY['byte_length','text_hashes'])) IS TRUE THEN
            RAISE EXCEPTION 'distribution source lineage differs';
        END IF;
    END LOOP;
    FOR item IN SELECT value FROM jsonb_array_elements(doc->'events') LOOP
        IF NOT (
            (item->>'record_date')::date BETWEEN (doc#>>'{window,start}')::date AND (doc#>>'{window,end}')::date
            AND item->>'amount_basis'='reported_history_not_net_investor_receipt'
            AND item->>'source_role'='history'
            AND item->>'source_page'=doc#>>'{request,history,pages,1}'
            AND item->>'unit_event_coverage'='unknown'
            AND item->'ex_date'='null'::jsonb AND item->'payment_date'='null'::jsonb
            AND item->'reinvestment_date'='null'::jsonb AND item->'reinvestment_nav_inr'='null'::jsonb
            AND item->'scheme_levy_inr_per_unit'='null'::jsonb
            AND item->>'amount_inr_per_unit' ~ '^[0-9]{1,9}(\.[0-9]{1,8})?$'
            AND item->>'amount_others_inr_per_unit' ~ '^[0-9]{1,9}(\.[0-9]{1,8})?$'
            AND item->>'cum_idcw_nav_inr' ~ '^[0-9]{1,9}(\.[0-9]{1,8})?$'
            AND (item->>'amount_inr_per_unit')::numeric>0 AND (item->>'amount_others_inr_per_unit')::numeric>0
            AND (item->>'cum_idcw_nav_inr')::numeric>0
        ) IS TRUE THEN RAISE EXCEPTION 'distribution observation cannot assert inferred inputs'; END IF;
    END LOOP;
    IF (SELECT count(*)<>count(DISTINCT value->>'record_date') FROM jsonb_array_elements(doc->'events')) THEN
        RAISE EXCEPTION 'duplicate distribution record date';
    END IF;
    announcement:=doc->'conditional_announcement';
    IF announcement<>'null'::jsonb AND NOT (
        announcement->'amount_is_conditional'='true'::jsonb
        AND announcement->'record_date_business_day_condition'='true'::jsonb
        AND announcement->'statutory_levy_quantified'='false'::jsonb
        AND announcement->'tds_applicable_rate'='null'::jsonb
        AND announcement->'stamp_duty_quantified'='false'::jsonb
        AND announcement->'reinvestment_nav_inr'='null'::jsonb AND announcement->'reinvestment_date'='null'::jsonb
    ) IS TRUE THEN RAISE EXCEPTION 'distribution conditional announcement differs'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER distribution_source_sealed BEFORE INSERT ON mf_distributions.sources FOR EACH ROW EXECUTE FUNCTION mf_distributions.seal_source();
CREATE TRIGGER distribution_snapshot_sealed BEFORE INSERT ON mf_distributions.snapshots FOR EACH ROW EXECUTE FUNCTION mf_distributions.seal_snapshot();
CREATE TRIGGER distribution_source_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_distributions.sources FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER distribution_snapshot_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_distributions.snapshots FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_distributions FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_distributions FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_distributions FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_distributions FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_distributions FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_distributions TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_distributions.snapshots TO %I',principal);
            EXECUTE format('GRANT SELECT(source_id,metadata) ON mf_distributions.sources TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA mf_distributions TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
