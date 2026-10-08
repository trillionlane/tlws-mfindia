-- 011_amc_factsheets.sql
-- Support for the AMC-factsheet enrichment source (Option A).
--
-- The AMC's own monthly factsheet PDF is the official source of record for
-- beta / Sharpe / standard deviation where the other providers are NULL.
-- Two changes:
--
--   1. mf.source_metadata.source gains 'AMC' — the factsheet fetch itself is
--      recorded with entity_kind='FACTSHEET' (already in the CHECK).
--   2. mf.factsheet_fields_log — provenance for every (fund, field, period)
--      the factsheet fill wrote: which AMC document, NAV as-of date, PDF
--      page and URL. Keyed per period so a newer month's figure supersedes
--      the log row while the fund_facts fill remains fill-if-missing.
--
-- Note: mf.fund_facts.source is deliberately NOT changed — 'source' names the
-- owner of the ROW (Scripbox/Groww/computed), and factsheet values are merged
-- into rows owned by other sources. Provenance lives here instead.
--
-- Idempotent: drop/re-add the constraint, CREATE TABLE IF NOT EXISTS.

ALTER TABLE mf.source_metadata DROP CONSTRAINT IF EXISTS source_metadata_source_check;
ALTER TABLE mf.source_metadata ADD CONSTRAINT source_metadata_source_check
    CHECK (source = ANY (ARRAY['MFAPI', 'AMFI', 'AMFI_HISTORY', 'SCRIPBOX', 'CAM',
                              'WAYBACK', 'MANUAL', 'AMC']));

CREATE TABLE IF NOT EXISTS mf.factsheet_fields_log (
    amfi_scheme_code INTEGER NOT NULL REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,
    field            TEXT NOT NULL,
    value            NUMERIC(9,4),
    amc              TEXT NOT NULL,
    factsheet_period TEXT NOT NULL,
    as_of            DATE,
    pdf_page         INTEGER,
    doc_url          TEXT,
    extracted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (amfi_scheme_code, field, factsheet_period)
);

COMMENT ON TABLE mf.factsheet_fields_log IS
    'Provenance for fund_facts values taken from an AMC monthly factsheet '
    '(AMC, document period, NAV as-of date, PDF page, source URL). '
    'Filled by scripts/enrich_amc_factsheets.py; fill-if-missing semantics.';