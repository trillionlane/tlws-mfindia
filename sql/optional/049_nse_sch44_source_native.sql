-- Source-only direct owner, with the versioned 045/048 composition. Does not change SCH FULL
-- publication, canonical mappings, existing heads, grants, or default ACLs.
CREATE TABLE mf_nse.source_configuration (
 singleton boolean PRIMARY KEY CHECK(singleton), definition_hash text NOT NULL
);
CREATE TABLE mf_nse.source_head (
 singleton boolean PRIMARY KEY CHECK(singleton),
 snapshot_id uuid REFERENCES mf_nse.snapshots, generation bigint NOT NULL CHECK(generation>=0)
);
INSERT INTO mf_nse.source_head VALUES(true,NULL,0);
GRANT SELECT ON mf_nse.source_configuration TO mfdata_nse_ingest;
GRANT SELECT, UPDATE ON mf_nse.source_head TO mfdata_nse_ingest;
CREATE VIEW mf_nse.source_routes WITH (security_barrier=true) AS
 SELECT s.snapshot_id,r.row_ordinal+1 AS source_line,r.original_cells,r.field_holds,
   m.disposition,m.reason,m.candidates,
   s.source_date AS generation_date,s.schema_version,s.mapping_input_hash,
   s.manifest->>'catalogue_version' AS catalogue_version,
   s.manifest->>'filename' AS filename,
   s.manifest->>'retrieved_at_utc' AS retrieved_at_utc,
   a.content_hash AS source_sha256,h.generation
 FROM mf_nse.source_head h JOIN mf_nse.snapshots s USING(snapshot_id)
 JOIN mf_nse.sch_rows r USING(snapshot_id)
 JOIN mf_nse.variant_mappings m USING(snapshot_id,row_ordinal)
 JOIN mf_nse.source_artifacts a USING(artifact_id);
GRANT SELECT ON mf_nse.source_routes TO mfdata_nse_ingest;
REVOKE ALL ON mf_nse.source_configuration,mf_nse.source_head,mf_nse.source_routes FROM PUBLIC;
-- Routine publication cannot grant or enroll. The explicit installer registers
-- only the safe source read model through the existing enrollment lifecycle.
