-- =============================================================================
-- MFDataIndia — Groww enrichment
-- Target: PostgreSQL 16+ (validated against PostgreSQL 18 via PGlite)
-- Depends on: 001, 002, 003
--
-- Groww fund pages (robots-allowed /mutual-funds/<slug>) carry a rich
-- __NEXT_DATA__ payload, joined to mf.funds on ISIN. Unlike Scripbox's bulk
-- list (direct-only), Groww serves REGULAR plan pages with the correct
-- regular-plan expense ratio, which is why it is the preferred enrichment source.
--
-- This migration is additive only: new columns and new tables, all idempotent.
-- =============================================================================

BEGIN;
SET search_path TO mf, public;

-- 'GROWW' becomes a valid enrichment source value.
ALTER TABLE mf.fund_facts DROP CONSTRAINT IF EXISTS fund_facts_source_check;
ALTER TABLE mf.fund_facts ADD CONSTRAINT fund_facts_source_check
    CHECK (source IN ('SCRIPBOX', 'AMFI', 'MFAPI', 'GROWW', 'MANUAL'));

-- Groww fields not present in the Scripbox shape. Fact-level, not opinion.
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS benchmark             text;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS benchmark_name        text;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS fund_manager_name     text;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS risk_level            text;   -- SEBI riskometer
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS base_expense_ratio    numeric(9,4);
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS super_category        text;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS sub_category          text;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS registrar_agent       text;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS launch_date           date;
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS expense_ratio_history jsonb;  -- [{date, expense_ratio}]
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS fund_manager_details  jsonb;  -- [{person_name, education, experience}]
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS groww_slug            text;

CREATE INDEX IF NOT EXISTS ix_facts_groww_slug ON mf.fund_facts (groww_slug)
    WHERE groww_slug IS NOT NULL;

-- Top holdings with sector/weight — a relational list the UI renders directly.
CREATE TABLE IF NOT EXISTS mf.fund_holdings (
    amfi_scheme_code integer NOT NULL REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,
    portfolio_date   date,
    holding_rank     integer NOT NULL,
    company_name     text NOT NULL,
    sector_name      text,
    nature_name      text,           -- EQUITY / CASH / DEBT ...
    market_value     numeric(20,4),  -- INR crore
    weight_pct       numeric(9,4),   -- corpus_per
    rating           text,
    fetched_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (amfi_scheme_code, holding_rank)
);

COMMENT ON TABLE mf.fund_holdings IS 'Top portfolio holdings per fund (from Groww), with sector and weight';

CREATE INDEX IF NOT EXISTS ix_holdings_fund ON mf.fund_holdings (amfi_scheme_code);
CREATE INDEX IF NOT EXISTS ix_holdings_sector ON mf.fund_holdings (sector_name)
    WHERE sector_name IS NOT NULL;

COMMIT;
