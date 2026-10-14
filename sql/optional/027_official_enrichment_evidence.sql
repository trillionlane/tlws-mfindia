-- OPT-IN: private immutable evidence; no original rows/default grants changed.
-- Adds only two source AMC read columns for existing restricted identities.
BEGIN;
CREATE SCHEMA mf_enrichment;
REVOKE ALL ON SCHEMA mf_enrichment FROM PUBLIC;
CREATE TABLE mf_enrichment.blobs (
    content_hash text PRIMARY KEY CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    content bytea NOT NULL CHECK(octet_length(content) BETWEEN 1 AND 8388608),
    CHECK(content_hash=encode(sha256(content),'hex'))
);
CREATE TABLE mf_enrichment.documents (
    document_id text PRIMARY KEY CHECK(document_id ~ '^[0-9a-f]{64}$'),
    content_hash text NOT NULL REFERENCES mf_enrichment.blobs,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mf_enrichment.mappings (
    mapping_id text PRIMARY KEY CHECK(mapping_id ~ '^[0-9a-f]{64}$'),
    document_id text NOT NULL REFERENCES mf_enrichment.documents,
    amfi_scheme_code integer NOT NULL CHECK(amfi_scheme_code>0),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    UNIQUE(document_id,amfi_scheme_code)
);
CREATE INDEX enrichment_mapping_code ON mf_enrichment.mappings(amfi_scheme_code,document_id);
CREATE FUNCTION mf_enrichment.seal_document() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf_enrichment,mf_review AS $$
DECLARE definition jsonb; BEGIN
    SELECT document INTO definition FROM mf_review.policies WHERE policy_hash=NEW.policy_hash;
    IF NOT (NEW.document->>'format'='official-enrichment-document-v1'
        AND NEW.document->>'document_id'=NEW.document_id
        AND NEW.document->>'content_hash'=NEW.content_hash
        AND NEW.document->'intake'->>'expected_sha256'=NEW.content_hash
        AND NEW.document->>'policy_hash'=NEW.policy_hash
        AND NEW.policy_hash='45ddf28f15fb2fa8d4bf77f63509d7f94cc26bb3ee574883117bfde54667c418'
        AND (NEW.document->>'byte_length')::integer=(SELECT octet_length(content) FROM mf_enrichment.blobs WHERE content_hash=NEW.content_hash)
        AND NEW.document->>'publication'='evidence_only_not_accepted_facts'
        AND NEW.document->>'parser_state' IN ('parsed','unsupported')
        AND NEW.document->'intake'->'source_conflict'='false'::jsonb
        AND NEW.document->'intake'->>'use_basis' IN ('operator_reported_authorization','published_permission')
        AND definition->>'id'='official-enrichment-evidence') IS TRUE
        OR NEW.document_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'document_id'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'official evidence seal or scope differs'; END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION mf_enrichment.seal_mapping() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf_enrichment,mf_review AS $$
DECLARE parent jsonb; body jsonb; source_identity jsonb; BEGIN
    SELECT document INTO parent FROM mf_enrichment.documents WHERE document_id=NEW.document_id;
    body:=NEW.document->'mapping';
    SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
        INTO source_identity FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=NEW.amfi_scheme_code;
    IF NOT (NEW.document->>'document_id'=NEW.document_id
        AND (body->>'code')::integer=NEW.amfi_scheme_code
        AND (body->'identity'->>'code')::integer=NEW.amfi_scheme_code
        AND parent->'intake'->'codes' @> jsonb_build_array(NEW.amfi_scheme_code)
        AND body->>'state' IN ('matched_candidate','unmapped')
        AND ((body->>'state'='matched_candidate' AND body->'reason'='null'::jsonb)
             OR (body->>'state'='unmapped' AND jsonb_typeof(body->'reason')='string'))
        AND body->>'identity_hash'=encode(sha256(convert_to(mf_review.canonical_json(body->'identity'),'UTF8')),'hex')) IS TRUE
        OR body->'identity' IS DISTINCT FROM source_identity
        OR (body->>'state'='matched_candidate' AND parent->>'parser_state'<>'parsed')
        OR NEW.mapping_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'official mapping seal or scope differs'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER document_sealed BEFORE INSERT ON mf_enrichment.documents FOR EACH ROW EXECUTE FUNCTION mf_enrichment.seal_document();
CREATE TRIGGER mapping_sealed BEFORE INSERT ON mf_enrichment.mappings FOR EACH ROW EXECUTE FUNCTION mf_enrichment.seal_mapping();
CREATE TRIGGER blob_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_enrichment.blobs FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER document_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_enrichment.documents FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER mapping_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_enrichment.mappings FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_enrichment FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_enrichment FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    -- Revoke inherited production default privileges on NEW objects only.
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_enrichment FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_enrichment FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_enrichment FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_enrichment TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_enrichment.documents,mf_enrichment.mappings TO %I',principal);
            EXECUTE format('GRANT SELECT(amc_id,amfi_amc_name) ON mf.amcs TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT SELECT,INSERT ON mf_enrichment.blobs,mf_enrichment.documents,mf_enrichment.mappings TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
