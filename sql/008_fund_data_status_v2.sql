-- 008_fund_data_status_v2.sql
-- Revises mf.v_fund_data_status against what the pipeline can actually obtain.
--
-- WHY THIS REVISION EXISTS
-- The v1 checklist scored five items that no configured source can deliver, so
-- they showed up as "missing" on effectively every fund and drowned the real
-- gaps. Separately, the 2026-10 recovery added five columns to groww_to_store.py
-- that had been mapped-then-dropped, and nothing in the inventory noticed.
--
-- REMOVED FROM SCORING (unobtainable, not merely absent):
--   groww_rating     -- Groww publishes no rating for these funds
--   sharpe_ratio     -- risk metrics dropped from the product surface
--   beta             -- ditto
--   std_deviation    -- ditto
--   amc_description  -- populated on 43 of 3,943 funds; no source returns it
-- These columns still exist on mf.fund_facts; they are simply no longer counted
-- against a fund. Do not resurrect them in the checklist.
--
-- TRACKED SEPARATELY, *NOT* SCORED (obtainable, but nothing has fetched them):
--   registrar_agent, base_expense_ratio, expense_ratio_history
-- Groww returns all three, so they are obtainable -- but they are 100% NULL today
-- because no enrichment run has happened since the loader began persisting them.
-- Scoring them would subtract a flat ~6 points from every fund and make 100%
-- unreachable for all live funds while adding ZERO discriminating signal: every
-- fund loses the same three. They are therefore reported in their own pending_*
-- columns instead, so the two figures mean different things:
--   data_completeness_pct = complete on what the pipeline delivers TODAY
--   pending_items         = what the next Groww enrichment run will close
--
-- DROPPED ENTIRELY (mislabelled at the source, or redundant):
--   super_category, sub_category
-- A live probe of Groww's mfServerSideData showed that `super_category` carries
-- the FUND NAME, not a category: `super_category == fund_name` on every fund
-- sampled. Persisting it would fill a category column with scheme names. The
-- hierarchy Groww actually serves is `category` -> `sub_category`, and that is
-- ALREADY stored as asset_class / sub_asset_class -- 99.9% populated on live
-- in-scope funds (3,853 of 3,856). Both columns are therefore left NULL
-- permanently and are not checked at all. Do not resurrect them; read
-- asset_class / sub_asset_class instead.
--
-- CLOSED FUNDS
-- A fund that is defunct, does not publish NAV, or whose last NAV is older than
-- 90 days is effectively closed. It will never have fresh NAV or current
-- trailing returns, so those items are marked NOT APPLICABLE rather than
-- missing -- otherwise a closed fund can never reach 100% and pollutes every
-- aggregate. is_closed / closed_reason are exposed so consumers and recovery
-- target lists can filter with `WHERE NOT is_closed`.
--
-- Scored checklist: 25 items (v1's 30 minus the 5 unobtainable ones). Three more
-- are held back by the pending_set CTE, so 28 fields are reported in total.
--
-- Idempotent and order-independent: DROP + CREATE, not CREATE OR REPLACE.
-- CREATE OR REPLACE can only append columns, so re-running the superseded 007
-- against a database already at 008 fails with "cannot drop columns from view".
-- Dropping first makes this file safe to re-apply from any starting state.
-- mf.v_fund_data_status has no dependent objects (verified via pg_depend), so
-- no CASCADE is needed and nothing downstream breaks.
--
-- NOTE: 007 is superseded by this file. Do not re-run it afterwards.

DROP VIEW IF EXISTS mf.v_fund_data_status;

CREATE VIEW mf.v_fund_data_status AS
-- SINGLE SOURCE OF TRUTH for which items are held back from the headline metric.
-- Both sides of every aggregate below key off this one list, so promoting an
-- item into data_completeness_pct after a successful Groww re-enrichment run is
-- a ONE-LINE EDIT: delete its row here. Nothing else in this file needs to
-- change -- not the checklist, not the SELECT list. An empty list means every
-- checklist item is scored.
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
           ff.groww_fetched_at, ff.source AS facts_source,
           -- added by 008: Groww columns that were mapped-but-unwritten AND carry
           -- genuinely new information. super_category / sub_category are absent
           -- on purpose -- see the DROPPED ENTIRELY note in the header.
           ff.registrar_agent, ff.base_expense_ratio, ff.expense_ratio_history,
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
           -- "live" = still expected to publish NAV and report current returns.
           -- A fund with no NAV history at all counts as live-but-suspect so it
           -- stays in scope and surfaces as a gap instead of being hidden.
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
       s.facts_source, s.groww_fetched_at,
       s.last_nav_date,
       (current_date - s.last_nav_date)                      AS nav_age_days,
       (s.last_nav_date >= current_date - interval '7 days') AS nav_is_fresh,
       s.nav_points,
       -- HEADLINE METRIC: every applicable item not held back by pending_set,
       -- i.e. what the pipeline can actually deliver today.
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
       -- TRACKED SEPARATELY: what the next Groww enrichment run will close.
       count(*) FILTER (WHERE chk.applicable
                         AND chk.item IN (SELECT item FROM pending_set))     AS pending_expected,
       count(*) FILTER (WHERE chk.applicable AND chk.present
                         AND chk.item IN (SELECT item FROM pending_set))     AS pending_present,
       coalesce(array_agg(chk.item ORDER BY chk.item)
                FILTER (WHERE chk.applicable AND NOT chk.present
                         AND chk.item IN (SELECT item FROM pending_set)), '{}'::text[])
             AS pending_items,
       -- added by 008: closed-fund awareness
       NOT s.is_live AS is_closed,
       CASE WHEN coalesce(s.is_defunct, false)        THEN 'defunct'
            WHEN coalesce(s.nav_not_published, false) THEN 'nav_not_published'
            WHEN s.last_nav_date < current_date - interval '90 days'
                 THEN 'nav_stale_' || (current_date - s.last_nav_date) || 'd'
            ELSE NULL
       END AS closed_reason
  FROM scored s
  CROSS JOIN LATERAL (VALUES
    -- An item named in pending_set (top of file) is reported via pending_* and
    -- excluded from data_completeness_pct; every other item is scored.
    -- identity / classification
    ('scheme_name',        true, s.scheme_name IS NOT NULL AND s.scheme_name <> ''),
    ('plan_type',          true, s.plan_type IS NOT NULL),
    ('option_type',        true, s.option_type IS NOT NULL AND s.option_type <> 'UNKNOWN'),
    ('scheme_category',    true, s.scheme_category IS NOT NULL),
    ('isin_primary',       true, s.isin_primary IS NOT NULL),
    ('amc_link',           true, s.amc_id IS NOT NULL),
    -- core fund facts
    ('aum',                true, s.aum IS NOT NULL),
    ('expense_ratio',      true, s.expense_ratio IS NOT NULL),
    ('inception_date',     true, s.inception_date IS NOT NULL),
    ('benchmark',          true, s.benchmark IS NOT NULL),
    ('fund_manager_name',  true, s.fund_manager_name IS NOT NULL),
    ('risk_level',         true, s.risk_level IS NOT NULL),
    ('min_initial_invest', true, s.min_initial_investment_amount IS NOT NULL),
    ('exit_load_value',    true, s.exit_load_value IS NOT NULL),
    ('lock_in_period',     true, s.lock_in_period IS NOT NULL),
    -- trailing returns: expected only for a LIVE fund that is that old. A closed
    -- fund will never have current trailing returns, so scoring them would make
    -- it permanently incomplete.
    ('return_1year',        s.is_live AND s.old_1y, s.return_1year IS NOT NULL),
    ('return_3year',        s.is_live AND s.old_3y, s.return_3year IS NOT NULL),
    ('return_5year',        s.is_live AND s.old_5y, s.return_5year IS NOT NULL),
    ('return_since_launch', true, s.return_since_launch IS NOT NULL),
    -- portfolio
    ('holdings_analysis',  true, s.holdings_analysis IS NOT NULL),
    ('holdings_sector',    true, coalesce(s.sector_rows, 0) > 0),
    ('portfolio_turnover', NOT coalesce(s.is_etf, false), s.portfolio_turnover IS NOT NULL),
    -- NAV
    ('nav_history',        true, coalesce(s.nav_points, 0) > 0),
    ('nav_fresh',          s.is_live, s.last_nav_date >= current_date - interval '7 days'),
    -- AMC house metadata. amc_description is deliberately NOT scored: no source
    -- returns it (43 of 3,943 funds), so it would be a permanent false gap.
    ('amc_aum',            true, s.amc_aum IS NOT NULL),
    --
    -- PENDING: obtainable from Groww, but 100% NULL until the next enrichment
    -- run, so excluded from data_completeness_pct and reported via pending_*.
    -- Scoped to live funds only -- a closed fund needs no re-enrichment.
    -- TO PROMOTE these into the headline metric after a successful re-enrichment
    -- run, delete their rows from the pending_set CTE at the top of this file.
    -- That one edit is sufficient -- do NOT touch the checklist rows below.
    ('registrar_agent',    s.is_live,
        s.registrar_agent IS NOT NULL AND s.registrar_agent <> ''),
    ('base_expense_ratio', s.is_live, s.base_expense_ratio IS NOT NULL),
    ('expense_ratio_hist', s.is_live,
        s.expense_ratio_history IS NOT NULL
        AND s.expense_ratio_history <> '{}'::jsonb)
  ) AS chk(item, applicable, present)
 GROUP BY s.amfi_scheme_code, s.scheme_name, s.plan_type, s.option_type,
          s.scheme_category, s.amc_id, s.is_defunct, s.nav_not_published,
          s.in_scope, s.facts_source, s.groww_fetched_at, s.last_nav_date,
          s.nav_points, s.is_live;
