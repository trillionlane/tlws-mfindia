-- =============================================================================
-- MFDataIndia — enrichment, provenance, taxonomy, reconciliation
-- Target: PostgreSQL 16+ (validated against PostgreSQL 18)
-- Depends on: 001_core_schema.sql, 002_nav_and_views.sql
-- =============================================================================

BEGIN;
SET search_path TO mf, public;

-- =============================================================================
-- 6. fund_facts — factual enrichment (primary source: Scripbox factsheetData)
-- =============================================================================
-- Column names deliberately mirror Scripbox's own keys so provenance is
-- traceable and the adapter is a near-direct mapping. Nested structures
-- (holdings, composition, sip/stp/swp, stats_variables) stay in JSONB rather
-- than being flattened: they are variable-shape and rarely queried by field.
--
-- LICENSING BOUNDARY: this table holds only factual data (AUM, expense ratio,
-- inception date, minimums, published returns). Scripbox's proprietary opinion
-- output lives in mf.fund_opinions so a redistribution-safe export can simply
-- omit that one table.

CREATE TABLE IF NOT EXISTS mf.fund_facts (
    amfi_scheme_code   integer PRIMARY KEY
                         REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,

    -- Scripbox identity / join keys
    scripbox_fund_id   uuid,
    fund_slug          text,
    plan_id            integer,
    sub_plan_id        text,
    plan_name          text,
    rta_scheme_code    text,

    -- classification as published by the source
    asset_class            text,
    asset_class_code       text,
    sub_asset_class        text,
    sub_asset_class_code   text,
    sebi_category_name     text,
    taxability             text,
    openended              boolean,

    -- sizes / costs. aum is in INR crore as published.
    aum                numeric(20,6),
    expense_ratio      numeric(9,4),
    face_value         numeric(18,4),

    inception_date     date,

    -- transactionability
    status                     text,
    transaction_status         text,
    is_active_status           boolean,
    is_investable              boolean,
    is_purchase_allowed        boolean,
    is_withdrawal_allowed      boolean,
    is_sip_allowed             boolean,
    is_stp_allowed             boolean,
    is_swp_allowed             boolean,
    is_switch_in_allowed       boolean,
    is_switch_out_allowed      boolean,
    is_nfo                     boolean,

    -- minimums
    min_initial_investment_amount    numeric(18,2),
    min_subsequent_investment_amount numeric(18,2),
    min_withdrawal_amount            numeric(18,2),
    min_investment_multiples         numeric(18,2),

    -- published trailing returns (%). Recomputable from mf.nav_history; stored
    -- so our own calculation can be cross-checked against the source.
    returns_as_on_date   date,
    return_1day          numeric(9,4),
    return_3month        numeric(9,4),
    return_6month        numeric(9,4),
    return_1year         numeric(9,4),
    return_2year         numeric(9,4),
    return_3year         numeric(9,4),
    return_4year         numeric(9,4),
    return_5year         numeric(9,4),
    return_7year         numeric(9,4),
    return_10year        numeric(9,4),
    return_since_launch  numeric(9,4),

    -- source NAV snapshot, used to reconcile against mf.nav_history
    source_nav        numeric(18,4),
    source_nav_date   date,

    -- variable-shape payloads kept whole
    composition         jsonb,
    sectorwise_holding  jsonb,
    asset_holding       jsonb,
    holdings_maturity   jsonb,
    exit_load           jsonb,
    sip                 jsonb,
    stp                 jsonb,
    swp                 jsonb,
    stats_variables     jsonb,
    category_return     jsonb,
    fund_manager        jsonb,
    fund_variant        jsonb,

    source           text NOT NULL DEFAULT 'SCRIPBOX' CHECK (source IN
                     ('SCRIPBOX', 'AMFI', 'MFAPI', 'MANUAL')),
    source_updated_at timestamptz,
    fetched_at        timestamptz NOT NULL DEFAULT now(),
    raw_payload       jsonb
);

COMMENT ON TABLE  mf.fund_facts IS
    'Factual fund enrichment. Proprietary opinion fields are in mf.fund_opinions (licensing).';
COMMENT ON COLUMN mf.fund_facts.aum IS 'Assets under management in INR crore, as published';
COMMENT ON COLUMN mf.fund_facts.raw_payload IS 'Full source payload, for re-mapping without re-fetching';

CREATE INDEX IF NOT EXISTS ix_facts_slug        ON mf.fund_facts (fund_slug)
    WHERE fund_slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_facts_scripbox_id ON mf.fund_facts (scripbox_fund_id)
    WHERE scripbox_fund_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_facts_sebi_cat    ON mf.fund_facts (sebi_category_name)
    WHERE sebi_category_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_facts_composition ON mf.fund_facts USING gin (composition);

-- =============================================================================
-- 7. fund_opinions — PROPRIETARY third-party opinion output (licensing boundary)
-- =============================================================================
-- These are Scripbox's own editorial/analytical judgements, not facts. They are
-- isolated in a dedicated table so that:
--   * a redistribution-safe Parquet/API export can omit this table entirely;
--   * the licensing obligation is visible in the schema, not buried in a column;
--   * dropping the enrichment source later is a single DROP TABLE.
-- Do NOT expose this table through a public API without clearing licensing.

CREATE TABLE IF NOT EXISTS mf.fund_opinions (
    amfi_scheme_code   integer PRIMARY KEY
                         REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,
    provider           text NOT NULL DEFAULT 'SCRIPBOX',

    sb_recommendation           boolean,
    fund_recommendation_rating  integer CHECK (fund_recommendation_rating IS NULL
                                  OR fund_recommendation_rating BETWEEN 0 AND 5),
    es_score                    numeric(9,4),
    fund_score                  jsonb,
    risk_level                  text,
    objective                   text,

    portfolio_audit_ic_blacklist boolean,

    source_updated_at timestamptz,
    fetched_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE mf.fund_opinions IS
    'LICENSING-SENSITIVE: third-party proprietary opinion/rating output. Omit from public exports.';

-- =============================================================================
-- 8. source_metadata — provenance for every fetch
-- =============================================================================
-- Records what was fetched, when, from where, and how it was obtained. This is
-- what makes the dataset auditable and lets a re-run be distinguished from a
-- re-fetch. Also carries the HTTP fingerprint needed to reproduce a pull.

CREATE TABLE IF NOT EXISTS mf.source_metadata (
    fetch_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source         text   NOT NULL CHECK (source IN
                   ('MFAPI', 'AMFI', 'AMFI_HISTORY', 'SCRIPBOX', 'CAM', 'WAYBACK', 'MANUAL')),
    endpoint       text   NOT NULL,
    entity_kind    text   NOT NULL CHECK (entity_kind IN
                   ('LATEST_NAV', 'NAV_HISTORY', 'SCHEME_LIST', 'FUND_DETAIL',
                    'AMC_LIST', 'AUM', 'PORTFOLIO', 'FACTSHEET', 'OTHER')),
    entity_key     text,

    http_status    integer,
    content_type   text,
    content_hash   text,
    content_bytes  bigint,

    -- how the bytes were obtained: live, or from an archive because the origin
    -- is geo-blocked from the current network.
    acquisition    text NOT NULL DEFAULT 'LIVE' CHECK (acquisition IN
                   ('LIVE', 'WAYBACK', 'USER_PROVIDED', 'CACHED', 'FIXTURE')),
    wayback_ts     text,
    user_agent     text,
    request_url    text,

    records_in     integer,
    records_ok     integer,
    records_quarantined integer,

    fetched_at     timestamptz NOT NULL DEFAULT now(),
    duration_ms    integer,
    error          text,
    notes          jsonb
);

COMMENT ON TABLE  mf.source_metadata IS 'Provenance/audit trail for every source fetch';
COMMENT ON COLUMN mf.source_metadata.acquisition IS
    'LIVE, WAYBACK (origin geo-blocked), USER_PROVIDED (India-server download), CACHED, or FIXTURE';
COMMENT ON COLUMN mf.source_metadata.content_hash IS 'SHA-256 of the payload, for change detection';

CREATE INDEX IF NOT EXISTS ix_srcmeta_source_time ON mf.source_metadata (source, fetched_at DESC);
CREATE INDEX IF NOT EXISTS ix_srcmeta_entity      ON mf.source_metadata (entity_kind, entity_key);
CREATE INDEX IF NOT EXISTS ix_srcmeta_hash        ON mf.source_metadata (content_hash)
    WHERE content_hash IS NOT NULL;

-- =============================================================================
-- 9. quality_flags — data-quality issues, queryable not logged
-- =============================================================================
-- Every defect found by parsing or reconciliation lands here with a stable type,
-- so coverage can be reported and regressions detected between runs. A flag with
-- a NULL amfi_scheme_code is dataset-wide.

CREATE TABLE IF NOT EXISTS mf.quality_flags (
    flag_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    amfi_scheme_code integer REFERENCES mf.funds (amfi_scheme_code) ON DELETE CASCADE,
    nav_date         date,
    flag_type        text NOT NULL CHECK (flag_type IN (
                       'NAV_MISMATCH',            -- AMFI vs mfapi.in value differs
                       'CATEGORY_MISMATCH',       -- taxonomy disagreement
                       'SCHEME_TYPE_MISMATCH',
                       'ISIN_MISMATCH',
                       'ISIN_MISSING',
                       'REGULAR_DIRECT_CONFLICT', -- pair disagrees on scheme/category
                       'DEAD_SCHEME',             -- NAV 0 / N.A. / defunct naming
                       'LIFECYCLE_ENDED',         -- close-ended scheme redeemed/matured
                       'NAV_SANITY',              -- implausible jump, non-positive, outlier
                       'NAV_GAP',                 -- missing business days in a series
                       'STALE_SERIES',            -- no recent NAV for an active scheme
                       'DUPLICATE',
                       'MALFORMED_ROW',           -- quarantined during parse
                       'NAME_PARSE_FAILURE',      -- plan/option unrecognised
                       'UNRECOGNISED_OPTION',
                       'COVERAGE_GAP',            -- expected scheme absent from a source
                       'OTHER')),
    severity         text NOT NULL DEFAULT 'WARN' CHECK (severity IN
                     ('INFO', 'WARN', 'ERROR', 'FATAL')),
    message          text NOT NULL,
    details          jsonb,
    source           text,
    run_id           bigint,
    detected_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at      timestamptz,
    resolution_note  text
);

COMMENT ON TABLE mf.quality_flags IS 'Queryable data-quality defects; NULL scheme code = dataset-wide';

CREATE INDEX IF NOT EXISTS ix_qflags_type_time  ON mf.quality_flags (flag_type, detected_at DESC);
CREATE INDEX IF NOT EXISTS ix_qflags_scheme     ON mf.quality_flags (amfi_scheme_code)
    WHERE amfi_scheme_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_qflags_open       ON mf.quality_flags (flag_type, severity)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_qflags_details    ON mf.quality_flags USING gin (details);

-- =============================================================================
-- 10. taxonomy_xwalk — old -> new SEBI category crosswalk
-- =============================================================================
-- Many AMFI/mfapi.in category disagreements are taxonomy versioning, not errors:
-- SEBI recategorised schemes (2017-2020 and later renames), and each source
-- adopts the new labels at a different time. This table resolves old->new so a
-- disagreement can be classified as "expected drift" instead of "data error".

CREATE TABLE IF NOT EXISTS mf.taxonomy_xwalk (
    xwalk_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    old_category   text NOT NULL,
    new_category   text NOT NULL,
    scheme_type    text,
    reason         text CHECK (reason IN
                   ('SEBI_RECATEGORISATION', 'AMC_RENAME', 'SOURCE_SPELLING',
                    'MERGER', 'OTHER')),
    confidence     text NOT NULL DEFAULT 'HIGH' CHECK (confidence IN
                   ('HIGH', 'MEDIUM', 'LOW')),
    evidence       text,
    is_active      boolean NOT NULL DEFAULT true,
    valid_from     date,
    valid_to       date,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (old_category, new_category)
);

COMMENT ON TABLE mf.taxonomy_xwalk IS
    'Old->new scheme category crosswalk; turns taxonomy drift into a classified, expected difference';

CREATE INDEX IF NOT EXISTS ix_xwalk_old ON mf.taxonomy_xwalk (old_category) WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_xwalk_new ON mf.taxonomy_xwalk (new_category) WHERE is_active;

-- =============================================================================
-- 11. reconciliation — cross-source agreement, as recurring state
-- =============================================================================
-- AMFI is authoritative for metadata; api.mfapi.in is the NAV-history workhorse.
-- Every ingest run re-checks their agreement so drift is detected, and each
-- disagreement is *classified*: taxonomy drift is expected, a NAV mismatch is not.

CREATE TABLE IF NOT EXISTS mf.reconciliation_runs (
    run_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    scope         text NOT NULL DEFAULT 'FULL' CHECK (scope IN
                  ('FULL', 'IN_SCOPE_ONLY', 'INCREMENTAL', 'SAMPLE')),
    -- AMFI is authoritative for NAV. mfapi.in is dropped as a NAV source
    -- (unreliable); AMFI vs AMFI_HISTORY cross-check is the live reconciliation.
    -- Sources compared can be extended if a second NAV source is ever re-added.
    sources       text[] NOT NULL DEFAULT ARRAY['AMFI'],
    nav_date      date,
    schemes_compared   integer,
    nav_compared       integer,
    findings_total     integer,
    findings_expected  integer,
    findings_real      integer,
    status        text NOT NULL DEFAULT 'RUNNING' CHECK (status IN
                  ('RUNNING', 'OK', 'WARN', 'FAILED')),
    notes         jsonb
);

COMMENT ON TABLE mf.reconciliation_runs IS 'One row per cross-source reconciliation run';

CREATE TABLE IF NOT EXISTS mf.reconciliation_findings (
    finding_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        bigint NOT NULL REFERENCES mf.reconciliation_runs (run_id)
                    ON DELETE CASCADE,
    amfi_scheme_code integer,
    nav_date      date,
    finding_type  text NOT NULL CHECK (finding_type IN (
                    'NAV_VALUE_DIFF', 'NAV_MISSING_IN_AMFI', 'NAV_MISSING_IN_MFAPI',
                    'CATEGORY_DIFF', 'SCHEME_TYPE_DIFF', 'AMC_DIFF',
                    'ISIN_DIFF', 'ISIN_MISSING_ONE_SIDE',
                    'REGULAR_DIRECT_CONFLICT', 'PLAN_LABEL_DIFF',
                    'CODE_ONLY_IN_ONE_SOURCE')),
    -- Expected drift (taxonomy versioning) vs a genuine data error. This is the
    -- column that keeps the signal-to-noise ratio usable.
    classification text NOT NULL DEFAULT 'UNKNOWN' CHECK (classification IN (
                    'EXPECTED_TAXONOMY_DRIFT', 'EXPECTED_SOURCE_LAG',
                    'REAL_ERROR', 'NEEDS_REVIEW', 'UNKNOWN')),
    xwalk_id      integer REFERENCES mf.taxonomy_xwalk (xwalk_id),
    amfi_value    text,
    other_value   text,
    other_source  text,
    delta         numeric(24,8),
    severity      text NOT NULL DEFAULT 'WARN' CHECK (severity IN
                  ('INFO', 'WARN', 'ERROR', 'FATAL')),
    details       jsonb,
    detected_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  mf.reconciliation_findings IS 'Individual cross-source disagreements, classified';
COMMENT ON COLUMN mf.reconciliation_findings.classification IS
    'EXPECTED_TAXONOMY_DRIFT / EXPECTED_SOURCE_LAG are benign; REAL_ERROR needs action';

CREATE INDEX IF NOT EXISTS ix_recon_run       ON mf.reconciliation_findings (run_id);
CREATE INDEX IF NOT EXISTS ix_recon_type      ON mf.reconciliation_findings (finding_type, classification);
CREATE INDEX IF NOT EXISTS ix_recon_scheme    ON mf.reconciliation_findings (amfi_scheme_code)
    WHERE amfi_scheme_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_recon_real      ON mf.reconciliation_findings (finding_type)
    WHERE classification = 'REAL_ERROR';

-- =============================================================================
-- 12. ingest_checkpoints — resumable / incremental ingestion
-- =============================================================================
-- NAV history download is ~4,352 sequential fetches; a run must survive
-- interruption. One row per (source, entity) holds the cursor to resume from.

CREATE TABLE IF NOT EXISTS mf.ingest_checkpoints (
    source        text NOT NULL,
    entity_kind   text NOT NULL,
    entity_key    text NOT NULL,
    cursor_value  text,
    last_nav_date date,
    records_done  integer NOT NULL DEFAULT 0,
    status        text NOT NULL DEFAULT 'PENDING' CHECK (status IN
                  ('PENDING', 'IN_PROGRESS', 'DONE', 'FAILED', 'SKIPPED')),
    attempts      integer NOT NULL DEFAULT 0,
    last_error    text,
    started_at    timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, entity_kind, entity_key)
);

COMMENT ON TABLE mf.ingest_checkpoints IS 'Resume cursors for resumable/incremental ingestion';

CREATE INDEX IF NOT EXISTS ix_ckpt_status ON mf.ingest_checkpoints (status, source)
    WHERE status <> 'DONE';

-- =============================================================================
-- 13. Operational views — health and coverage reporting
-- =============================================================================

-- Partition health. nav_history_default must stay empty: a row there means a NAV
-- date fell outside every yearly range, which is a data-quality signal.
CREATE OR REPLACE VIEW mf.v_partition_health AS
SELECT c.relname                                     AS partition_name,
       pg_get_expr(c.relpartbound, c.oid)            AS bounds,
       c.reltuples::bigint                           AS approx_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_inherits  i ON i.inhrelid = c.oid
JOIN pg_class     p ON p.oid = i.inhparent
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE p.relname = 'nav_history' AND n.nspname = 'mf'
ORDER BY c.relname;

COMMENT ON VIEW mf.v_partition_health IS
    'nav_history partitions with approximate row counts; nav_history_default must stay empty';

-- Scheme coverage. Cheap: funds only, no join to nav_history.
CREATE OR REPLACE VIEW mf.v_coverage AS
SELECT count(*)                                                       AS schemes_total,
       count(*) FILTER (WHERE in_scope)                               AS in_scope_total,
       count(*) FILTER (WHERE in_scope AND NOT is_defunct)            AS in_scope_live,
       count(*) FILTER (WHERE in_scope AND is_defunct)                AS in_scope_defunct,
       count(*) FILTER (WHERE in_scope AND isin_primary IS NOT NULL)  AS in_scope_with_isin,
       count(*) FILTER (WHERE in_scope AND option_type = 'UNKNOWN')   AS in_scope_option_unknown,
       count(*) FILTER (WHERE plan_type = 'DIRECT')                   AS direct_total,
       count(*) FILTER (WHERE plan_type = 'RETAIL')                   AS retail_total,
       count(*) FILTER (WHERE plan_type = 'INSTITUTIONAL')            AS institutional_total,
       count(*) FILTER (WHERE is_etf)                                 AS etf_total,
       count(DISTINCT amc_id)                                         AS amcs,
       count(DISTINCT scheme_category)                                AS categories
FROM mf.funds;

COMMENT ON VIEW mf.v_coverage IS 'Scheme-level coverage summary (funds only; cheap)';

-- NAV coverage. Aggregates the full nav_history partitioned table, so this is
-- expensive at ~26M rows — run off-peak or materialise it on a schedule.
CREATE OR REPLACE VIEW mf.v_nav_coverage AS
SELECT f.amfi_scheme_code,
       f.scheme_name,
       f.plan_type,
       f.option_type,
       f.scheme_category,
       f.is_defunct,
       min(n.nav_date)                          AS first_nav_date,
       max(n.nav_date)                          AS last_nav_date,
       count(*)                                 AS nav_points,
       count(*) FILTER (WHERE n.nav = 0)        AS zero_nav_points,
       count(*) FILTER (WHERE n.is_cross_verified) AS cross_verified_points
FROM mf.funds f
LEFT JOIN mf.nav_history n ON n.amfi_scheme_code = f.amfi_scheme_code
WHERE f.in_scope
GROUP BY f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
         f.scheme_category, f.is_defunct;

COMMENT ON VIEW mf.v_nav_coverage IS
    'Per-scheme NAV coverage for in-scope schemes. Expensive at full scale; run off-peak.';

-- Enrichment coverage: how much of the in-scope universe Scripbox enrichment covers.
CREATE OR REPLACE VIEW mf.v_enrichment_coverage AS
SELECT count(*)                                            AS in_scope_live,
       count(ff.amfi_scheme_code)                          AS with_facts,
       round(100.0 * count(ff.amfi_scheme_code)
             / nullif(count(*), 0), 2)                     AS facts_pct,
       count(fo.amfi_scheme_code)                          AS with_opinions,
       count(*) FILTER (WHERE ff.aum IS NOT NULL)          AS with_aum,
       count(*) FILTER (WHERE ff.expense_ratio IS NOT NULL) AS with_expense_ratio,
       count(*) FILTER (WHERE ff.inception_date IS NOT NULL) AS with_inception
FROM mf.funds f
LEFT JOIN mf.fund_facts     ff ON ff.amfi_scheme_code = f.amfi_scheme_code
LEFT JOIN mf.fund_opinions  fo ON fo.amfi_scheme_code = f.amfi_scheme_code
WHERE f.in_scope AND NOT f.is_defunct;

COMMENT ON VIEW mf.v_enrichment_coverage IS
    'Scripbox enrichment coverage across the in-scope live universe';

COMMIT;
