-- Optional append-only namespace. Never changes original or prior sidecar rows.
BEGIN;
CREATE SCHEMA mf_portfolios;
REVOKE ALL ON SCHEMA mf_portfolios FROM PUBLIC;
CREATE TABLE mf_portfolios.sources (
    source_id text PRIMARY KEY CHECK(source_id ~ '^[0-9a-f]{64}$'),
    content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    content bytea NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 8388608),
    intake jsonb NOT NULL CHECK(jsonb_typeof(intake)='object'),
    CHECK(content_hash=encode(sha256(content),'hex')),
    CHECK((intake->>'content_hash'=content_hash) IS TRUE)
);
CREATE TABLE mf_portfolios.snapshots (
    snapshot_id text PRIMARY KEY CHECK(snapshot_id ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL,
    source_id text NOT NULL REFERENCES mf_portfolios.sources,
    identity_source_id text NOT NULL REFERENCES mf_portfolios.sources,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    disclosure_as_of date NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    UNIQUE(amfi_scheme_code,source_id,identity_source_id,policy_hash)
);
CREATE INDEX portfolio_code_date ON mf_portfolios.snapshots(amfi_scheme_code,disclosure_as_of);
CREATE FUNCTION mf_portfolios.seal_source() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF NEW.source_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.intake),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'portfolio original source seal differs';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION mf_portfolios.seal_snapshot() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source jsonb; registry jsonb; current_identity jsonb; definition jsonb; item jsonb; last_row integer:=0; BEGIN
    SELECT intake INTO source FROM mf_portfolios.sources WHERE source_id=NEW.source_id;
    SELECT intake INTO registry FROM mf_portfolios.sources WHERE source_id=NEW.identity_source_id;
    SELECT document INTO definition FROM mf_review.policies WHERE policy_hash=NEW.policy_hash;
    SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
          (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
    INTO current_identity FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=NEW.amfi_scheme_code;
    IF NOT (
        NEW.document->>'format'='official-complete-portfolio-v1'
        AND NEW.document->>'snapshot_id'=NEW.snapshot_id
        AND NEW.document->>'code'=NEW.amfi_scheme_code::text
        AND NEW.document->>'source_id'=NEW.source_id
        AND NEW.document->>'identity_source_id'=NEW.identity_source_id
        AND NEW.document->>'policy_hash'=NEW.policy_hash
        AND NEW.policy_hash='0e571cf2db7a7e9db5d99cf697255babcd20e0406d73fe4639a2791c7baa6a7e'
        AND definition->>'id'='official-absl-complete-portfolios' AND definition->>'version'='1'
        AND NEW.document->>'content_hash'=source->>'content_hash'
        AND NEW.document->>'identity_content_hash'=registry->>'content_hash'
        AND NEW.document->>'source_url'=source->>'source_url'
        AND NEW.document->>'identity_source_url'=registry->>'source_url'
        AND source->>'media_type'='application/zip' AND registry->>'media_type'='application/pdf'
        AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
        AND source->>'disclosure_as_of'=NEW.disclosure_as_of::text
        AND NEW.document->>'identity_hash'=encode(sha256(convert_to(mf_review.canonical_json(current_identity),'UTF8')),'hex')
        AND NEW.document->'identity_evidence'->>'amfi_code'=NEW.amfi_scheme_code::text
        AND NEW.document->'identity_evidence'->>'scope'='fund_shared_portfolio'
        AND current_identity->'isins' ? (NEW.document->'identity_evidence'->>'payout_isin')
        AND NEW.document->>'statement_complete'='true'
        AND jsonb_array_length(NEW.document->'holdings') BETWEEN 1 AND 1000
        AND NEW.document->>'holdings_count'=jsonb_array_length(NEW.document->'holdings')::text
        AND (NEW.document->>'grand_total_inr_lakh')::numeric>0
    ) IS TRUE OR NEW.document->'identity' IS DISTINCT FROM current_identity
      OR NEW.snapshot_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'snapshot_id'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'portfolio source/identity seal differs';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'holdings') LOOP
        IF NOT ((item->>'row')::integer>last_row AND jsonb_typeof(item->'market_value_inr_lakh')='string'
            AND jsonb_typeof(item->'weight_percent')='object' AND jsonb_typeof(item->'name')='string'
            AND item->>'asset_class' IN('equity','debt','government','cash','current_assets_liabilities')) IS TRUE THEN
            RAISE EXCEPTION 'portfolio constituent seal differs';
        END IF;
        last_row:=(item->>'row')::integer;
    END LOOP;
    IF abs((SELECT sum((x->>'market_value_inr_lakh')::numeric) FROM jsonb_array_elements(NEW.document->'holdings') x)
           -(NEW.document->>'grand_total_inr_lakh')::numeric) > 0.005*(jsonb_array_length(NEW.document->'holdings')+1) THEN
        RAISE EXCEPTION 'portfolio total seal differs';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER portfolio_snapshot_sealed BEFORE INSERT ON mf_portfolios.snapshots FOR EACH ROW EXECUTE FUNCTION mf_portfolios.seal_snapshot();
CREATE TRIGGER portfolio_source_sealed BEFORE INSERT ON mf_portfolios.sources FOR EACH ROW EXECUTE FUNCTION mf_portfolios.seal_source();
CREATE TRIGGER portfolio_snapshot_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_portfolios.snapshots FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER portfolio_source_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_portfolios.sources FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_portfolios FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_portfolios FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_portfolios FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_portfolios FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_portfolios FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_portfolios TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_portfolios.snapshots TO %I',principal);
            EXECUTE format('GRANT SELECT(source_id,content_hash,intake) ON mf_portfolios.sources TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT SELECT,INSERT ON mf_portfolios.sources,mf_portfolios.snapshots TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
