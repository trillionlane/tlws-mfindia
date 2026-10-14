-- OPT-IN ONLY. New immutable sidecars; no source/old-artifact/role/default-ACL edits.
BEGIN;
CREATE SCHEMA mf_analytics;
REVOKE ALL ON SCHEMA mf_analytics FROM PUBLIC;
CREATE TABLE mf_analytics.calendar_results (
    result_id text PRIMARY KEY CHECK(result_id ~ '^[0-9a-f]{64}$'),
    input_hash text NOT NULL REFERENCES mf_review.snapshots,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    engine_fingerprint text NOT NULL CHECK(engine_fingerprint ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    published_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(input_hash,policy_hash,engine_fingerprint)
);
CREATE TABLE mf_analytics.calendar_certifications (
    certificate_id text PRIMARY KEY CHECK(certificate_id ~ '^[0-9a-f]{64}$'),
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    runtime_git_sha text NOT NULL CHECK(runtime_git_sha ~ '^[0-9a-f]{40}$'),
    certified_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX calendar_results_by_input ON mf_analytics.calendar_results(input_hash,published_at DESC,result_id);
CREATE INDEX calendar_certifications_by_completion ON mf_analytics.calendar_certifications(certified_at DESC,certificate_id DESC);
CREATE FUNCTION mf_analytics.seal_calendar_result() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf_analytics,mf_review AS $$
DECLARE input mf_review.snapshots; definition jsonb; body jsonb; BEGIN
    body:=NEW.document-'result_id';
    SELECT * INTO input FROM mf_review.snapshots WHERE input_hash=NEW.input_hash;
    SELECT document INTO definition FROM mf_review.policies WHERE policy_hash=NEW.policy_hash;
    IF NOT (NEW.document->>'format'='calendar-nav-result-v1'
        AND NEW.document->>'result_id'=NEW.result_id AND body->>'input_hash'=NEW.input_hash
        AND body->>'policy_hash'=NEW.policy_hash AND body->>'engine_fingerprint'=NEW.engine_fingerprint
        AND (body->>'code')::integer=input.amfi_scheme_code
        AND (body->>'source_revision')::bigint=input.source_revision
        AND body->'dataset_version'=input.document->'dataset_version'
        AND body->'context'=input.document->'context'
        AND (body->>'points')::integer=jsonb_array_length(input.document->'observations')
        AND body->>'first_date' IS NOT DISTINCT FROM input.document->'observations'->0->>'date'
        AND body->>'as_of' IS NOT DISTINCT FROM input.document->'observations'->-1->>'date'
        AND definition->>'id'='growth-calendar-nav-returns'
        AND body->>'engine_version'=definition->>'engine_version'
        AND body->'engine'->>'version'=body->>'engine_version'
        AND jsonb_typeof(body->'calendar')='object') IS TRUE THEN
        RAISE EXCEPTION 'calendar result lineage differs'; END IF;
    IF NEW.result_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(body),'UTF8')),'hex')
        OR NEW.engine_fingerprint IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(body->'engine'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'calendar result or engine seal differs'; END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION mf_analytics.seal_calendar_certification() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf_analytics,mf_review AS $$
DECLARE cert jsonb; evidence jsonb; member jsonb; result mf_analytics.calendar_results; input mf_review.snapshots; BEGIN
    cert:=NEW.document->'certificate'; evidence:=cert->'evidence';
    IF NEW.certificate_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document),'UTF8')),'hex')
        OR cert->>'sha256' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(cert-'sha256'),'UTF8')),'hex')
        OR cert->>'manifest_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document->'manifest'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'calendar certificate seal differs'; END IF;
    IF NOT (NEW.document->>'format'='calendar-analytics-certification-v1'
        AND NEW.document->>'policy_hash'=NEW.policy_hash
        AND NEW.document->'manifest'->>'git_sha'=NEW.runtime_git_sha
        AND (NEW.document->'execution'->'status'->>'completionTime')::timestamptz=NEW.certified_at
        AND cert->>'format'='mfdata-review-rollout-evidence-v1' AND cert->>'stage'='calendar-http'
        AND evidence->>'level'='DEV_PRIVATE_HTTP' AND (evidence->>'source_writes')::integer=0
        AND evidence->>'policy_hash'=NEW.policy_hash AND evidence->>'origin'=evidence->>'audience'
        AND jsonb_typeof(evidence->'cohort')='array' AND jsonb_array_length(evidence->'cohort') BETWEEN 1 AND 10
        AND (SELECT count(*) FROM jsonb_array_elements(evidence->'cohort'))=
            (SELECT count(DISTINCT item->>'code') FROM jsonb_array_elements(evidence->'cohort') item)) IS TRUE THEN
        RAISE EXCEPTION 'calendar certificate scope differs'; END IF;
    FOR member IN SELECT * FROM jsonb_array_elements(evidence->'cohort') LOOP
        SELECT * INTO result FROM mf_analytics.calendar_results WHERE result_id=member->>'result_id';
        IF NOT FOUND THEN RAISE EXCEPTION 'calendar certified result absent'; END IF;
        SELECT * INTO input FROM mf_review.snapshots WHERE input_hash=result.input_hash;
        IF NOT ((member->>'code')::integer=input.amfi_scheme_code
            AND member->>'input_hash'=result.input_hash AND result.policy_hash=NEW.policy_hash) IS TRUE THEN
            RAISE EXCEPTION 'calendar certificate/result/input lineage differs'; END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER calendar_result_sealed BEFORE INSERT ON mf_analytics.calendar_results
    FOR EACH ROW EXECUTE FUNCTION mf_analytics.seal_calendar_result();
CREATE TRIGGER calendar_certificate_sealed BEFORE INSERT ON mf_analytics.calendar_certifications
    FOR EACH ROW EXECUTE FUNCTION mf_analytics.seal_calendar_certification();
CREATE TRIGGER calendar_result_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_analytics.calendar_results
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER calendar_certificate_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_analytics.calendar_certifications
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_analytics FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_analytics FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    -- Restrict NEW objects even when the owner's production default grants
    -- give unfamiliar ingestion roles write access. Existing grants stay intact.
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_analytics FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_analytics FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_analytics FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_analytics TO %I',principal);
            EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA mf_analytics TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT INSERT ON mf_analytics.calendar_results TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
