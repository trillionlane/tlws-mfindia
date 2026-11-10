-- Additive optional TAG-01 owner installation. No role provisioning or source writes.
CREATE SCHEMA mf_tagging AUTHORIZATION mfdata_admin;
REVOKE ALL ON SCHEMA mf_tagging FROM PUBLIC;
CREATE TABLE mf_tagging.definitions (
    tag_id text PRIMARY KEY CHECK(tag_id ~ '^[0-9a-f]{64}$'),
    dimension text NOT NULL, value text NOT NULL,
    UNIQUE(dimension,value)
);
CREATE TABLE mf_tagging.snapshots (
    snapshot_id text PRIMARY KEY CHECK(snapshot_id ~ '^[0-9a-f]{64}$'),
    source_hash text NOT NULL, policy_hash text NOT NULL,
    funds integer NOT NULL CHECK(funds>0), report jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mf_tagging.observations (
    snapshot_id text NOT NULL REFERENCES mf_tagging.snapshots,
    code integer NOT NULL CHECK(code>0), document jsonb NOT NULL,
    PRIMARY KEY(snapshot_id,code)
);
CREATE TABLE mf_tagging.assessments (
    snapshot_id text NOT NULL REFERENCES mf_tagging.snapshots,
    code integer NOT NULL CHECK(code>0), dimension text NOT NULL,
    document jsonb NOT NULL, PRIMARY KEY(snapshot_id,code,dimension)
);
CREATE TABLE mf_tagging.head (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    snapshot_id text NOT NULL REFERENCES mf_tagging.snapshots
);
-- Physical safe read projection: internal snapshot/control tables are not enrolled.
CREATE TABLE mf_tagging.current (
    code integer NOT NULL, dimension text NOT NULL,
    document jsonb NOT NULL, PRIMARY KEY(code,dimension)
);
CREATE INDEX fund_tag_value ON mf_tagging.current ((document->>'tag_id'),code);
REVOKE ALL ON ALL TABLES IN SCHEMA mf_tagging FROM PUBLIC;
-- Current-role defaults must not leak controls to the existing ingestion role.
REVOKE ALL ON ALL TABLES IN SCHEMA mf_tagging FROM mfdata_ingest,mfdata_verified_reader,mfdata_tag_writer;
GRANT USAGE ON SCHEMA mf_tagging TO mfdata_tag_writer,mfdata_verified_reader;
GRANT SELECT,INSERT ON mf_tagging.definitions,mf_tagging.snapshots,mf_tagging.observations,mf_tagging.assessments TO mfdata_tag_writer;
GRANT SELECT,INSERT,UPDATE ON mf_tagging.head TO mfdata_tag_writer;
GRANT SELECT,INSERT,DELETE ON mf_tagging.current TO mfdata_tag_writer;
GRANT SELECT ON mf_tagging.current,mf_tagging.definitions TO mfdata_verified_reader;
GRANT USAGE ON SCHEMA mf TO mfdata_tag_writer;
GRANT SELECT(amfi_scheme_code,amc_id,scheme_name,scheme_type,scheme_category,category_source,
    plan_type,plan_source,option_type,is_etf,in_scope,last_seen_in_source) ON mf.funds TO mfdata_tag_writer;
GRANT SELECT(amc_id,amfi_amc_name) ON mf.amcs TO mfdata_tag_writer;
GRANT SELECT(amfi_scheme_code,group_key) ON mf.fund_variants TO mfdata_tag_writer;
GRANT SELECT(group_key,tlws_mf_id) ON mf.fund_family TO mfdata_tag_writer;
