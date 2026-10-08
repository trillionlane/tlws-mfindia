-- 012_drop_aggregator_identity.sql
-- Drop the aggregator-identity fields (compliance: MFDataIndia does not hold
-- or disseminate the source aggregator's own IDs, slugs, or ratings).
--
--   scripbox_fund_id  (uuid)      -- Scripbox internal fund id
--   groww_slug        (text)      -- Groww URL slug
--   groww_rating      (numeric)   -- Groww's rating of the fund
--
-- These are provenance/branding markers of the aggregators, not fund data.
-- The fund facts they sit alongside (AUM, returns, benchmark, ...) remain.
-- The columns were created in 003 (scripbox_fund_id), 004/006 (groww_slug)
-- and 005 (groww_rating); this forward migration removes them rather than
-- rewriting migration history. No live view references these columns.
--
-- Idempotent: DROP ... IF EXISTS.

DROP INDEX IF EXISTS mf.ix_facts_scripbox_id;
DROP INDEX IF EXISTS mf.ix_facts_groww_slug;

ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS scripbox_fund_id;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS groww_slug;
ALTER TABLE mf.fund_facts DROP COLUMN IF EXISTS groww_rating;