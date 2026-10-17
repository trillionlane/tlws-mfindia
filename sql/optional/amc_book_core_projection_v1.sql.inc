-- Execution-only optimization. The dated selected-fields policy is unchanged.
-- Expand each selected field once, rather than reading the whole graph six times.
CREATE OR REPLACE VIEW mf_live_facts.current_values AS
SELECT selected_field_once.code,jsonb_object_agg(alias.key,alias.value) AS values
FROM mf_live_facts.selected_fields selected_field_once
CROSS JOIN LATERAL (
    SELECT 'benchmark_name' AS key,selected_field_once.value
        WHERE selected_field_once.field='benchmark_name'
    UNION ALL SELECT 'benchmark',selected_field_once.value
        WHERE selected_field_once.field='benchmark_name'
    UNION ALL SELECT 'fund_manager_name',to_jsonb((
        SELECT string_agg(x,'; ' ORDER BY ordinal)
        FROM jsonb_array_elements_text(selected_field_once.value)
            WITH ORDINALITY AS names(x,ordinal)))
        WHERE selected_field_once.field='fund_managers'
    UNION ALL SELECT 'base_expense_ratio',selected_field_once.value
        WHERE selected_field_once.field='base_expense_ratio'
    UNION ALL SELECT 'risk_level',selected_field_once.value
        WHERE selected_field_once.field='risk_level'
    UNION ALL SELECT 'aum',selected_field_once.value
        WHERE selected_field_once.field='aum'
) alias GROUP BY selected_field_once.code;

DO $$ DECLARE baseline_projection text; overlay_projection text; BEGIN
    SELECT string_agg(CASE
        WHEN attname='amfi_scheme_code' THEN 'b.amfi_scheme_code'
        WHEN attname IN('benchmark_name','benchmark','fund_manager_name','base_expense_ratio','risk_level','aum')
            THEN format('COALESCE((v.values->>%L)::%s,b.%I) AS %I',attname,format_type(atttypid,atttypmod),attname,attname)
        ELSE format('b.%I',attname) END,',' ORDER BY attnum),
        string_agg(CASE
        WHEN attname='amfi_scheme_code' THEN 'v.code AS amfi_scheme_code'
        WHEN attname IN('benchmark_name','benchmark','fund_manager_name','base_expense_ratio','risk_level','aum')
            THEN format('(v.values->>%L)::%s AS %I',attname,format_type(atttypid,atttypmod),attname)
        ELSE format('NULL::%s AS %I',format_type(atttypid,atttypmod),attname) END,',' ORDER BY attnum)
    INTO baseline_projection,overlay_projection
    FROM pg_attribute WHERE attrelid='mf_live_facts.baseline'::regclass
        AND attnum>0 AND NOT attisdropped;
    -- Equivalent to FULL JOIN on the code, but point predicates can reach both
    -- graph branches. Preserve overlay-only rows and every baseline column.
    EXECUTE 'CREATE OR REPLACE VIEW mf_live_facts.resolved AS SELECT '||baseline_projection||
        ' FROM mf_live_facts.baseline b LEFT JOIN mf_live_facts.current_values v ON v.code=b.amfi_scheme_code'||
        ' UNION ALL SELECT '||overlay_projection||
        ' FROM mf_live_facts.current_values v WHERE NOT EXISTS ('||
        'SELECT 1 FROM mf_live_facts.baseline b WHERE b.amfi_scheme_code=v.code)';
END $$;
