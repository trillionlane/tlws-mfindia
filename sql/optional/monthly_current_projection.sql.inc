-- Pinned owner-only view replacement fragment; never a default migration.
CREATE OR REPLACE VIEW mf_live_facts.field_versions AS
WITH assertions AS (
    SELECT s.amfi_scheme_code AS code,k.key AS field,s.disclosure_as_of AS as_of,
        k.value->'value' AS value,k.value->>'unit' AS unit,k.value->>'scope' AS scope,
        k.value->>'status' AS status,
        s.document->'identity' AS identity,
        jsonb_build_object('snapshot_id',s.snapshot_id,'source_url',s.document->>'source_url',
            'content_hash',s.document->>'content_hash','source_anchor',k.value->>'source_anchor',
            'date_basis',k.value->>'date_basis','raw_value',k.value->>'raw_value',
            'unit',k.value->>'unit','scope',k.value->>'scope','status',k.value->>'status') AS evidence
    FROM mf_official_facts.snapshots s CROSS JOIN LATERAL jsonb_each(s.document->'fields') k
    WHERE k.key IN('benchmark_name','fund_managers','base_expense_ratio') AND k.value->>'status'='accepted'
      AND EXISTS(SELECT 1 FROM mf_fact_refresh.transitions t WHERE t.document->>'mode'='activate_monthly_current' AND t.document->'legacy_snapshot_ids' ? s.snapshot_id)
    UNION ALL
    SELECT s.amfi_scheme_code,field,s.disclosure_as_of,
        COALESCE((s.document->'fields'->field)->'value','null'::jsonb),
        (s.document->'fields'->field)->>'unit',(s.document->'fields'->field)->>'scope',
        COALESCE((s.document->'fields'->field)->>'status','missing'),s.document->'identity',
        jsonb_build_object('snapshot_id',s.snapshot_id,'source_url',s.document->>'source_url',
            'content_hash',s.document->>'content_hash','source_anchor',(s.document->'fields'->field)->>'source_anchor',
            'date_basis',(s.document->'fields'->field)->>'date_basis','raw_value',(s.document->'fields'->field)->>'raw_value',
            'unit',(s.document->'fields'->field)->>'unit','scope',(s.document->'fields'->field)->>'scope',
            'status',COALESCE((s.document->'fields'->field)->>'status','missing'),
            'reason',(s.document->'fields'->field)->>'reason',
            'field_effective_date',(s.document->'fields'->field)->>'field_effective_date')
    FROM mf_monthly_facts.snapshots s CROSS JOIN unnest(ARRAY['benchmark_name','fund_managers','base_expense_ratio','risk_level','aum']) field
    WHERE s.policy_hash='813c1649be01699cb1d4d205bb5d0b844faa72a08c487be333e0793212cd0f0b'
      AND EXISTS(SELECT 1 FROM mf_fact_refresh.publications p WHERE p.content_hash=s.content_hash AND p.disclosure_as_of=s.disclosure_as_of AND p.snapshot_ids ? s.snapshot_id)
), matched AS (
    SELECT a.*,CASE WHEN jsonb_typeof(a.value)='string' AND a.value#>>'{}' ~ '^[0-9]{1,13}(\.[0-9]{1,8})?$'
        THEN (a.value#>>'{}')::numeric END AS amount
    FROM assertions a JOIN mf.funds f ON f.amfi_scheme_code=a.code JOIN mf.amcs c USING(amc_id)
    WHERE a.identity=jsonb_build_object('code',f.amfi_scheme_code,'amc',c.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
), checked AS (
    SELECT m.*,COALESCE(status='accepted' AND CASE field
        WHEN 'benchmark_name' THEN unit='name' AND scope='fund' AND jsonb_typeof(value)='string' AND length(btrim(value#>>'{}')) BETWEEN 1 AND 500
        WHEN 'fund_managers' THEN unit='names' AND scope='fund' AND CASE WHEN jsonb_typeof(value)='array'
            THEN jsonb_array_length(value) BETWEEN 1 AND 10 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(value) x WHERE jsonb_typeof(x)<>'string' OR length(btrim(x#>>'{}')) NOT BETWEEN 1 AND 150)
            ELSE false END
        WHEN 'base_expense_ratio' THEN unit='percent' AND scope='regular_plan' AND CASE WHEN amount BETWEEN 0 AND 10 THEN amount=amount::numeric(9,4) ELSE false END
        WHEN 'aum' THEN unit='INR_crore' AND scope='fund' AND CASE WHEN amount BETWEEN 0 AND 1000000000000 THEN amount=amount::numeric(20,6) ELSE false END
        WHEN 'risk_level' THEN unit='risk_category' AND scope='fund' AND value#>>'{}' IN('Low','Low to Moderate','Moderate','Moderately High','High','Very High')
        ELSE false END,false) AS usable
    FROM matched m
)
SELECT code,field,as_of,
    CASE WHEN field IN('base_expense_ratio','aum') AND bool_and(usable)
        THEN to_jsonb(trim_scale(((jsonb_agg(value ORDER BY evidence->>'snapshot_id'))->>0)::numeric)::text)
        ELSE (jsonb_agg(value ORDER BY evidence->>'snapshot_id'))->0 END AS value,
    count(DISTINCT CASE WHEN field IN('base_expense_ratio','aum') AND usable THEN to_jsonb(amount) ELSE value END)::integer AS distinct_values,
    bool_and(usable) AS usable,jsonb_agg(evidence ORDER BY evidence->>'snapshot_id') AS sources
FROM checked GROUP BY code,field,as_of;

CREATE OR REPLACE VIEW mf_live_facts.current_values AS
SELECT code,jsonb_object_agg(key,value) AS values FROM (
    SELECT code,'benchmark_name' AS key,value FROM mf_live_facts.selected_fields WHERE field='benchmark_name'
    UNION ALL SELECT code,'benchmark',value FROM mf_live_facts.selected_fields WHERE field='benchmark_name'
    UNION ALL SELECT code,'fund_manager_name',to_jsonb((SELECT string_agg(x,'; ' ORDER BY ordinal) FROM jsonb_array_elements_text(value) WITH ORDINALITY AS names(x,ordinal))) FROM mf_live_facts.selected_fields WHERE field='fund_managers'
    UNION ALL SELECT code,'base_expense_ratio',value FROM mf_live_facts.selected_fields WHERE field='base_expense_ratio'
    UNION ALL SELECT code,'risk_level',value FROM mf_live_facts.selected_fields WHERE field='risk_level'
    UNION ALL SELECT code,'aum',value FROM mf_live_facts.selected_fields WHERE field='aum'
) fields GROUP BY code;

DO $$ DECLARE projection text; BEGIN
    SELECT string_agg(CASE
        WHEN attname='amfi_scheme_code' THEN 'COALESCE(b.amfi_scheme_code,v.code) AS amfi_scheme_code'
        WHEN attname IN('benchmark_name','benchmark','fund_manager_name','base_expense_ratio','risk_level','aum')
            THEN format('COALESCE((v.values->>%L)::%s,b.%I) AS %I',attname,format_type(atttypid,atttypmod),attname,attname)
        ELSE format('b.%I',attname) END,',' ORDER BY attnum)
    INTO projection FROM pg_attribute WHERE attrelid='mf.current_fund_facts'::regclass AND attnum>0 AND NOT attisdropped;
    EXECUTE 'CREATE OR REPLACE VIEW mf_live_facts.resolved AS SELECT '||projection||
        ' FROM mf_live_facts.baseline b FULL JOIN mf_live_facts.current_values v ON v.code=b.amfi_scheme_code';
END $$;
