-- Opt-in additive disclosure snapshots. No original rows/default privileges changed.
BEGIN;
CREATE SCHEMA mf_official_facts;
REVOKE ALL ON SCHEMA mf_official_facts FROM PUBLIC;
CREATE TABLE mf_official_facts.snapshots (
    snapshot_id text PRIMARY KEY CHECK(snapshot_id ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL,
    document_id text NOT NULL REFERENCES mf_enrichment.documents,
    identity_document_id text NOT NULL REFERENCES mf_enrichment.documents,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    disclosure_as_of date NOT NULL,
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    UNIQUE(amfi_scheme_code,document_id,identity_document_id,policy_hash)
);
CREATE INDEX official_facts_code_date ON mf_official_facts.snapshots(amfi_scheme_code,disclosure_as_of);
CREATE FUNCTION mf_official_facts.seal_snapshot() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source jsonb; registry jsonb; current_identity jsonb; definition jsonb; BEGIN
    SELECT document INTO source FROM mf_enrichment.documents WHERE document_id=NEW.document_id;
    SELECT document INTO registry FROM mf_enrichment.documents WHERE document_id=NEW.identity_document_id;
    SELECT document INTO definition FROM mf_review.policies WHERE policy_hash=NEW.policy_hash;
    SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
          (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
    INTO current_identity FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=NEW.amfi_scheme_code;
    IF NOT (
        NEW.document->>'format'='official-fund-facts-v1'
        AND NEW.document->>'snapshot_id'=NEW.snapshot_id
        AND (NEW.document->>'code')::integer=NEW.amfi_scheme_code
        AND NEW.document->>'document_id'=NEW.document_id
        AND NEW.document->>'identity_document_id'=NEW.identity_document_id
        AND NEW.document->>'policy_hash'=NEW.policy_hash
        AND NEW.policy_hash='05ab0962bfa22af8c3387757f9d406f0cf105aa6571c638dec0cba9fdc0f751b'
        AND definition->>'id'='official-absl-fund-facts' AND definition->>'version'='1'
        AND NEW.document->>'content_hash'=source->>'content_hash'
        AND NEW.document->>'identity_content_hash'=registry->>'content_hash'
        AND NEW.document->>'source_url'=source->'intake'->>'final_url'
        AND NEW.document->>'identity_source_url'=registry->'intake'->>'final_url'
        AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
        AND source->'intake'->>'document_as_of'=NEW.disclosure_as_of::text
        AND source->'intake'->'codes' @> jsonb_build_array(NEW.amfi_scheme_code)
        AND registry->'intake'->'codes' @> jsonb_build_array(NEW.amfi_scheme_code)
        AND NEW.document->>'identity_hash'=encode(sha256(convert_to(mf_review.canonical_json(current_identity),'UTF8')),'hex')
        AND NEW.document->'identity_evidence'->>'amfi_code'=NEW.amfi_scheme_code::text
        AND current_identity->'isins' ? (NEW.document->'identity_evidence'->>'matched_payout_isin')
    ) IS TRUE OR NEW.document->'identity' IS DISTINCT FROM current_identity
      OR NEW.snapshot_id IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'snapshot_id'),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'official facts source/identity seal differs';
    END IF;
    IF NOT (
        (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(NEW.document->'fields') key)
          =ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','total_expense_ratio']::text[]
        AND NEW.document->'fields'->'base_expense_ratio'->>'unit'='percent'
        AND NEW.document->'fields'->'base_expense_ratio'->>'scope'='regular_plan'
        AND NEW.document->'fields'->'benchmark_name'->>'unit'='name'
        AND NEW.document->'fields'->'benchmark_name'->>'scope'='fund'
        AND NEW.document->'fields'->'fund_managers'->>'unit'='names'
        AND NEW.document->'fields'->'fund_managers'->>'scope'='fund'
        AND NOT EXISTS(SELECT 1 FROM jsonb_each(NEW.document->'fields') f WHERE
          (f.value->>'disclosure_as_of'=NEW.disclosure_as_of::text
           AND f.value->>'date_basis'='document_context_not_field_effective_date') IS NOT TRUE)
        AND NOT EXISTS(SELECT 1 FROM jsonb_each(NEW.document->'fields') f
          WHERE f.key IN('benchmark_name','fund_managers','base_expense_ratio') AND
          (f.value->>'status'='accepted' AND f.value->'value'<>'null'::jsonb
           AND jsonb_typeof(f.value->'raw_value')='string'
           AND jsonb_typeof(f.value->'source_anchor')='string'
           AND f.value->'reason'='null'::jsonb) IS NOT TRUE)
        AND NOT EXISTS(SELECT 1 FROM jsonb_each(NEW.document->'fields') f
          WHERE f.key IN('aum','total_expense_ratio') AND
          (f.value->>'status'='unavailable' AND f.value->'value'='null'::jsonb
           AND jsonb_typeof(f.value->'reason')='string') IS NOT TRUE)
    ) IS TRUE THEN RAISE EXCEPTION 'official fact field scope differs'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER official_snapshot_sealed BEFORE INSERT ON mf_official_facts.snapshots FOR EACH ROW EXECUTE FUNCTION mf_official_facts.seal_snapshot();
CREATE TRIGGER official_snapshot_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_official_facts.snapshots FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
REVOKE ALL ON ALL TABLES IN SCHEMA mf_official_facts FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_official_facts FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_official_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_official_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_official_facts FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_official_facts TO %I',principal);
            EXECUTE format('GRANT SELECT ON mf_official_facts.snapshots TO %I',principal);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_verified_generator') THEN
        GRANT INSERT ON mf_official_facts.snapshots TO mfdata_verified_generator;
    END IF;
END $$;
COMMIT;
