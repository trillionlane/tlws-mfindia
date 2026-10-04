-- =============================================================================
-- MFDataIndia — Groww deep enrichment
-- Target: PostgreSQL 16+
-- Depends on: 001, 002, 003, 004
--
-- Groww fund pages carry the fields only it has: the "Holdings analysis"
-- breakdown, AMC-house metadata, Groww/Crisil ratings, sub-type, exit-load /
-- lock-in / portfolio-turnover, the full returns + category-comparison payload,
-- and sector-tagged top holdings. All additive + idempotent.
--
-- mf.fund_facts is keyed by amfi_scheme_code (a single row; `source` is a tag),
-- so the Groww loader merges into the existing (usually SCRIPBOX) row with a
-- COALESCE upsert rather than replacing it.
-- =============================================================================

BEGIN;
SET search_path TO mf, public;

-- ratings / category sub-type
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS groww_rating    numeric(4,2);
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS crisil_rating   text;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS sub_type        text;   -- category_info.sub_type (e.g. 'Gilt')

-- risk / transactional descriptors
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS exit_load_value   text;      -- 'Nil' / '0.25%' ...
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS lock_in_period    text;      -- formatted {y/m/d}
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS portfolio_turnover numeric(9,2);

-- short / annualised returns Groww publishes that Scripbox lacks
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS return_1week    numeric(9,4);
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS return_1month   numeric(9,4);
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS return_9month   numeric(9,4);

-- risk metrics from Groww return_stats
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS sharpe_ratio    numeric(9,4);
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS beta            numeric(9,4);
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS std_deviation   numeric(9,4);
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS risk_rating     text;

-- full Groww returns + category-comparison + ranks payload, kept whole so the
-- data can be re-mapped later without re-fetching Groww
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS groww_return_stats jsonb;

-- the "Holdings analysis": aggregated asset-class + sector breakdown computed
-- from Groww's sector/nature-tagged top holdings
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS holdings_analysis jsonb;

-- when Groww last merged into this row (independent of source / fetched_at)
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS groww_fetched_at timestamptz;

COMMENT ON COLUMN mf.fund_facts.holdings_analysis   IS 'Aggregated asset-class + sector breakdown (Groww holdings) for the UI "Holdings analysis"';
COMMENT ON COLUMN mf.fund_facts.groww_return_stats  IS 'Full Groww return_stats + simple_return payload (returns, category comparison, ranks, risk metrics)';

-- AMC house metadata (Groww amc_info) — enriches the AMFI-registered row in place
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_aum          numeric(20,6);
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_rank         integer;
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_launch_date  date;
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_address      text;
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_description  text;
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_sponsor      text;
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_source       text;
ALTER TABLE mf.amcs ADD COLUMN IF NOT EXISTS amc_fetched_at   timestamptz;

COMMIT;