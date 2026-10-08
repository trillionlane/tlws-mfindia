-- 010_computed_metrics.sql
-- Support for filling NULL fund_facts risk/return fields from our own NAV
-- series (scripts/compute_metrics.py, per the compute-don't-scrape rule).
--
-- Two changes:
--   1. fund_facts.source gains 'COMPUTED'. Rows where the enrichment sources
--      (Scripbox/Groww) had nothing and the value was derived from
--      mf.nav_history carry source='COMPUTED' — distinct from 'MANUAL',
--      which means a human keyed data in.
--   2. mf.computed_fields_log — provenance for every (fund, field) the
--      compute job fills: which method, which window, which as-of NAV date.
--      Re-runs upsert, so the log always reflects the latest computation.
--
-- Idempotent: drop/re-add the constraint, CREATE TABLE IF NOT EXISTS.

ALTER TABLE mf.fund_facts DROP CONSTRAINT IF EXISTS fund_facts_source_check;
ALTER TABLE mf.fund_facts ADD CONSTRAINT fund_facts_source_check
    CHECK (source = ANY (ARRAY['SCRIPBOX', 'AMFI', 'MFAPI', 'GROWW', 'MANUAL', 'COMPUTED']));

CREATE TABLE IF NOT EXISTS mf.computed_fields_log (
    amfi_scheme_code INTEGER NOT NULL REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,
    field            TEXT NOT NULL,
    value            NUMERIC(9,4),
    method           TEXT NOT NULL,
    window_label     TEXT,
    as_of            DATE,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (amfi_scheme_code, field)
);

COMMENT ON TABLE mf.computed_fields_log IS
    'Provenance for fund_facts values derived from mf.nav_history by '
    'scripts/compute_metrics.py (field, method, window, as-of NAV date).';