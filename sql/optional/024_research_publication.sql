-- OPT-IN ONLY. No source data, schema, prior artifact, role or default-ACL edits.
-- The caller stores the immutable publication policy in the EXISTING policy
-- registry in this same transaction. No legacy source migration-ledger append.
BEGIN;
CREATE TABLE mf_review.certifications (
    certificate_id text PRIMARY KEY CHECK(certificate_id ~ '^[0-9a-f]{64}$'),
    publication_policy_hash text NOT NULL REFERENCES mf_review.policies,
    runtime_git_sha text NOT NULL CHECK(runtime_git_sha ~ '^[0-9a-f]{40}$'),
    certified_at timestamptz NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX certifications_by_completion ON mf_review.certifications(certified_at DESC,certificate_id DESC);
CREATE FUNCTION mf_review.seal_certification() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf_review AS $$
DECLARE cert jsonb; evidence jsonb; member jsonb; result mf_review.results; input mf_review.snapshots;
BEGIN
    IF NEW.certificate_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'certification record hash differs'; END IF;
    cert:=NEW.document->'certificate'; evidence:=cert->'evidence';
    IF NOT (NEW.document->>'format'='verified-research-certification-v1'
        AND NEW.document->>'publication_policy_hash'=NEW.publication_policy_hash
        AND NEW.document->'manifest'->>'git_sha'=NEW.runtime_git_sha
        AND (NEW.document->'execution'->'status'->>'completionTime')::timestamptz=NEW.certified_at
        AND cert->>'format'='mfdata-review-rollout-evidence-v1' AND cert->>'stage'='certification'
        AND evidence->>'level'='DEV_PRIVATE_HTTP' AND (evidence->>'endpoints')::integer=6
        AND (evidence->>'source_writes')::integer=0 AND evidence->>'insights_rbac_certified'='false'
        AND jsonb_typeof(evidence->'cohort')='array' AND jsonb_array_length(evidence->'cohort') BETWEEN 1 AND 10
        AND evidence->>'origin'=evidence->>'audience') IS TRUE THEN
        RAISE EXCEPTION 'not service-origin provider certification evidence'; END IF;
    IF cert->>'sha256' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(cert-'sha256'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'source certificate seal differs'; END IF;
    IF cert->>'manifest_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document->'manifest'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'source certificate manifest binding differs'; END IF;
    IF (SELECT count(*) FROM jsonb_array_elements(evidence->'cohort')) <>
       (SELECT count(DISTINCT item->>'code') FROM jsonb_array_elements(evidence->'cohort') item) THEN
        RAISE EXCEPTION 'duplicate certified fund'; END IF;
    FOR member IN SELECT * FROM jsonb_array_elements(evidence->'cohort') LOOP
        SELECT * INTO result FROM mf_review.results WHERE result_id=member->>'result_id';
        IF NOT FOUND THEN RAISE EXCEPTION 'certified result absent'; END IF;
        SELECT * INTO input FROM mf_review.snapshots WHERE input_hash=result.input_hash;
        IF NOT ((member->>'code')::integer=input.amfi_scheme_code
                AND member->>'input_hash'=result.input_hash
                AND result.policy_hash=NEW.document->'manifest'->>'policy_hash') IS TRUE THEN
            RAISE EXCEPTION 'certificate/result/input lineage differs'; END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER certification_sealed BEFORE INSERT ON mf_review.certifications
    FOR EACH ROW EXECUTE FUNCTION mf_review.seal_certification();
CREATE TRIGGER certification_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_review.certifications
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON mf_review.certifications FROM PUBLIC;
REVOKE ALL ON FUNCTION mf_review.seal_certification() FROM PUBLIC;
-- Revoke ALL inherited owner-default privileges on this NEW object only,
-- including unfamiliar source roles. None of the runtime roles may INSERT.
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT r.rolname FROM pg_roles r WHERE r.oid<>current_user::regrole
        AND (has_table_privilege(r.oid,'mf_review.certifications','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
             OR has_function_privilege(r.oid,'mf_review.seal_certification()','EXECUTE')) LOOP
        EXECUTE format('REVOKE ALL ON mf_review.certifications FROM %I',principal);
        EXECUTE format('REVOKE ALL ON FUNCTION mf_review.seal_certification() FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT SELECT ON mf_review.certifications TO %I',principal);
        END IF;
    END LOOP;
END $$;
COMMIT;
