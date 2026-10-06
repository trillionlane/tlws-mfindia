-- =============================================================================
-- MFDataIndia — core schema
-- Target: PostgreSQL 16+ (validated against PostgreSQL 18)
--
-- Scope: Indian Regular Plan mutual fund schemes, all options/variants,
--        fund metadata, ISIN/AMFI codes, and NAV history (5-year required
--        window, fuller history retained where freely available).
--
-- Design rules enforced here:
--   * amfi_scheme_code (INTEGER) is the canonical identity. Verified 1:1 with
--     api.mfapi.in schemeCode. INTEGER chosen over VARCHAR: 4 bytes vs ~13,
--     which matters at ~26M nav_history rows.
--   * NAV is NUMERIC(18,4), never FLOAT. AMFI publishes 4 decimals and
--     financial data must not accumulate binary float error.
--   * nav_history is RANGE-partitioned by nav_date (yearly) so the 5-year
--     window prunes partitions and old years can be dropped cheaply.
--   * in_scope is a GENERATED column derived from plan_type/is_etf, so it can
--     never drift from its inputs. The expression mirrors
--     mfdataindia.ingest.amfi_navall.classify_plan exactly.
--   * VARCHAR + CHECK is used instead of PG ENUMs: enums are awkward to extend
--     and cannot be reordered, CHECK constraints migrate trivially.
--   * Reconciliation and checkpoints are tables, not log files — they are
--     queried state.
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS mf;
SET search_path TO mf, public;

-- =============================================================================
-- 1. amcs — fund houses (asset management companies)
-- =============================================================================
-- amfi_amc_name is the exact string from the NAVAll.txt section header and is
-- the join key from the AMFI adapter. normalised_name supports fuzzy matching
-- against Scripbox / mfapi.in spellings ("SBI" vs "SBI Mutual Fund").

CREATE TABLE IF NOT EXISTS mf.amcs (
    amc_id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    amfi_amc_name    text        NOT NULL UNIQUE,
    normalised_name  text        NOT NULL,
    website          text,
    portfolio_url    text,
    factsheet_url    text,
    is_active        boolean     NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  mf.amcs IS 'Indian mutual fund asset management companies';
COMMENT ON COLUMN mf.amcs.amfi_amc_name IS 'Exact AMC header string from AMFI NAVAll.txt';
COMMENT ON COLUMN mf.amcs.portfolio_url IS 'Portfolio-disclosure landing page (AMFI directory seed)';

CREATE INDEX IF NOT EXISTS ix_amcs_normalised ON mf.amcs (normalised_name);

-- =============================================================================
-- 2. funds — one row per AMFI scheme code (the finest identity we have)
-- =============================================================================
-- A "fund" here is a single AMFI scheme code, i.e. one plan+option variant.
-- The investor-facing scheme that groups Regular/Direct/Growth/IDCW together
-- is derived in mf.fund_variants / mf.v_scheme_groups.

CREATE TABLE IF NOT EXISTS mf.funds (
    amfi_scheme_code   integer PRIMARY KEY,
    mfapi_scheme_code  integer,
    scheme_name        text NOT NULL,
    scheme_name_norm   text NOT NULL,

    amc_id             integer NOT NULL REFERENCES mf.amcs (amc_id),

    -- taxonomy: AMFI is authoritative; mfapi.in is often a stale taxonomy version
    scheme_type        text NOT NULL CHECK (scheme_type IN
                         ('OPEN_ENDED', 'CLOSE_ENDED', 'INTERVAL')),
    scheme_category    text NOT NULL,
    scheme_category_raw text,
    category_source    text NOT NULL DEFAULT 'AMFI' CHECK (category_source IN
                         ('AMFI', 'MFAPI', 'XWALK', 'MANUAL')),

    -- plan / option classification (see amfi_navall.classify_plan/classify_option)
    plan_type          text NOT NULL DEFAULT 'UNLABELLED' CHECK (plan_type IN
                         ('REGULAR', 'DIRECT', 'RETAIL', 'INSTITUTIONAL', 'UNLABELLED')),
    -- How plan_type was determined. Scope depends on this, so it must be a real
    -- column (a GENERATED in_scope cannot reference a parser-internal field).
    --   COLUMN             the current feed's explicit Plan column. Authoritative.
    --   NAME               legacy feed: name inference was the only signal.
    --   COLUMN_BLANK       feed has a Plan column but left it empty => plan unknown.
    --   COLUMN_UNRECOGNISED feed has a Plan column with a novel value.
    plan_source        text NOT NULL DEFAULT 'NAME' CHECK (plan_source IN
                         ('COLUMN', 'NAME', 'COLUMN_BLANK', 'COLUMN_UNRECOGNISED')),
    option_type        text NOT NULL DEFAULT 'UNKNOWN' CHECK (option_type IN
                         ('GROWTH', 'IDCW', 'DIVIDEND', 'BONUS', 'UNKNOWN')),
    periodicity        text CHECK (periodicity IS NULL OR periodicity IN
                         ('DAILY', 'WEEKLY', 'FORTNIGHTLY', 'MONTHLY', 'QUARTERLY',
                          'HALF_YEARLY', 'ANNUAL', 'PERIODIC')),

    is_etf             boolean NOT NULL DEFAULT false,
    is_defunct         boolean NOT NULL DEFAULT false,
    nav_not_published  boolean NOT NULL DEFAULT false,

    -- In-scope = Regular Plan, all options. Mirrors amfi_navall.resolve_plan:
    --   plan == REGULAR, or (plan == UNLABELLED and not an ETF AND the plan was
    --   inferred from the name because no Plan column existed).
    -- A blank Plan column (current feed) means "plan unknown" -> NOT in scope.
    -- Retail / Institutional are separate plan labels: stored, not in scope.
    in_scope           boolean GENERATED ALWAYS AS (
                           plan_type = 'REGULAR'
                           OR (plan_type = 'UNLABELLED' AND NOT is_etf
                               AND plan_source = 'NAME')
                       ) STORED,

    -- AMFI ISIN columns, preserving their exact dual-purpose semantics.
    -- Column A = Growth ISIN for growth options, Div-Payout ISIN for IDCW.
    -- Column B = Dividend-Reinvestment ISIN.
    isin_growth_or_div_payout char(12) CHECK (isin_growth_or_div_payout IS NULL
                           OR (length(isin_growth_or_div_payout) = 12
                               AND isin_growth_or_div_payout ~ '^[A-Z][A-Z0-9]{11}$')),
    isin_div_reinvest         char(12) CHECK (isin_div_reinvest IS NULL
                           OR (length(isin_div_reinvest) = 12
                               AND isin_div_reinvest ~ '^[A-Z][A-Z0-9]{11}$')),

    -- Resolved primary ISIN, for joins to Scripbox / CAM / NSDL.
    isin_primary       char(12) GENERATED ALWAYS AS (
                           coalesce(isin_growth_or_div_payout, isin_div_reinvest)
                       ) STORED,

    -- lifecycle
    first_seen_in_source date,
    last_seen_in_source  date,
    is_active            boolean NOT NULL DEFAULT true,

    -- AMFI-authoritative metadata overrides mfapi.in where they disagree.
    metadata_authority   text NOT NULL DEFAULT 'AMFI' CHECK (metadata_authority IN
                         ('AMFI', 'MFAPI', 'SCRIPBOX', 'MANUAL')),

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  mf.funds IS 'One row per AMFI scheme code (plan+option variant)';
COMMENT ON COLUMN mf.funds.isin_growth_or_div_payout IS
    'AMFI NAVAll.txt column A: Growth ISIN for growth options, Div-Payout ISIN for IDCW';
COMMENT ON COLUMN mf.funds.isin_div_reinvest IS 'AMFI NAVAll.txt column B: Dividend-Reinvestment ISIN';
COMMENT ON COLUMN mf.funds.in_scope IS 'Generated: Regular Plan (or unlabelled non-ETF), all options';
COMMENT ON COLUMN mf.funds.is_defunct IS 'Dead scheme: NAV 0.0000 / N.A., or name flagged Defunct / OLD-';

CREATE INDEX IF NOT EXISTS ix_funds_scope_type   ON mf.funds (in_scope, scheme_type);
CREATE INDEX IF NOT EXISTS ix_funds_amc_cat      ON mf.funds (amc_id, scheme_category);
CREATE INDEX IF NOT EXISTS ix_funds_isin_primary ON mf.funds (isin_primary)
    WHERE isin_primary IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_funds_isin_payout  ON mf.funds (isin_growth_or_div_payout)
    WHERE isin_growth_or_div_payout IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_funds_isin_reinv   ON mf.funds (isin_div_reinvest)
    WHERE isin_div_reinvest IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_funds_name_norm    ON mf.funds (scheme_name_norm);
CREATE INDEX IF NOT EXISTS ix_funds_active_scope ON mf.funds (is_active, in_scope);

COMMIT;
