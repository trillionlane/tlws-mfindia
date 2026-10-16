-- Opt-in reversible current projection. No existing table rows are changed.
BEGIN;
CREATE SCHEMA mf_live_facts;
REVOKE ALL ON SCHEMA mf_live_facts FROM PUBLIC;
CREATE TABLE mf_live_facts.configuration (
    generation integer PRIMARY KEY CHECK(generation>0),
    baseline_definition text NOT NULL,
    baseline_sha256 text NOT NULL CHECK(baseline_sha256 ~ '^[0-9a-f]{64}$'),
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    view_hashes jsonb NOT NULL CHECK(jsonb_typeof(view_hashes)='object'),
    view_definitions jsonb NOT NULL CHECK(jsonb_typeof(view_definitions)='object')
);
CREATE TRIGGER live_configuration_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
    ON mf_live_facts.configuration FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
DO $$ DECLARE definition text; BEGIN
    SELECT pg_get_viewdef('mf.current_fund_facts'::regclass,true) INTO definition;
    EXECUTE 'CREATE VIEW mf_live_facts.baseline AS '||definition;
END $$;

-- A newer publication cannot inherit an older identity mapping. No fuzzy matches.
CREATE VIEW mf_live_facts.field_versions AS
WITH assertions AS (
    SELECT s.amfi_scheme_code AS code,k.key AS field,s.disclosure_as_of,
        k.value->'value' AS value,
        CASE k.key
            WHEN 'base_expense_ratio' THEN CASE
                WHEN jsonb_typeof(k.value->'value')='string' AND k.value->>'value' ~ '^[0-9]{1,2}(\.[0-9]{1,4})?$'
                THEN (k.value->>'value')::numeric BETWEEN 0 AND 10 ELSE false END
            WHEN 'benchmark_name' THEN jsonb_typeof(k.value->'value')='string'
                AND length(btrim(k.value->>'value')) BETWEEN 1 AND 500
            WHEN 'fund_managers' THEN CASE WHEN jsonb_typeof(k.value->'value')='array'
                THEN jsonb_array_length(k.value->'value') BETWEEN 1 AND 10
                    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(k.value->'value') item
                        WHERE jsonb_typeof(item)<>'string' OR length(btrim(item#>>'{}')) NOT BETWEEN 1 AND 150)
                ELSE false END
            ELSE false END AS usable,
        jsonb_build_object('snapshot_id',s.snapshot_id,'source_url',s.document->>'source_url',
            'content_hash',s.document->>'content_hash','source_anchor',k.value->>'source_anchor',
            'date_basis',k.value->>'date_basis','raw_value',k.value->>'raw_value',
            'unit',k.value->>'unit','scope',k.value->>'scope') AS evidence
    FROM mf_official_facts.snapshots s
    JOIN mf.funds f ON f.amfi_scheme_code=s.amfi_scheme_code
    JOIN mf.amcs a USING(amc_id)
    CROSS JOIN LATERAL jsonb_each(s.document->'fields') k
    WHERE k.key IN('benchmark_name','fund_managers','base_expense_ratio')
      AND k.value->>'status'='accepted' AND k.value->'value'<>'null'::jsonb
      AND s.document->'identity'=jsonb_build_object('code',f.amfi_scheme_code,
        'amc',a.amfi_amc_name,'scheme_name',f.scheme_name,'plan',f.plan_type,
        'option',f.option_type,'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[
                f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x
                WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
)
SELECT a.code,a.field,a.disclosure_as_of AS as_of,
    CASE WHEN a.field='base_expense_ratio' AND bool_and(a.usable)
        THEN to_jsonb(trim_scale(((jsonb_agg(a.value ORDER BY a.evidence->>'snapshot_id'))->>0)::numeric)::text)
        ELSE (jsonb_agg(a.value ORDER BY a.evidence->>'snapshot_id'))->0 END AS value,
    count(DISTINCT CASE WHEN a.field='base_expense_ratio' AND a.usable THEN to_jsonb((a.value#>>'{}')::numeric) ELSE a.value END)::integer AS distinct_values,
    bool_and(a.usable) AS usable,
    jsonb_agg(a.evidence ORDER BY a.evidence->>'snapshot_id') AS sources
FROM assertions a GROUP BY a.code,a.field,a.disclosure_as_of;

CREATE VIEW mf_live_facts.field_heads AS
    SELECT DISTINCT ON(code,field) * FROM mf_live_facts.field_versions ORDER BY code,field,as_of DESC;
CREATE VIEW mf_live_facts.selected_fields AS
    SELECT DISTINCT ON(code,field) * FROM mf_live_facts.field_versions WHERE distinct_values=1 AND usable ORDER BY code,field,as_of DESC;

CREATE VIEW mf_live_facts.current_values AS
SELECT code,jsonb_object_agg(key,value) AS values FROM (
    SELECT code,'benchmark_name' AS key,value FROM mf_live_facts.selected_fields WHERE field='benchmark_name'
    UNION ALL SELECT code,'benchmark',value FROM mf_live_facts.selected_fields WHERE field='benchmark_name'
    UNION ALL SELECT code,'fund_manager_name',to_jsonb((SELECT string_agg(x,'; ' ORDER BY ordinal) FROM jsonb_array_elements_text(value) WITH ORDINALITY AS names(x,ordinal)))
        FROM mf_live_facts.selected_fields WHERE field='fund_managers'
    UNION ALL SELECT code,'base_expense_ratio',value FROM mf_live_facts.selected_fields WHERE field='base_expense_ratio'
) fields GROUP BY code;

DO $$ DECLARE projection text; BEGIN
    SELECT string_agg(CASE
        WHEN attname='amfi_scheme_code' THEN 'COALESCE(b.amfi_scheme_code,v.code) AS amfi_scheme_code'
        WHEN attname IN('benchmark_name','benchmark','fund_manager_name','base_expense_ratio')
            THEN format('COALESCE((v.values->>%L)::%s,b.%I) AS %I',attname,format_type(atttypid,atttypmod),attname,attname)
        ELSE format('b.%I',attname) END,',' ORDER BY attnum)
    INTO projection FROM pg_attribute WHERE attrelid='mf.current_fund_facts'::regclass
        AND attnum>0 AND NOT attisdropped;
    EXECUTE 'CREATE VIEW mf_live_facts.resolved AS SELECT '||projection||
        ' FROM mf_live_facts.baseline b FULL JOIN mf_live_facts.current_values v ON v.code=b.amfi_scheme_code';
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA mf_live_facts FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_live_facts FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_live_facts FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_generator','mfdata_verified_reader','mfdata_verified_admin'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=principal) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA mf_live_facts TO %I',principal);
            EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA mf_live_facts TO %I',principal);
        END IF;
    END LOOP;
END $$;
COMMIT;
