-- =============================================================================
-- MFDataIndia — NAV history, variants, and the 5-year serving view
-- Target: PostgreSQL 16+ (validated against PostgreSQL 18 via PGlite)
-- Depends on: 001_core_schema.sql
-- =============================================================================

BEGIN;
SET search_path TO mf, public;

-- =============================================================================
-- 3. fund_variants — grouping of plan/option variants of one investor scheme
-- =============================================================================
-- AMFI gives one code per plan+option variant. Investors think in terms of the
-- base scheme ("ICICI Pru Multi Asset"), under which Regular-Growth,
-- Regular-IDCW, Direct-Growth, Direct-IDCW etc. all sit.
--
-- group_key is the base scheme name with plan/option tokens stripped and
-- normalised, so variants collapse onto one row. regular_code / direct_code
-- resolve the Regular<->Direct pairing that the "Regular vs Direct conflict"
-- reconciliation check depends on.

CREATE TABLE IF NOT EXISTS mf.fund_variants (
    amfi_scheme_code   integer PRIMARY KEY
                         REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,
    group_key          text    NOT NULL,
    base_scheme_name   text    NOT NULL,
    regular_code       integer REFERENCES mf.funds (amfi_scheme_code),
    direct_code        integer REFERENCES mf.funds (amfi_scheme_code),
    has_direct_sibling boolean NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  mf.fund_variants IS
    'Groups plan/option variants of one investor-facing scheme; resolves Regular<->Direct pairs';
COMMENT ON COLUMN mf.fund_variants.group_key IS
    'Base scheme name with plan/option tokens stripped and normalised';
COMMENT ON COLUMN mf.fund_variants.regular_code IS 'Regular Plan sibling in the same group';
COMMENT ON COLUMN mf.fund_variants.direct_code  IS 'Direct Plan sibling in the same group';

CREATE INDEX IF NOT EXISTS ix_variants_group   ON mf.fund_variants (group_key);
CREATE INDEX IF NOT EXISTS ix_variants_regular ON mf.fund_variants (regular_code)
    WHERE regular_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_variants_direct  ON mf.fund_variants (direct_code)
    WHERE direct_code IS NOT NULL;

-- =============================================================================
-- 4. nav_history — the high-volume table (~5.4M rows for the required 5-year
--    window, ~26M if full history is ingested)
-- =============================================================================
-- Partitioned by RANGE(nav_date), yearly. Benefits:
--   * the 5-year serving view prunes to ~6 partitions instead of scanning all;
--   * an old year can be detached/dropped in O(1) instead of a huge DELETE;
--   * per-year ANALYZE/VACUUM keeps planner stats cheap.
--
-- The primary key must include the partition key (nav_date) — a PostgreSQL
-- requirement for partitioned tables. (amfi_scheme_code, nav_date) is also the
-- natural upsert target for incremental ingestion.
--
-- NAV is NUMERIC(18,4): AMFI publishes exactly 4 decimals and float would
-- introduce representation error into financial data.
--
-- One canonical row per (scheme, date). When both AMFI and api.mfapi.in publish
-- a value they agree exactly (verified); `source` records provenance and
-- `is_cross_verified` records that both were seen and matched. Disagreements are
-- written to mf.reconciliation_findings rather than duplicated here.

CREATE TABLE IF NOT EXISTS mf.nav_history (
    amfi_scheme_code integer       NOT NULL
                       REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,
    nav_date         date          NOT NULL,
    nav              numeric(18,4) NOT NULL CHECK (nav >= 0),
    source           text          NOT NULL DEFAULT 'MFAPI' CHECK (source IN
                       ('MFAPI', 'AMFI', 'CAM', 'MANUAL')),
    is_cross_verified boolean      NOT NULL DEFAULT false,
    ingested_at      timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (amfi_scheme_code, nav_date)
) PARTITION BY RANGE (nav_date);

COMMENT ON TABLE  mf.nav_history IS
    'Canonical daily NAV per scheme. Yearly RANGE partitions. NUMERIC(18,4), never float.';
COMMENT ON COLUMN mf.nav_history.is_cross_verified IS
    'True when both AMFI and api.mfapi.in published this (scheme,date) and the values matched';
COMMENT ON COLUMN mf.nav_history.source IS 'Provenance of the canonical value';

-- Date-slice queries ("all NAVs for 2024-03-01") are common for reconciliation
-- and for building the daily cross-source check. The PK covers per-scheme scans.
CREATE INDEX IF NOT EXISTS ix_nav_date ON mf.nav_history (nav_date);

-- -----------------------------------------------------------------------------
-- Yearly partitions. Indian MF NAV history reaches back into the 1990s, so we
-- pre-create a generous range and keep two future years for clock skew.
-- ensure_nav_partition() is idempotent and callable from the ingestion job so a
-- new year never breaks an ingest run.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mf.ensure_nav_partition(p_year integer)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_name  text := format('nav_history_y%s', p_year);
    v_from  date := make_date(p_year, 1, 1);
    v_to    date := make_date(p_year + 1, 1, 1);
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS mf.%I PARTITION OF mf.nav_history
             FOR VALUES FROM (%L) TO (%L)',
        v_name, v_from, v_to);
    RETURN v_name;
END;
$$;

COMMENT ON FUNCTION mf.ensure_nav_partition(integer) IS
    'Idempotently create the yearly nav_history partition for p_year';

DO $$
DECLARE y integer;
BEGIN
    FOR y IN 1990 .. (extract(year from now())::integer + 2) LOOP
        PERFORM mf.ensure_nav_partition(y);
    END LOOP;
END;
$$;

-- A DEFAULT partition catches anything outside the created ranges so an ingest
-- run can never fail on an unexpected date. It should stay empty; if it does not,
-- that is a data-quality signal and mf.v_partition_health reports it.
CREATE TABLE IF NOT EXISTS mf.nav_history_default
    PARTITION OF mf.nav_history DEFAULT;

-- =============================================================================
-- 5. Serving views
-- =============================================================================

-- -----------------------------------------------------------------------------
-- nav_last_5y — the required delivery window: in-scope Regular Plan schemes,
-- last 5 years of NAV.
--
-- The cutoff is anchored on the dataset's own max(nav_date), NOT CURRENT_DATE.
-- Reason: a snapshot must be reproducible. If the view used CURRENT_DATE its
-- contents would silently shift every day and two exports of the same snapshot
-- would differ. Anchoring on the data means "the 5 years up to the last NAV we
-- actually hold", which is stable for a given snapshot.
--
-- max(nav_date) is an index-only scan on ix_nav_date, so the anchor is cheap.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mf.nav_last_5y AS
SELECT n.amfi_scheme_code,
       n.nav_date,
       n.nav,
       n.source,
       n.is_cross_verified,
       f.scheme_name,
       f.plan_type,
       f.option_type,
       f.scheme_type,
       f.scheme_category,
       f.isin_primary,
       a.amfi_amc_name
FROM mf.nav_history n
JOIN mf.funds f ON f.amfi_scheme_code = n.amfi_scheme_code
JOIN mf.amcs  a ON a.amc_id           = f.amc_id
WHERE f.in_scope
  AND n.nav_date > ((SELECT max(nav_date) FROM mf.nav_history) - interval '5 years')::date;

COMMENT ON VIEW mf.nav_last_5y IS
    'Required window: in-scope Regular Plan NAVs for the 5 years up to the dataset max nav_date';

-- -----------------------------------------------------------------------------
-- nav_window() — explicit control for API consumers and ad-hoc analysis.
-- p_asof defaults to the dataset max date; pass a date to reproduce a historical
-- snapshot exactly.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mf.nav_window(
    p_years integer DEFAULT 5,
    p_asof  date    DEFAULT NULL
)
RETURNS TABLE (
    amfi_scheme_code integer,
    nav_date         date,
    nav              numeric,
    scheme_name      text,
    plan_type        text,
    option_type      text,
    scheme_category  text,
    isin_primary     character(12),
    amfi_amc_name    text
)
LANGUAGE sql
STABLE
AS $$
    WITH anchor AS (
        SELECT coalesce(p_asof, (SELECT max(nav_date) FROM mf.nav_history)) AS asof
    )
    SELECT n.amfi_scheme_code, n.nav_date, n.nav,
           f.scheme_name, f.plan_type, f.option_type, f.scheme_category,
           f.isin_primary, a.amfi_amc_name
    FROM anchor
    CROSS JOIN mf.nav_history n
    JOIN mf.funds f ON f.amfi_scheme_code = n.amfi_scheme_code
    JOIN mf.amcs  a ON a.amc_id           = f.amc_id
    WHERE f.in_scope
      AND n.nav_date >  (anchor.asof - make_interval(years => p_years))
      AND n.nav_date <= anchor.asof
    ORDER BY n.amfi_scheme_code, n.nav_date;
$$;

COMMENT ON FUNCTION mf.nav_window(integer, date) IS
    'In-scope NAVs for the p_years up to p_asof (default: dataset max nav_date)';

COMMIT;
