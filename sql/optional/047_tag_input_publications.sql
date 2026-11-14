-- Ordered, append-only factual-tag input receipts. Depends on optional 046.
BEGIN;

CREATE TABLE mf_identity.tag_input_publications (
    publication_sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    publication_id text NOT NULL UNIQUE CHECK (publication_id ~ '^[0-9a-f]{64}$'),
    predecessor_publication_id text NULL
        REFERENCES mf_identity.tag_input_publications(publication_id) ON DELETE RESTRICT,
    source_kind text NOT NULL CHECK (source_kind IN ('full-catalogue','fund-family-rebuild')),
    source_snapshot_run_id bigint NOT NULL
        REFERENCES mf.source_snapshot_runs(snapshot_run_id) ON DELETE RESTRICT,
    catalogue_observed_on date NOT NULL,
    observed_on date NOT NULL,
    catalogue_census_hash text NOT NULL CHECK (catalogue_census_hash ~ '^[0-9a-f]{64}$'),
    family_projection_hash text NOT NULL CHECK (family_projection_hash ~ '^[0-9a-f]{64}$'),
    receipt_content_hash text NOT NULL CHECK (receipt_content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (publication_id = receipt_content_hash),
    CHECK (observed_on >= catalogue_observed_on)
);

REVOKE ALL ON mf_identity.tag_input_publications FROM PUBLIC;
REVOKE ALL ON SEQUENCE mf_identity.tag_input_publications_publication_sequence_seq FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mfdata_ingest') THEN
        GRANT SELECT, INSERT ON mf_identity.tag_input_publications TO mfdata_ingest;
        GRANT USAGE, SELECT ON SEQUENCE
            mf_identity.tag_input_publications_publication_sequence_seq TO mfdata_ingest;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mfdata_tag_writer') THEN
        GRANT USAGE ON SCHEMA mf_identity TO mfdata_tag_writer;
        GRANT SELECT(publication_sequence, publication_id, predecessor_publication_id, source_kind,
            source_snapshot_run_id, catalogue_observed_on, observed_on,
            catalogue_census_hash, family_projection_hash, receipt_content_hash)
            ON mf_identity.tag_input_publications TO mfdata_tag_writer;
    END IF;
END
$$;

COMMIT;
