-- =============================================================================
-- MFDataIndia — governed, typed fund-family association tags
-- Depends on: 013 (mf.fund_family)
--
-- These records are intentionally separate from mf.fund_family.tags.  The
-- latter is generated from AMFI/factsheet facets and is replaced by the family
-- builder; partner-authored associations must survive those rebuilds.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS mf.fund_family_association_state (
    tlws_mf_id  text PRIMARY KEY
                    REFERENCES mf.fund_family (tlws_mf_id) ON DELETE RESTRICT,
    version     bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mf.fund_family_association_tags (
    tlws_mf_id  text NOT NULL
                    REFERENCES mf.fund_family (tlws_mf_id) ON DELETE RESTRICT,
    value       text NOT NULL CHECK (
                    length(value) BETWEEN 1 AND 160
                    AND value ~ '^[a-z0-9]+(-[a-z0-9]+)*$'
                ),
    tag_type    text NOT NULL CHECK (tag_type = 'scheme_alias'),
    source      text NOT NULL CHECK (source = 'trillion-insights'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tlws_mf_id, tag_type, value, source)
);

CREATE INDEX IF NOT EXISTS ix_family_association_tags_lookup
    ON mf.fund_family_association_tags (tag_type, value);

CREATE TABLE IF NOT EXISTS mf.association_tag_idempotency (
    source           text NOT NULL CHECK (source = 'trillion-insights'),
    idempotency_key  text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    request_hash     text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    response_status  integer NOT NULL CHECK (response_status IN (200, 201)),
    response_body    jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, idempotency_key)
);

COMMENT ON TABLE mf.fund_family_association_tags IS
    'Typed, source-attributed partner associations; never overwritten by the generated family-tag rebuild';
COMMENT ON TABLE mf.association_tag_idempotency IS
    'Durable replay ledger for the private association-tag write contract';

REVOKE ALL ON mf.fund_family_association_state,
              mf.fund_family_association_tags,
              mf.association_tag_idempotency FROM PUBLIC;

-- Restore-time default privileges intentionally make the ingest role broad.
-- These partner-authored records are outside ingest ownership, so narrow them
-- again even when the role existed before this migration was applied.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_app') THEN
        EXECUTE 'REVOKE ALL ON mf.fund_family_association_state, mf.fund_family_association_tags, mf.association_tag_idempotency FROM mfdata_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_ingest') THEN
        EXECUTE 'REVOKE ALL ON mf.fund_family_association_state, mf.fund_family_association_tags, mf.association_tag_idempotency FROM mfdata_ingest';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_association_writer') THEN
        EXECUTE format(
            'GRANT CONNECT ON DATABASE %I TO mfdata_association_writer',
            current_database()
        );
        EXECUTE 'GRANT USAGE ON SCHEMA mf TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT ON mf.fund_family TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT, INSERT ON mf.fund_family_association_state TO mfdata_association_writer';
        EXECUTE 'GRANT UPDATE (version, updated_at) ON mf.fund_family_association_state TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT, INSERT ON mf.fund_family_association_tags TO mfdata_association_writer';
        EXECUTE 'GRANT SELECT, INSERT ON mf.association_tag_idempotency TO mfdata_association_writer';
    END IF;
END
$$;

COMMIT;
