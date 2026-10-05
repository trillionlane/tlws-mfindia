-- =============================================================================
-- MFDataIndia — Groww enrichment provenance
-- Target: PostgreSQL 16+
-- Depends on: 005
--
-- Two gaps the first deep-enrichment pass left behind:
--
--   1. mf.fund_facts.groww_slug already existed but was never written, so no row
--      could be traced back to the Groww URL that produced it.
--   2. Groww does not publish a page for every option variant (there are no
--      BONUS pages, and many IDCW funds 404), so a sibling option variant of the
--      same fund is used as the source. That is legitimate — growth and IDCW
--      share one portfolio, verified sector-identical in 729/737 sampled pairs —
--      but it must be explicit and auditable.
--
-- groww_source_mode records how a row got its Groww data:
--   'direct'    — fetched from this fund's own Groww page
--   'inherited' — portfolio fields copied from groww_inherited_from
--
-- groww_inherited_from is the amfi_scheme_code of the sibling row the
-- portfolio-level fields were copied from (NULL when mode is 'direct').
-- Option-specific fields (returns, NAV, expense ratio) are never inherited.
-- =============================================================================

BEGIN;
SET search_path TO mf, public;

-- which Groww slug produced this row (NULL for inherited/never-fetched rows)
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS groww_slug text;

-- 'direct' | 'inherited'
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS groww_source_mode text;

-- sibling amfi_scheme_code the portfolio fields were inherited from
ALTER TABLE mf.fund_facts ADD COLUMN IF NOT EXISTS groww_inherited_from integer;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'fund_facts_groww_source_mode_ck') THEN
    ALTER TABLE mf.fund_facts ADD CONSTRAINT fund_facts_groww_source_mode_ck
      CHECK (groww_source_mode IS NULL OR groww_source_mode IN ('direct','inherited'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'fund_facts_groww_inherit_ck') THEN
    -- An inherited row must name its source; anything else must not claim one.
    ALTER TABLE mf.fund_facts ADD CONSTRAINT fund_facts_groww_inherit_ck CHECK (
      (groww_source_mode = 'inherited' AND groww_inherited_from IS NOT NULL) OR
      (groww_source_mode IS DISTINCT FROM 'inherited')
    );
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS fund_facts_groww_inherited_from_ix
  ON mf.fund_facts (groww_inherited_from)
  WHERE groww_inherited_from IS NOT NULL;

COMMENT ON COLUMN mf.fund_facts.groww_slug IS 'Groww URL slug this row''s data came from';
COMMENT ON COLUMN mf.fund_facts.groww_source_mode IS 'direct = own Groww page; inherited = portfolio fields copied from a sibling option variant';
COMMENT ON COLUMN mf.fund_facts.groww_inherited_from IS 'amfi_scheme_code of the sibling option variant the portfolio fields were copied from';

COMMIT;
