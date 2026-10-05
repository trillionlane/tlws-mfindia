-- 007_fund_data_status.sql
-- Per-fund data status: NAV freshness, completeness %, missing items.
--
-- This is a derived VIEW, not stored columns: it recomputes from the underlying
-- tables on every read, so it can never drift out of sync with the data it
-- describes. Keyed on mf.funds so every in-scope fund gets a row, including the
-- ones that have no mf.fund_facts row yet.
--
-- Completeness is scored against a 30-item product checklist. Four items are
-- conditional and only count when they are genuinely obtainable for that fund:
--   return_1year / return_3year / return_5year -> fund must be that old
--   nav_fresh                                  -> live fund that publishes NAV
-- So a 2-year-old fund is scored out of 28, not 30, and can still reach 100%.

CREATE OR REPLACE VIEW mf.v_fund_data_status AS
WITH nav AS (
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
           ff.sharpe_ratio, ff.beta, ff.std_deviation,
           ff.holdings_analysis, ff.portfolio_turnover, ff.groww_rating,
           ff.groww_fetched_at, ff.source AS facts_source,
           a.amc_aum, a.amc_description,
           nav.last_nav_date, nav.nav_points,
           hold.holding_rows, hold.sector_rows
      FROM mf.funds f
      LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code  = f.amfi_scheme_code
      LEFT JOIN mf.amcs       a  ON a.amc_id             = f.amc_id
      LEFT JOIN nav              ON nav.amfi_scheme_code = f.amfi_scheme_code
      LEFT JOIN hold             ON hold.amfi_scheme_code = f.amfi_scheme_code
),
scored AS (
    SELECT b.*,
           (b.inception_date IS NOT NULL
              AND b.inception_date <= current_date - interval '1 year')  AS a_ret1y,
           (b.inception_date IS NOT NULL
              AND b.inception_date <= current_date - interval '3 years') AS a_ret3y,
           (b.inception_date IS NOT NULL
              AND b.inception_date <= current_date - interval '5 years') AS a_ret5y,
           (NOT COALESCE(b.is_defunct, false)
              AND NOT COALESCE(b.nav_not_published, false))              AS a_nav_fresh
      FROM base b
)
SELECT s.amfi_scheme_code,
       s.scheme_name,
       s.plan_type,
       s.option_type,
       s.scheme_category,
       s.amc_id,
       s.is_defunct,
       s.in_scope,
       s.facts_source,
       s.groww_fetched_at,
       -- (1) last NAV freshness
       s.last_nav_date,
       (current_date - s.last_nav_date)                     AS nav_age_days,
       (s.last_nav_date >= current_date - interval '7 days') AS nav_is_fresh,
       s.nav_points,
       -- (2) + (3) completeness and the specific gaps
       count(*) FILTER (WHERE chk.applicable)                AS items_expected,
       count(*) FILTER (WHERE chk.applicable AND chk.present) AS items_present,
       round(100.0 * count(*) FILTER (WHERE chk.applicable AND chk.present)
             / nullif(count(*) FILTER (WHERE chk.applicable), 0), 1) AS data_completeness_pct,
       coalesce(array_agg(chk.item ORDER BY chk.item)
                FILTER (WHERE chk.applicable AND NOT chk.present), '{}') AS missing_items
  FROM scored s
  CROSS JOIN LATERAL (VALUES
    -- identity / spine
    ('scheme_name',        true,  s.scheme_name IS NOT NULL AND s.scheme_name <> ''),
    ('plan_type',          true,  s.plan_type IS NOT NULL),
    ('option_type',        true,  s.option_type IS NOT NULL AND s.option_type <> 'UNKNOWN'),
    ('scheme_category',    true,  s.scheme_category IS NOT NULL),
    ('isin_primary',       true,  s.isin_primary IS NOT NULL),
    ('amc_link',           true,  s.amc_id IS NOT NULL),
    -- core facts
    ('aum',                true,  s.aum IS NOT NULL),
    ('expense_ratio',      true,  s.expense_ratio IS NOT NULL),
    ('inception_date',     true,  s.inception_date IS NOT NULL),
    ('benchmark',          true,  s.benchmark IS NOT NULL),
    ('fund_manager_name',  true,  s.fund_manager_name IS NOT NULL),
    ('risk_level',         true,  s.risk_level IS NOT NULL),
    ('min_initial_invest', true,  s.min_initial_investment_amount IS NOT NULL),
    ('exit_load_value',    true,  s.exit_load_value IS NOT NULL),
    ('lock_in_period',     true,  s.lock_in_period IS NOT NULL),
    -- trailing returns (only expected once the fund is that old)
    ('return_1year',       s.a_ret1y, s.return_1year IS NOT NULL),
    ('return_3year',       s.a_ret3y, s.return_3year IS NOT NULL),
    ('return_5year',       s.a_ret5y, s.return_5year IS NOT NULL),
    ('return_since_launch',true,  s.return_since_launch IS NOT NULL),
    -- risk metrics (need >=1y of history to be computable)
    ('sharpe_ratio',       s.a_ret1y, s.sharpe_ratio IS NOT NULL),
    ('beta',               s.a_ret1y, s.beta IS NOT NULL),
    ('std_deviation',      s.a_ret1y, s.std_deviation IS NOT NULL),
    -- portfolio
    ('holdings_analysis',  true,  s.holdings_analysis IS NOT NULL),
    ('holdings_sector',    true,  coalesce(s.sector_rows, 0) > 0),
    ('portfolio_turnover', NOT coalesce(s.is_etf, false), s.portfolio_turnover IS NOT NULL),
    -- NAV
    ('nav_history',        true,  coalesce(s.nav_points, 0) > 0),
    ('nav_fresh',          s.a_nav_fresh, s.last_nav_date >= current_date - interval '7 days'),
    -- AMC house metadata
    ('amc_aum',            true,  s.amc_aum IS NOT NULL),
    ('amc_description',    true,  s.amc_description IS NOT NULL),
    -- rating
    ('groww_rating',       true,  s.groww_rating IS NOT NULL)
  ) AS chk(item, applicable, present)
 GROUP BY s.amfi_scheme_code, s.scheme_name, s.plan_type, s.option_type,
          s.scheme_category, s.amc_id, s.is_defunct, s.in_scope, s.facts_source,
          s.groww_fetched_at, s.last_nav_date, s.nav_points;
