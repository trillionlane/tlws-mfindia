-- Persist the exact aggregates served by /api/stats. The API must never scan
-- the multi-million-row NAV table on a request path.

BEGIN;

CREATE TABLE mf.dataset_summary (
    singleton            boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    schemes_total        bigint NOT NULL CHECK (schemes_total >= 0),
    in_scope_total       bigint NOT NULL CHECK (in_scope_total >= 0),
    in_scope_live        bigint NOT NULL CHECK (in_scope_live >= 0),
    amcs                 bigint NOT NULL CHECK (amcs >= 0),
    categories           bigint NOT NULL CHECK (categories >= 0),
    nav_rows             bigint NOT NULL CHECK (nav_rows >= 0),
    nav_first            date,
    nav_last             date,
    enrichment_pct       numeric(5,2) CHECK (
                             enrichment_pct IS NULL
                             OR enrichment_pct BETWEEN 0 AND 100
                         ),
    dataset_version      bigint NOT NULL CHECK (dataset_version > 0),
    source_content_hash  text CHECK (
                             source_content_hash IS NULL
                             OR source_content_hash ~ '^[0-9a-f]{64}$'
                         ),
    refresh_reason       text NOT NULL CHECK (
                             length(btrim(refresh_reason)) BETWEEN 1 AND 100
                         ),
    refreshed_at         timestamptz NOT NULL,
    CHECK (
        (nav_rows = 0 AND nav_first IS NULL AND nav_last IS NULL)
        OR (nav_rows > 0 AND nav_first IS NOT NULL AND nav_last IS NOT NULL
            AND nav_first <= nav_last)
    )
);

COMMENT ON TABLE mf.dataset_summary IS
    'Singleton exact API aggregates, refreshed only after governed dataset writes';
COMMENT ON COLUMN mf.dataset_summary.dataset_version IS
    'Monotonic cache-invalidation version incremented on every successful refresh';

CREATE OR REPLACE FUNCTION mf.refresh_dataset_summary(
    p_reason text,
    p_source_content_hash text DEFAULT NULL
)
RETURNS mf.dataset_summary
LANGUAGE plpgsql
SET search_path = mf, pg_temp
AS $$
DECLARE
    result mf.dataset_summary;
BEGIN
    IF p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'dataset summary refresh reason must contain 1..100 characters';
    END IF;

    INSERT INTO mf.dataset_summary (
        singleton, schemes_total, in_scope_total, in_scope_live, amcs,
        categories, nav_rows, nav_first, nav_last, enrichment_pct,
        dataset_version, source_content_hash, refresh_reason, refreshed_at
    )
    SELECT
        true,
        coverage.schemes_total,
        coverage.in_scope_total,
        coverage.in_scope_live,
        coverage.amcs,
        coverage.categories,
        nav.rows,
        nav.first_date,
        nav.last_date,
        enrichment.facts_pct,
        1,
        lower(COALESCE(p_source_content_hash, latest_source.content_hash)),
        btrim(p_reason),
        clock_timestamp()
    FROM mf.v_coverage AS coverage
    CROSS JOIN (
        SELECT count(*) AS rows,
               min(nav_date) AS first_date,
               max(nav_date) AS last_date
        FROM mf.nav_history
    ) AS nav
    CROSS JOIN mf.v_enrichment_coverage AS enrichment
    LEFT JOIN LATERAL (
        SELECT content_hash
        FROM mf.source_metadata
        WHERE source = 'AMFI'
          AND entity_kind = 'LATEST_NAV'
          AND error IS NULL
          AND content_hash IS NOT NULL
        ORDER BY fetched_at DESC, fetch_id DESC
        LIMIT 1
    ) AS latest_source ON true
    ON CONFLICT (singleton) DO UPDATE SET
        schemes_total = EXCLUDED.schemes_total,
        in_scope_total = EXCLUDED.in_scope_total,
        in_scope_live = EXCLUDED.in_scope_live,
        amcs = EXCLUDED.amcs,
        categories = EXCLUDED.categories,
        nav_rows = EXCLUDED.nav_rows,
        nav_first = EXCLUDED.nav_first,
        nav_last = EXCLUDED.nav_last,
        enrichment_pct = EXCLUDED.enrichment_pct,
        dataset_version = mf.dataset_summary.dataset_version + 1,
        source_content_hash = COALESCE(
            EXCLUDED.source_content_hash,
            mf.dataset_summary.source_content_hash
        ),
        refresh_reason = EXCLUDED.refresh_reason,
        refreshed_at = EXCLUDED.refreshed_at
    RETURNING * INTO result;

    RETURN result;
END;
$$;

REVOKE ALL ON FUNCTION mf.refresh_dataset_summary(text, text) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_app') THEN
        EXECUTE 'GRANT SELECT ON mf.dataset_summary TO mfdata_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_ingest') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON mf.dataset_summary TO mfdata_ingest';
        EXECUTE 'GRANT EXECUTE ON FUNCTION mf.refresh_dataset_summary(text, text) '
                'TO mfdata_ingest';
    END IF;
END
$$;

SELECT mf.refresh_dataset_summary('migration_015');

COMMIT;
