-- Optional reported observations only. No source DML or analytics eligibility.
BEGIN;
CREATE SCHEMA mf_idcw;
REVOKE ALL ON SCHEMA mf_idcw FROM PUBLIC;
CREATE TABLE mf_idcw.sources (
    source_id text PRIMARY KEY CHECK(source_id ~ '^[0-9a-f]{64}$'),
    content bytea NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 16777216),
    metadata jsonb NOT NULL CHECK(jsonb_typeof(metadata)='object')
);
CREATE TABLE mf_idcw.snapshots (
    snapshot_id text PRIMARY KEY CHECK(snapshot_id ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL CHECK(amfi_scheme_code IN(101763,118954,102001,119017)),
    history_source_id text NOT NULL REFERENCES mf_idcw.sources,
    registry_source_id text NOT NULL REFERENCES mf_idcw.sources,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object')
);
CREATE FUNCTION mf_idcw.seal_source() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'idcw source operation busy';
    END IF;
    IF NOT (
        NEW.metadata->>'role' IN('history','registry')
        AND NEW.metadata - ARRAY['role','source'] = '{}'::jsonb
        AND NEW.metadata#>>'{source,content_hash}'=encode(sha256(NEW.content),'hex')
        AND substring(NEW.content FROM 1 FOR 5)=convert_to('%PDF-','UTF8')
    ) IS TRUE OR NEW.source_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.metadata),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'idcw source seal differs';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION mf_idcw.seal_snapshot() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE doc jsonb:=NEW.document; current_identity jsonb; item jsonb; role text; key text; source jsonb; BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'idcw snapshot operation busy';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM mf_idcw.snapshots WHERE snapshot_id=NEW.snapshot_id)
       AND (SELECT count(*) FROM mf_idcw.snapshots WHERE amfi_scheme_code=NEW.amfi_scheme_code)>=100 THEN
        RAISE EXCEPTION 'idcw snapshot capacity exceeded';
    END IF;
    SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(v ORDER BY v) FROM
          (SELECT DISTINCT btrim(x) AS v FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL AND btrim(x)<>'') s),'[]'::jsonb))
        INTO current_identity FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=NEW.amfi_scheme_code;
    IF NOT (
        doc->>'format'='reported-idcw-observations-v2'
        AND doc->>'snapshot_id'=NEW.snapshot_id AND doc->>'code'=NEW.amfi_scheme_code::text
        AND doc->>'policy_hash'=NEW.policy_hash
        AND NEW.policy_hash='aadacdd45f0ca5822cc558dcd1e85e21864fd2040d8657e9524f719ac7f82326'
        AND doc->>'engine_code_hash'='3760d0b05089d1a1711f52cee023152fba1dfd84cb09bc7e43a7d9f013faa423'
        AND doc->'identity'=current_identity AND doc#>'{request,identity}'=current_identity
        AND doc->>'identity_hash'=encode(sha256(convert_to(mf_review.canonical_json(current_identity),'UTF8')),'hex')
        AND current_identity->>'plan' IN('REGULAR','DIRECT') AND current_identity->>'option'='IDCW'
        AND doc->>'coverage_state'='unknown'
        AND doc->'complete_event_window_proven'='false'::jsonb
        AND doc->'analytics_eligible'='false'::jsonb AND doc->'freshness_certified'='false'::jsonb
        AND doc->'universe_complete'='false'::jsonb AND doc->'source_data_replaced'='false'::jsonb
        AND doc#>>'{window,start}'=doc#>>'{request,proposed_start}'
        AND doc#>>'{window,end}'=doc#>>'{request,proposed_end}'
        AND doc#>>'{request,format}'='reported-idcw-request-v2'
        AND doc->>'history_reporting_month'=doc#>>'{request,history_period}'
        AND (doc#>>'{window,end}')::date-(doc#>>'{window,start}')::date BETWEEN 0 AND 365
        AND jsonb_typeof(doc->'events')='array' AND jsonb_array_length(doc->'events')<=20
        AND doc->'conditional_announcement'='null'::jsonb
        AND doc->>'announcement_evidence'='not_captured_in_this_snapshot'
        AND doc->>'adapter'='hdfc-idcw-history-v1' AND doc#>>'{request,adapter}'='hdfc-idcw-history-v1'
        AND current_identity IN (SELECT value FROM mf_review.policies p,
            jsonb_array_elements(p.document#>'{adapters,hdfc-idcw-history-v1,cohort}')
            WHERE p.policy_hash=NEW.policy_hash)
        AND doc#>>'{identity_evidence,amfi_code}'=NEW.amfi_scheme_code::text
        AND doc#>>'{identity_evidence,scheme}'=current_identity->>'scheme_name'
        AND doc#>>'{identity_evidence,plan}'=current_identity->>'plan'
        AND doc#>>'{identity_evidence,option}'='IDCW'
        AND jsonb_build_array(doc#>>'{identity_evidence,payout_isin}',doc#>>'{identity_evidence,reinvestment_isin}')=current_identity->'isins'
    ) IS TRUE OR NEW.snapshot_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(doc-'snapshot_id'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'idcw snapshot seal differs';
    END IF;
    FOREACH role IN ARRAY ARRAY['history','registry'] LOOP
        key:=CASE role WHEN 'history' THEN NEW.history_source_id ELSE NEW.registry_source_id END;
        SELECT metadata INTO source FROM mf_idcw.sources WHERE source_id=key;
        IF NOT (source->>'role'=role AND source->'source'=doc#>ARRAY['request',role]
          AND source->'source'=((doc#>ARRAY['source_lineage',role])-ARRAY['byte_length','text_hashes'])) IS TRUE THEN
            RAISE EXCEPTION 'idcw source lineage differs';
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
        ) IS TRUE THEN RAISE EXCEPTION 'idcw observation cannot assert inferred inputs'; END IF;
    END LOOP;
    IF (SELECT count(*)<>count(DISTINCT value->>'record_date') FROM jsonb_array_elements(doc->'events')) THEN
        RAISE EXCEPTION 'duplicate idcw record date';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER idcw_source_sealed BEFORE INSERT ON mf_idcw.sources FOR EACH ROW EXECUTE FUNCTION mf_idcw.seal_source();
CREATE TRIGGER idcw_snapshot_sealed BEFORE INSERT ON mf_idcw.snapshots FOR EACH ROW EXECUTE FUNCTION mf_idcw.seal_snapshot();
CREATE TRIGGER idcw_source_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_idcw.sources FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER idcw_snapshot_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_idcw.snapshots FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_idcw FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_idcw FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_idcw FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_idcw FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_idcw FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_idcw TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_idcw.snapshots TO %I',principal);
            EXECUTE format('GRANT SELECT(source_id,metadata) ON mf_idcw.sources TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA mf_idcw TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
