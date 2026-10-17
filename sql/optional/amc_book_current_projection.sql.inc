-- Retain the exact accepted v3 graph; aggregate equal-date book assertions.
CREATE OR REPLACE VIEW mf_live_facts.field_versions AS
WITH active_authority AS MATERIALIZED (
    SELECT source.document FROM mf_live_facts.configuration active_config
    JOIN mf_review.policies policy USING(policy_hash)
    CROSS JOIN LATERAL (
        SELECT document FROM mf_book_facts.configuration source
        WHERE source.configuration_hash=policy.document->>'book_source_configuration_hash'
        ORDER BY source.generation DESC LIMIT 1
    ) source
    WHERE active_config.generation=(SELECT max(generation) FROM mf_live_facts.configuration)
), registered_specs AS MATERIALIZED (
    SELECT source.key AS source_key,source.value->'preserved_projection_codes' AS preserved_codes,
        encode(sha256(convert_to(mf_review.canonical_json(source.value),'UTF8')),'hex') AS specification_hash
    FROM active_authority authority
    CROSS JOIN LATERAL jsonb_each(authority.document->'source_specifications') source
)
SELECT * FROM mf_book_facts.prior_field_versions
UNION ALL
SELECT code,field,as_of,
    CASE WHEN field IN('aum','base_expense_ratio') AND bool_and(usable)
        THEN to_jsonb(trim_scale(((jsonb_agg(value ORDER BY batch_id))->>0)::numeric)::text)
        ELSE (jsonb_agg(value ORDER BY batch_id))->0 END AS value,
    count(DISTINCT value)::integer AS distinct_values,bool_and(usable) AS usable,
    jsonb_agg(evidence ORDER BY batch_id) AS sources
FROM (
    SELECT f.amfi_scheme_code AS code,k.key AS field,b.disclosure_as_of AS as_of,b.batch_id,
        CASE WHEN k.key IN('aum','base_expense_ratio') AND k.value->>'status'='accepted'
            THEN to_jsonb(trim_scale((k.value->>'value')::numeric)::text) ELSE k.value->'value' END AS value,
        k.value->>'status'='accepted' AS usable,
        jsonb_build_object('snapshot_id',b.batch_id,'source_namespace','amc-book',
            'code',f.amfi_scheme_code,'batch_id',b.batch_id,'source_url',b.document->>'source_url',
            'content_hash',b.content_hash,'identity_content_hash',b.identity_content_hash,
            'source_anchor',k.value->>'source_anchor','date_basis',k.value->>'date_basis',
            'raw_value',k.value->>'raw_value','unit',k.value->>'unit','scope',k.value->>'scope',
            'status',k.value->>'status','reason',k.value->>'reason',
            'field_effective_date',k.value->>'field_effective_date',
            'detail_page',x.value->'result'->'detail_page','changes_page',x.value->'result'->'changes_page') AS evidence
    FROM mf_book_facts.batches b
    JOIN registered_specs registered ON registered.source_key=b.source_key
        AND registered.specification_hash=b.document->>'specification_hash'
    CROSS JOIN LATERAL jsonb_array_elements(b.document->'observations') x
    CROSS JOIN LATERAL jsonb_each(x.value->'result'->'fields') k
    JOIN mf.funds f ON f.amfi_scheme_code=(x.value->'identity'->>'code')::integer
    JOIN mf.amcs a USING(amc_id)
    WHERE EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.status<>'failed' AND r.document->'batch_ids' ? b.batch_id)
    AND NOT (registered.preserved_codes @> jsonb_build_array(f.amfi_scheme_code))
    AND NOT EXISTS(SELECT 1 FROM mf_book_facts.prior_field_versions p WHERE p.code=f.amfi_scheme_code)
    AND f.in_scope
    AND x.value->'identity'=jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM (SELECT DISTINCT btrim(z) AS i
            FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) z WHERE z IS NOT NULL) identifiers),'[]'::jsonb))
) assertions GROUP BY code,field,as_of;
