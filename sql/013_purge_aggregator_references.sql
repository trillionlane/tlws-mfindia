-- 013_purge_aggregator_references.sql
-- Compliance: no aggregator (Groww/Scripbox) provenance, identity, or payload
-- may remain in the database. Fund DATA stays; only the aggregator references
-- go.
--
-- NOTE: "Groww" is also a real AMC (Groww Mutual Fund). Its funds' official
-- names/ISINs/NAVs live in mf.funds / mf.nav_history and are legitimate data —
-- untouched by this migration.
--
-- Also introduces OUR OWN fund identity (mf.fund_family: tlws_mf_id, slug,
-- tags) to replace the dropped aggregator fund_slug / scripbox_fund_id, at
-- base-scheme (fund family) granularity — the entity for news + URLs.
--
-- Idempotent: DROP ... IF EXISTS / guarded UPDATE / guarded DELETE.

-- 1. Drop the reporting view first: it references groww_fetched_at and
--    ff.source, both of which change below. Recreated at the end.
DROP VIEW IF EXISTS mf.v_fund_data_status;

-- 2. Aggregator-named fund_facts columns (+ their two CHECK constraints).
ALTER TABLE mf.fund_facts DROP CONSTRAINT IF EXISTS fund_facts_groww_source_mode_ck;
ALTER TABLE mf.fund_facts DROP CONSTRAINT IF EXISTS fund_facts_groww_inherit_ck;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS groww_return_stats;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS groww_fetched_at;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS groww_source_mode;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS groww_inherited_from;

-- 3. Aggregator payload + slug columns (no API/UI consumer after the
--    matching queries.py change; fund_variant is superseded by mf.fund_variants,
--    which is derived from AMFI).
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS raw_payload;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS fund_slug;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS fund_variant;

-- 4. Neutralise provenance values: the aggregator names must not appear in
--    data. 'LEGACY' = historical fund facts of undisclosed provenance.
--    Sequence is fixed: DROP (old CHECK blocks 'LEGACY'), UPDATE (new CHECK
--    blocks the old values), then ADD.
ALTER TABLE mf.fund_facts DROP CONSTRAINT IF EXISTS fund_facts_source_check;
UPDATE mf.fund_facts SET source = 'LEGACY' WHERE source IN ('SCRIPBOX', 'GROWW');
ALTER TABLE mf.fund_facts ADD CONSTRAINT fund_facts_source_check
    CHECK (source = ANY (ARRAY['AMFI', 'MFAPI', 'CAM', 'WAYBACK', 'MANUAL',
                               'COMPUTED', 'AMC', 'LEGACY']));

-- 5. Drop the opinions table: a pure aggregator editorial artifact, not
--    exposed in the API/UI, and the licensing-sensitive table the design
--    explicitly keeps separable for redistribution. The coverage view still
--    counts its rows (with_opinions), so recreate that view first without the
--    join — its consumers (API /stats, scripts/status.py) read facts_pct only.
DROP VIEW IF EXISTS mf.v_enrichment_coverage;
CREATE VIEW mf.v_enrichment_coverage AS
    SELECT count(*)                AS in_scope_live,
           count(ff.amfi_scheme_code) AS with_facts,
           round(((100.0 * (count(ff.amfi_scheme_code))::numeric)
                  / (NULLIF(count(*), 0))::numeric), 2) AS facts_pct,
           count(*) FILTER (WHERE ff.aum IS NOT NULL)            AS with_aum,
           count(*) FILTER (WHERE ff.expense_ratio IS NOT NULL)  AS with_expense_ratio,
           count(*) FILTER (WHERE ff.inception_date IS NOT NULL) AS with_inception
      FROM mf.funds f
      LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
     WHERE f.in_scope AND NOT f.is_defunct;
DROP TABLE IF EXISTS mf.fund_opinions;

-- 6. Purge the dead aggregator work-queue rows; keep the live AMFI/AMC queues.
DELETE FROM mf.ingest_checkpoints WHERE source IN ('GROWW', 'SCRIPBOX');

-- 7. Clean the source_metadata constraint (drop the aggregator name).
ALTER TABLE mf.source_metadata DROP CONSTRAINT IF EXISTS source_metadata_source_check;
ALTER TABLE mf.source_metadata ADD CONSTRAINT source_metadata_source_check
    CHECK (source = ANY (ARRAY['MFAPI', 'AMFI', 'AMFI_HISTORY', 'CAM', 'WAYBACK',
                               'MANUAL', 'AMC']));

-- 8. Our own fund identity (replaces the dropped aggregator fund_slug /
--    scripbox_fund_id). One row per base scheme (fund family): the
--    granularity for related-news retrieval and stable URLs. Populated by
--    scripts/build_fund_family.py (idempotent upsert on group_key).
CREATE TABLE IF NOT EXISTS mf.fund_family (
    tlws_mf_id       text PRIMARY KEY,          -- our own stable ID (UUIDv5 of group_key)
    group_key        text NOT NULL UNIQUE,
    base_scheme_name text NOT NULL,
    slug             text NOT NULL UNIQUE,      -- for related-news retrieval + URLs
    tags             text[] NOT NULL DEFAULT '{}',
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_family_tags ON mf.fund_family USING gin (tags);

COMMENT ON TABLE mf.fund_family IS
    'Own fund identity per base scheme (fund family): tlws_mf_id (deterministic '
    'UUIDv5), search/URL slug and news tags. Replaces the aggregator slugs that '
    'were dropped in 012/013; populated by scripts/build_fund_family.py.';

-- 9. Recreate the reporting view (no groww_fetched_at; no aggregator language).
CREATE VIEW mf.v_fund_data_status AS
WITH pending_set(item) AS (
    VALUES ('registrar_agent'),
           ('base_expense_ratio'),
           ('expense_ratio_hist')
),
nav AS (
    SELECT n.amfi_scheme_code,
           max(n.nav_date) AS last_nav_date,
           count(*)        AS nav_points
      FROM mf.nav_history n
     GROUP BY n.amfi_scheme_code
),
hold AS (
    SELECT h.amfi_scheme_code,
           count(*)                                          AS holding_rows,
           count(*) FILTER (WHERE h.sector_name IS NOT NULL) AS sector_rows
      FROM mf.fund_holdings h
     GROUP BY h.amfi_scheme_code
),
base AS (
    SELECT f.amfi_scheme_code,
           f.scheme_name, f.plan_type, f.option_type, f.scheme_category,
           f.amc_id, f.is_defunct, f.nav_not_published, f.in_scope, f.is_etf,
           f.isin_primary,
           ff.aum, ff.expense_ratio, ff.inception_date, ff.benchmark,
           ff.fund_manager_name, ff.risk_level, ff.min_initial_investment_amount,
           ff.exit_load_value, ff.lock_in_period,
           ff.return_1year, ff.return_3year, ff.return_5year, ff.return_since_launch,
           ff.holdings_analysis, ff.portfolio_turnover,
           ff.registrar_agent, ff.base_expense_ratio, ff.expense_ratio_history,
           ff.source AS facts_source,
           a.amc_aum,
           nav.last_nav_date, nav.nav_points,
           hold.holding_rows, hold.sector_rows
      FROM mf.funds f
      LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
      LEFT JOIN mf.amcs       a  ON a.amc_id            = f.amc_id
      LEFT JOIN nav  ON nav.amfi_scheme_code  = f.amfi_scheme_code
      LEFT JOIN hold ON hold.amfi_scheme_code = f.amfi_scheme_code
),
scored AS (
    SELECT b.*,
           (NOT coalesce(b.is_defunct, false)
             AND NOT coalesce(b.nav_not_published, false)
             AND (b.last_nav_date IS NULL
                  OR b.last_nav_date >= current_date - interval '90 days')) AS is_live,
           (b.inception_date IS NOT NULL
             AND b.inception_date <= current_date - interval '1 year')  AS old_1y,
           (b.inception_date IS NOT NULL
             AND b.inception_date <= current_date - interval '3 years') AS old_3y,
           (b.inception_date IS NOT NULL
             AND b.inception_date <= current_date - interval '5 years') AS old_5y
      FROM base b
)
SELECT s.amfi_scheme_code,
       s.scheme_name, s.plan_type, s.option_type, s.scheme_category,
       s.amc_id, s.is_defunct, s.in_scope,
       s.facts_source,
       s.last_nav_date,
       (current_date - s.last_nav_date)                      AS nav_age_days,
       (s.last_nav_date >= current_date - interval '7 days') AS nav_is_fresh,
       s.nav_points,
       count(*) FILTER (WHERE chk.applicable
                         AND chk.item NOT IN (SELECT item FROM pending_set)) AS items_expected,
       count(*) FILTER (WHERE chk.applicable AND chk.present
                         AND chk.item NOT IN (SELECT item FROM pending_set)) AS items_present,
       round(100.0
             * count(*) FILTER (WHERE chk.applicable AND chk.present
                                 AND chk.item NOT IN (SELECT item FROM pending_set))::numeric
             / NULLIF(count(*) FILTER (WHERE chk.applicable
                                        AND chk.item NOT IN (SELECT item FROM pending_set)),
                      0)::numeric, 1)
             AS data_completeness_pct,
       coalesce(array_agg(chk.item ORDER BY chk.item)
                FILTER (WHERE chk.applicable AND NOT chk.present
                         AND chk.item NOT IN (SELECT item FROM pending_set)), '{}'::text[])
             AS missing_items,
       count(*) FILTER (WHERE chk.applicable
                         AND chk.item IN (SELECT item FROM pending_set))     AS pending_expected,
       count(*) FILTER (WHERE chk.applicable AND chk.present
                         AND chk.item IN (SELECT item FROM pending_set))     AS pending_present,
       coalesce(array_agg(chk.item ORDER BY chk.item)
                FILTER (WHERE chk.applicable AND NOT chk.present
                         AND chk.item IN (SELECT item FROM pending_set)), '{}'::text[])
             AS pending_items,
       NOT s.is_live AS is_closed,
       CASE WHEN coalesce(s.is_defunct, false)        THEN 'defunct'
            WHEN coalesce(s.nav_not_published, false) THEN 'nav_not_published'
            WHEN s.last_nav_date < current_date - interval '90 days'
                 THEN 'nav_stale_' || (current_date - s.last_nav_date) || 'd'
            ELSE NULL
       END AS closed_reason
  FROM scored s
  CROSS JOIN LATERAL (VALUES
     ('scheme_name',        true, s.scheme_name IS NOT NULL AND s.scheme_name <> ''),
     ('plan_type',          true, s.plan_type IS NOT NULL),
     ('option_type',        true, s.option_type IS NOT NULL AND s.option_type <> 'UNKNOWN'),
     ('scheme_category',    true, s.scheme_category IS NOT NULL),
     ('isin_primary',       true, s.isin_primary IS NOT NULL),
     ('amc_link',           true, s.amc_id IS NOT NULL),
     ('aum',                true, s.aum IS NOT NULL),
     ('expense_ratio',      true, s.expense_ratio IS NOT NULL),
     ('inception_date',     true, s.inception_date IS NOT NULL),
     ('benchmark',          true, s.benchmark IS NOT NULL),
     ('fund_manager_name',  true, s.fund_manager_name IS NOT NULL),
     ('risk_level',         true, s.risk_level IS NOT NULL),
     ('min_initial_invest', true, s.min_initial_investment_amount IS NOT NULL),
     ('exit_load_value',    true, s.exit_load_value IS NOT NULL),
     ('lock_in_period',     true, s.lock_in_period IS NOT NULL),
     ('return_1year',        s.is_live AND s.old_1y, s.return_1year IS NOT NULL),
     ('return_3year',        s.is_live AND s.old_3y, s.return_3year IS NOT NULL),
     ('return_5year',        s.is_live AND s.old_5y, s.return_5year IS NOT NULL),
     ('return_since_launch', true, s.return_since_launch IS NOT NULL),
     ('holdings_analysis',  true, s.holdings_analysis IS NOT NULL),
     ('holdings_sector',    true, coalesce(s.sector_rows, 0) > 0),
     ('portfolio_turnover', NOT coalesce(s.is_etf, false), s.portfolio_turnover IS NOT NULL),
     ('nav_history',        true, coalesce(s.nav_points, 0) > 0),
     ('nav_fresh',          s.is_live, s.last_nav_date >= current_date - interval '7 days'),
     ('amc_aum',            true, s.amc_aum IS NOT NULL),
     ('registrar_agent',    s.is_live,
         s.registrar_agent IS NOT NULL AND s.registrar_agent <> ''),
     ('base_expense_ratio', s.is_live, s.base_expense_ratio IS NOT NULL),
     ('expense_ratio_hist', s.is_live,
         s.expense_ratio_history IS NOT NULL
         AND s.expense_ratio_history <> '{}'::jsonb)
  ) AS chk(item, applicable, present)
 GROUP BY s.amfi_scheme_code, s.scheme_name, s.plan_type, s.option_type,
          s.scheme_category, s.amc_id, s.is_defunct, s.nav_not_published,
          s.in_scope, s.facts_source, s.last_nav_date,
          s.nav_points, s.is_live;

-- 10. Strip aggregator provenance left inside JSONB values, and clean the
--     funds.metadata_authority CHECK (all 14,368 rows are already 'AMFI').
--     NOTE: references to Groww *as a fund house* are legitimate data and are
--     deliberately NOT touched — e.g. fund names ("Groww Nifty …"), holdings
--     of Groww funds (FoFs holding "Groww Liquid Fund"), manager bios.
UPDATE mf.fund_facts
   SET holdings_analysis = holdings_analysis - 'source'
 WHERE holdings_analysis ? 'source';
ALTER TABLE mf.funds DROP CONSTRAINT IF EXISTS funds_metadata_authority_check;
ALTER TABLE mf.funds ADD CONSTRAINT funds_metadata_authority_check
    CHECK (metadata_authority = ANY (ARRAY['AMFI', 'MFAPI', 'MANUAL']));