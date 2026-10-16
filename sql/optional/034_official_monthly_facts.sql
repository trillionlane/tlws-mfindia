-- Opt-in monthly reporting sidecar. Existing source rows and v1 contracts stay intact.
BEGIN;
CREATE SCHEMA mf_monthly_facts;
REVOKE ALL ON SCHEMA mf_monthly_facts FROM PUBLIC;
CREATE TABLE mf_monthly_facts.sources (
    content_hash text PRIMARY KEY CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    content bytea NOT NULL CHECK(octet_length(content) BETWEEN 5 AND 16777216),
    byte_length integer NOT NULL CHECK(byte_length BETWEEN 5 AND 16777216),
    intake jsonb NOT NULL CHECK(jsonb_typeof(intake)='object'),
    CHECK(byte_length=octet_length(content)),
    CHECK(substring(content FROM 1 FOR 5)=convert_to('%PDF-','UTF8')),
    CHECK(content_hash=encode(sha256(content),'hex')),
    CHECK((intake->>'expected_sha256'=content_hash) IS TRUE),
    CHECK((intake->>'media_type'='application/pdf') IS TRUE),
    CHECK((intake->>'amc'='Aditya Birla Sun Life Mutual Fund') IS TRUE),
    CHECK((intake->>'source_url'=intake->>'final_url') IS TRUE),
    CHECK((intake->>'final_url' ~ '^https://mutualfund\.adityabirlacapital\.com/-/media/[A-Za-z0-9_./%-]+\.pdf$') IS TRUE),
    CHECK((intake->'codes' @> '[103173,103174]'::jsonb AND jsonb_array_length(intake->'codes')=2) IS TRUE)
);
CREATE TABLE mf_monthly_facts.snapshots (
    snapshot_id text PRIMARY KEY CHECK(snapshot_id ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL CHECK(amfi_scheme_code IN(103173,103174)),
    content_hash text NOT NULL REFERENCES mf_monthly_facts.sources,
    identity_document_id text NOT NULL REFERENCES mf_enrichment.documents,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    disclosure_as_of date NOT NULL,
    identity_hash text NOT NULL CHECK(identity_hash ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=262144),
    UNIQUE(amfi_scheme_code,content_hash,identity_document_id,policy_hash,identity_hash)
);
CREATE INDEX monthly_facts_code_date ON mf_monthly_facts.snapshots(amfi_scheme_code,disclosure_as_of);
CREATE FUNCTION mf_monthly_facts.seal_snapshot() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source jsonb; registry jsonb; identity jsonb; definition jsonb; BEGIN
    SELECT intake INTO source FROM mf_monthly_facts.sources WHERE content_hash=NEW.content_hash;
    SELECT document INTO registry FROM mf_enrichment.documents WHERE document_id=NEW.identity_document_id;
    SELECT document INTO definition FROM mf_review.policies WHERE policy_hash=NEW.policy_hash;
    SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
          (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
    INTO identity FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=NEW.amfi_scheme_code;
    IF NOT (
        NEW.document->>'format'='official-monthly-fund-facts-v1'
        AND NEW.document->>'snapshot_id'=NEW.snapshot_id
        AND (NEW.document->>'code')::integer=NEW.amfi_scheme_code
        AND NEW.document->>'content_hash'=NEW.content_hash
        AND NEW.document->>'identity_document_id'=NEW.identity_document_id
        AND NEW.document->>'policy_hash'=NEW.policy_hash
        AND NEW.policy_hash='813c1649be01699cb1d4d205bb5d0b844faa72a08c487be333e0793212cd0f0b'
        AND definition->>'id'='official-absl-monthly-facts' AND definition->>'version'='1'
        AND NEW.document->>'identity_content_hash'=registry->>'content_hash'
        AND NEW.document->>'source_url'=source->>'final_url'
        AND NEW.document->>'identity_source_url'=registry->'intake'->>'final_url'
        AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
        AND source->>'document_as_of'=NEW.disclosure_as_of::text
        AND registry->'intake'->'codes' @> jsonb_build_array(NEW.amfi_scheme_code)
        AND NEW.identity_hash=encode(sha256(convert_to(mf_review.canonical_json(identity),'UTF8')),'hex')
        AND NEW.document->>'identity_hash'=NEW.identity_hash
        AND NEW.document->'identity_evidence'->>'amfi_code'=NEW.amfi_scheme_code::text
        AND identity->'isins' ? (NEW.document->'identity_evidence'->>'matched_payout_isin')
        AND NEW.document->>'verification_status'='needs_verification'
        AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
        AND jsonb_typeof(NEW.document->'fields')='object'
        AND (SELECT count(*) FROM jsonb_object_keys(NEW.document->'fields')) BETWEEN 1 AND 48
        AND NOT EXISTS(SELECT 1 FROM jsonb_each(NEW.document->'fields') f WHERE
            (f.value->>'disclosure_as_of'=NEW.disclosure_as_of::text
             AND f.value->>'status' IN('accepted','unavailable')
             AND (f.value->>'status'<>'accepted' OR
                  (f.value->'value'<>'null'::jsonb AND jsonb_typeof(f.value->'unit')='string'
                   AND jsonb_typeof(f.value->'scope')='string' AND jsonb_typeof(f.value->'source_anchor')='string')))
            IS NOT TRUE)
    ) IS TRUE OR NEW.document->'identity' IS DISTINCT FROM identity
      OR NEW.snapshot_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'snapshot_id'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'monthly reported facts source/identity seal differs';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER monthly_snapshot_sealed BEFORE INSERT ON mf_monthly_facts.snapshots FOR EACH ROW EXECUTE FUNCTION mf_monthly_facts.seal_snapshot();
CREATE TRIGGER monthly_snapshot_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_monthly_facts.snapshots FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER monthly_source_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_monthly_facts.sources FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_monthly_facts FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_monthly_facts FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_monthly_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_monthly_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_monthly_facts FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_monthly_facts TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_monthly_facts.snapshots TO %I',principal);
            EXECUTE format('GRANT SELECT(content_hash,byte_length,intake) ON mf_monthly_facts.sources TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT SELECT,INSERT ON mf_monthly_facts.sources TO mfdata_verified_generator;
        GRANT INSERT ON mf_monthly_facts.snapshots TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
