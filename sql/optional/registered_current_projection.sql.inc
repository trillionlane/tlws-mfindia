-- Add a disjoint registered cohort without rewriting the frozen pilot graph.
CREATE OR REPLACE VIEW mf_live_facts.field_versions AS
SELECT * FROM mf_multi_facts.prior_field_versions
UNION ALL
SELECT s.amfi_scheme_code AS code,k.key AS field,s.disclosure_as_of AS as_of,
    CASE WHEN k.key IN('aum','base_expense_ratio') AND k.value->>'status'='accepted'
        THEN to_jsonb(trim_scale((k.value->>'value')::numeric)::text)
        ELSE k.value->'value' END AS value,
    1::integer AS distinct_values,
    COALESCE(k.value->>'status'='accepted' AND CASE k.key
        WHEN 'benchmark_name' THEN k.value->>'unit'='name' AND k.value->>'scope'='fund' AND jsonb_typeof(k.value->'value')='string' AND length(btrim(k.value->>'value')) BETWEEN 1 AND 500
        WHEN 'fund_managers' THEN k.value->>'unit'='names' AND k.value->>'scope'='fund' AND CASE WHEN jsonb_typeof(k.value->'value')='array' THEN jsonb_array_length(k.value->'value') BETWEEN 1 AND 10 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(k.value->'value') x WHERE jsonb_typeof(x)<>'string' OR length(btrim(x#>>'{}')) NOT BETWEEN 1 AND 150) ELSE false END
        WHEN 'base_expense_ratio' THEN k.value->>'unit'='percent' AND k.value->>'scope'='regular_plan' AND CASE WHEN k.value->>'value' ~ '^[0-9]{1,2}(\.[0-9]{1,8})?$' THEN (k.value->>'value')::numeric BETWEEN 0 AND 10 AND (k.value->>'value')::numeric=(k.value->>'value')::numeric(9,4) ELSE false END
        WHEN 'aum' THEN k.value->>'unit'='INR_crore' AND k.value->>'scope'='fund' AND CASE WHEN k.value->>'value' ~ '^[0-9]{1,13}(\.[0-9]{1,8})?$' THEN (k.value->>'value')::numeric BETWEEN 0 AND 1000000000000 AND (k.value->>'value')::numeric=(k.value->>'value')::numeric(20,6) ELSE false END
        ELSE false END,false) AS usable,
    jsonb_build_array(jsonb_build_object('snapshot_id',s.snapshot_id,'source_url',s.document->>'source_url',
        'content_hash',s.content_hash,'source_anchor',k.value->>'source_anchor',
        'date_basis',k.value->>'date_basis','raw_value',k.value->>'raw_value','unit',k.value->>'unit',
        'scope',k.value->>'scope','status',k.value->>'status','reason',k.value->>'reason',
        'field_effective_date',k.value->>'field_effective_date')) AS sources
FROM mf_multi_facts.snapshots s
CROSS JOIN LATERAL jsonb_each(s.document->'fields') k
JOIN mf.funds f ON f.amfi_scheme_code=s.amfi_scheme_code
JOIN mf.amcs a USING(amc_id)
WHERE EXISTS(SELECT 1 FROM mf_multi_facts.runs r WHERE r.status<>'failed' AND r.document->'snapshot_ids' ? s.snapshot_id)
AND s.document->'identity'=jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
    'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
    'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM (SELECT DISTINCT btrim(x) AS i
        FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb));
