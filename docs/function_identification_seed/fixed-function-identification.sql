WITH reader AS (
    SELECT r.oid, r.rolname, r.rolsuper, r.rolinherit
    FROM pg_catalog.pg_roles AS r
    WHERE r.rolname = 'mfdata_verified_reader'
), domain_schemas AS (
    SELECT n.oid, n.nspname
    FROM pg_catalog.pg_namespace AS n
    WHERE n.nspname IN (
        'mf','mf_analytics','mf_book_facts','mf_distributions','mf_enrichment',
        'mf_fact_refresh','mf_fact_review','mf_idcw','mf_live_facts',
        'mf_monthly_facts','mf_multi_facts','mf_official_facts','mf_overlap',
        'mf_portfolios','mf_review','mf_rolling'
    )
), matching AS (
    SELECT p.oid, p.pronamespace, n.nspname, p.proname, p.prokind,
           p.proowner, o.rolname AS owner_name, p.proargtypes, p.prorettype,
           p.prosecdef, p.proacl,
           pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE') AS schema_usage
    FROM pg_catalog.pg_proc AS p
    JOIN domain_schemas AS n ON n.oid = p.pronamespace
    CROSS JOIN reader AS r
    LEFT JOIN pg_catalog.pg_roles AS o ON o.oid = p.proowner
    WHERE p.prosecdef
      AND pg_catalog.has_function_privilege(r.oid, p.oid, 'EXECUTE')
), function_rows AS (
    SELECT f.oid, pg_catalog.jsonb_build_object(
        'oid', f.oid::bigint,
        'namespace_oid', f.pronamespace::bigint,
        'schema', f.nspname,
        'name', f.proname,
        'kind', f.prokind::text,
        'owner_oid', f.proowner::bigint,
        'owner_name', f.owner_name,
        'argument_type_oids', COALESCE((
            SELECT pg_catalog.jsonb_agg(t.type_oid::bigint ORDER BY t.ordinality)
            FROM pg_catalog.unnest(f.proargtypes::oid[])
                WITH ORDINALITY AS t(type_oid, ordinality)
        ), '[]'::jsonb),
        'return_type_oid', f.prorettype::bigint,
        'security_definer', true,
        'effective_execute', true,
        'schema_usage', f.schema_usage,
        'acl_is_null', f.proacl IS NULL,
        'execute_acl_atoms', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'grantor_oid', a.grantor::bigint,
                    'grantee_oid', a.grantee::bigint,
                    'grantee_name', CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                                         ELSE g.rolname END,
                    'is_grantable', a.is_grantable,
                    'reader_is_grantee', a.grantee = r.oid,
                    'role_privileges_immediately_available',
                        CASE WHEN a.grantee = 0 THEN true
                             ELSE pg_catalog.pg_has_role(r.oid, a.grantee, 'USAGE') END,
                    'reader_is_member',
                        CASE WHEN a.grantee = 0 THEN false
                             ELSE pg_catalog.pg_has_role(r.oid, a.grantee, 'MEMBER') END,
                    'reader_can_set_role',
                        CASE WHEN a.grantee = 0 THEN false
                             ELSE pg_catalog.pg_has_role(r.oid, a.grantee, 'SET') END
                ) ORDER BY a.grantor, a.grantee, a.is_grantable
            )
            FROM pg_catalog.aclexplode(
                COALESCE(f.proacl, pg_catalog.acldefault('f', f.proowner))
            ) AS a
            LEFT JOIN pg_catalog.pg_roles AS g ON g.oid = a.grantee
            WHERE a.privilege_type = 'EXECUTE'
        ), '[]'::jsonb)
    ) AS value
    FROM matching AS f
    CROSS JOIN reader AS r
)
SELECT pg_catalog.jsonb_build_object(
    'format', 'mf-reader-privileged-function-identification-v1',
    'reader', COALESCE((
        SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'oid', r.oid::bigint, 'rolname', r.rolname,
            'rolsuper', r.rolsuper, 'rolinherit', r.rolinherit
        ) ORDER BY r.oid) FROM reader AS r
    ), '[]'::jsonb),
    'schemas', COALESCE((
        SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'oid', n.oid::bigint, 'nspname', n.nspname
        ) ORDER BY n.oid) FROM domain_schemas AS n
    ), '[]'::jsonb),
    'functions', COALESCE((
        SELECT pg_catalog.jsonb_agg(f.value ORDER BY f.oid) FROM function_rows AS f
    ), '[]'::jsonb)
) AS value
