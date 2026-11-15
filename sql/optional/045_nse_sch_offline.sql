-- Explicit owner installation only; not part of automatic core migrations.
-- No credentials, provider calls, canonical identity/NAV writes, global default
-- ACL, privileged functions or reader ownership. Routine importer never grants.
CREATE SCHEMA mf_nse AUTHORIZATION mfdata_admin;
REVOKE ALL ON SCHEMA mf_nse FROM PUBLIC;

CREATE TABLE mf_nse.configuration (
    singleton boolean PRIMARY KEY CHECK (singleton),
    version text NOT NULL,
    definition_hash text NOT NULL
);
CREATE TABLE mf_nse.source_artifacts (
    artifact_id uuid PRIMARY KEY,
    content_hash text NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    original_bytes bytea NOT NULL,
    manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE mf_nse.import_attempts (
    operation_id uuid PRIMARY KEY,
    work_key text NOT NULL UNIQUE,
    snapshot_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('PUBLISHED','PUBLISHED_WITH_LIMITATIONS','HISTORICAL_ONLY')),
    report jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE mf_nse.snapshots (
    snapshot_id uuid PRIMARY KEY,
    artifact_id uuid NOT NULL REFERENCES mf_nse.source_artifacts,
    operation_id uuid NOT NULL UNIQUE REFERENCES mf_nse.import_attempts,
    source_date date NOT NULL,
    schema_version text NOT NULL,
    semantic_hash text NOT NULL,
    mapping_input_hash text NOT NULL,
    crosswalk jsonb NOT NULL,
    manifest jsonb NOT NULL,
    source_rows integer NOT NULL CHECK (source_rows>0),
    exposed_rows integer NOT NULL CHECK (exposed_rows>=0),
    withheld_rows integer NOT NULL CHECK (withheld_rows>=0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE mf_nse.sch_rows (
    snapshot_id uuid NOT NULL REFERENCES mf_nse.snapshots,
    row_ordinal integer NOT NULL CHECK (row_ordinal>0),
    original_cells jsonb NOT NULL CHECK (jsonb_array_length(original_cells)=43),
    normalized jsonb NOT NULL,
    field_holds jsonb NOT NULL,
    PRIMARY KEY(snapshot_id,row_ordinal)
);
CREATE TABLE mf_nse.variant_mappings (
    snapshot_id uuid NOT NULL,
    row_ordinal integer NOT NULL,
    disposition text NOT NULL,
    reason text,
    candidates jsonb NOT NULL,
    PRIMARY KEY(snapshot_id,row_ordinal),
    FOREIGN KEY(snapshot_id,row_ordinal) REFERENCES mf_nse.sch_rows
);
CREATE TABLE mf_nse.family_heads (
    family text PRIMARY KEY CHECK (family='SCH'),
    snapshot_id uuid REFERENCES mf_nse.snapshots,
    generation bigint NOT NULL CHECK(generation>=0)
);
INSERT INTO mf_nse.family_heads VALUES('SCH',NULL,0);
CREATE TABLE mf_nse.observations (
    operation_id uuid PRIMARY KEY,
    snapshot_id uuid NOT NULL REFERENCES mf_nse.snapshots,
    manifest jsonb NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('PUBLISHED','PUBLISHED_WITH_LIMITATIONS','HISTORICAL_ONLY','UNCHANGED')),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE mf_nse.current_sch (
    id uuid PRIMARY KEY,
    snapshot_id uuid NOT NULL,
    source_date date NOT NULL,
    amfi_scheme_code integer NOT NULL,
    nse_scheme_code text NOT NULL,
    isin text NOT NULL,
    isin_slot text NOT NULL,
    plan_type text NOT NULL,
    option_type text NOT NULL,
    reference_fields jsonb NOT NULL,
    limitations jsonb NOT NULL,
    eligibility text NOT NULL CHECK(eligibility='UNKNOWN')
);
REVOKE ALL ON ALL TABLES IN SCHEMA mf_nse FROM PUBLIC, mfdata_verified_reader;
GRANT USAGE ON SCHEMA mf_nse TO mfdata_ingest;
GRANT SELECT, INSERT ON mf_nse.source_artifacts, mf_nse.import_attempts,
    mf_nse.snapshots, mf_nse.sch_rows, mf_nse.variant_mappings, mf_nse.observations TO mfdata_ingest;
GRANT SELECT ON mf_nse.configuration TO mfdata_ingest;
GRANT SELECT, UPDATE ON mf_nse.family_heads TO mfdata_ingest;
GRANT SELECT, INSERT, DELETE ON mf_nse.current_sch TO mfdata_ingest;
GRANT USAGE ON SCHEMA mf TO mfdata_ingest;
GRANT SELECT ON mf.amcs, mf.funds, mf.nav_history TO mfdata_ingest;
