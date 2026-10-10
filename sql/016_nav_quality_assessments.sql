-- =============================================================================
-- MFDataIndia — durable NAV-quality assessments
-- Depends on: 001 (mf.funds), 002 (mf.nav_history), 015 (mf.dataset_summary)
--
-- One compact, additive row per assessed scheme. This table is the ONLY write
-- target of the NAV-integrity audit (scripts/audit_nav_integrity.py); the
-- audit never mutates mf.nav_history.
--
-- Signals are DETECTION signals, not proof that a stored NAV is wrong:
--   constant_nav_series              one distinct NAV across the full history
--   duplicate_variant_series         identical series within a guarded family
--                                    identity (AMC + scheme type + category +
--                                    group_key) — an IDCW option that never
--                                    diverges from its Growth twin
--   terminal_face_value_reset_candidate
--                                    the terminal observation is exactly
--                                    10.0000 (FMP redemption at face value)
--                                    after a materially higher prior NAV
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS mf.nav_quality_assessments (
    amfi_scheme_code          integer PRIMARY KEY
                                REFERENCES mf.funds (amfi_scheme_code)
                                ON DELETE CASCADE,
    methodology_version       text NOT NULL CHECK (methodology_version ~ '^[0-9]+$'),
    assessed_dataset_version  bigint NOT NULL CHECK (assessed_dataset_version > 0),
    assessed_at               timestamptz NOT NULL,
    observation_count         integer NOT NULL CHECK (observation_count >= 1),
    distinct_nav_count        integer NOT NULL CHECK (distinct_nav_count >= 1),
    first_nav_date            date NOT NULL,
    last_nav_date             date NOT NULL,
    -- Closed set: the CHECK admits only these three signals (empty = no
    -- signal; the audit writes rows for signaled schemes only, so this is
    -- enforced at the application layer as well).
    signals                   text[] NOT NULL DEFAULT '{}'
                                CHECK (signals <@ ARRAY['constant_nav_series',
                                                         'duplicate_variant_series',
                                                         'terminal_face_value_reset_candidate']),
    -- Internal diagnosis only (e.g. the constant value, the duplicate's
    -- family identity and counterpart codes, the terminal pair). Never a
    -- customer-facing verdict.
    evidence                  jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE mf.nav_quality_assessments IS
    'Durable NAV-quality detection signals per scheme; written only by the '
    'governed audit, never by the API request path or the daily refresh';
COMMENT ON COLUMN mf.nav_quality_assessments.signals IS
    'Closed set of detection signals: constant_nav_series, duplicate_variant_series, '
    'terminal_face_value_reset_candidate — detection, not proof of wrong NAV';
COMMENT ON COLUMN mf.nav_quality_assessments.assessed_dataset_version IS
    'mf.dataset_summary.dataset_version the full-history scan saw; the API serves '
    'assessment_status=stale when this is older than the current version';
COMMENT ON COLUMN mf.nav_quality_assessments.evidence IS
    'Internal diagnosis (values, family identity, counterpart codes); not a verdict';

CREATE INDEX IF NOT EXISTS ix_navq_version
    ON mf.nav_quality_assessments (assessed_dataset_version);

REVOKE ALL ON mf.nav_quality_assessments FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_app') THEN
        EXECUTE 'GRANT SELECT ON mf.nav_quality_assessments TO mfdata_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_ingest') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON mf.nav_quality_assessments TO mfdata_ingest';
    END IF;
END
$$;

COMMIT;
