-- Optional immutable derived sidecar; no existing table or source row altered.
BEGIN;
CREATE SCHEMA mf_overlap;
REVOKE ALL ON SCHEMA mf_overlap FROM PUBLIC;
CREATE TABLE mf_overlap.results (
    result_id text PRIMARY KEY CHECK(result_id ~ '^[0-9a-f]{64}$'),
    left_snapshot_id text NOT NULL REFERENCES mf_portfolios.snapshots,
    right_snapshot_id text NOT NULL REFERENCES mf_portfolios.snapshots,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    engine_code_hash text NOT NULL CHECK(engine_code_hash ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    UNIQUE(left_snapshot_id,right_snapshot_id,policy_hash,engine_code_hash)
);
CREATE FUNCTION mf_overlap.seal_result() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a jsonb; b jsonb; definition jsonb; BEGIN
    SELECT document INTO a FROM mf_portfolios.snapshots WHERE snapshot_id=NEW.left_snapshot_id;
    SELECT document INTO b FROM mf_portfolios.snapshots WHERE snapshot_id=NEW.right_snapshot_id;
    SELECT document INTO definition FROM mf_review.policies WHERE policy_hash=NEW.policy_hash;
    IF NOT (
        NEW.document->>'format'='reported-isin-overlap-v1'
        AND NEW.document->>'result_id'=NEW.result_id
        AND NEW.document->>'left_snapshot_id'=NEW.left_snapshot_id
        AND NEW.document->>'right_snapshot_id'=NEW.right_snapshot_id
        AND NEW.document->>'policy_hash'=NEW.policy_hash
        AND NEW.policy_hash='79d11ce237df116015d74a9e50df04855d4bffec0a0419835783c428b968b524'
        AND definition->>'id'='reported-isin-weighted-overlap' AND definition->>'version'='1'
        AND NEW.document->>'engine_code_hash'=NEW.engine_code_hash
        AND NEW.document->>'left_code'=a->>'code' AND NEW.document->>'right_code'=b->>'code'
        AND (a->>'code')::integer < (b->>'code')::integer
        AND a->>'disclosure_as_of'=b->>'disclosure_as_of'
        AND NEW.document->>'disclosure_as_of'=a->>'disclosure_as_of'
        AND a->>'statement_complete'='true' AND b->>'statement_complete'='true'
        AND NEW.document->>'scope'='observed_reported_isin_securities_only'
        AND NEW.document->>'full_economic_state'='unavailable'
        AND NEW.document->'full_economic_overlap_percent'='null'::jsonb
        AND NEW.document->>'not_normalized'='true'
        AND jsonb_array_length(NEW.document->'shared_holdings') BETWEEN 0 AND 1000
        AND NEW.document->>'shared_security_count'=jsonb_array_length(NEW.document->'shared_holdings')::text
        AND jsonb_array_length(NEW.document->'coverage')=2
    ) IS TRUE OR NEW.result_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'result_id'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'overlap result/input seal differs';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER overlap_result_sealed BEFORE INSERT ON mf_overlap.results FOR EACH ROW EXECUTE FUNCTION mf_overlap.seal_result();
CREATE TRIGGER overlap_result_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_overlap.results FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_overlap FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_overlap FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_overlap FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_overlap FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_overlap FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_overlap TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_overlap.results TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT INSERT ON mf_overlap.results TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
