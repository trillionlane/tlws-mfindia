-- =============================================================================
-- MFDataIndia — authoritative source-snapshot run ledger
-- Depends on: 001 (mf.funds), 003 (mf.source_metadata)
--
-- A newer partial AMFI payload must never become the lifecycle reference for
-- the whole universe.  Only a successfully committed FULL run is eligible to
-- advance that reference; PARTIAL runs remain useful provenance and may update
-- the schemes they actually contain.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS mf.source_snapshot_runs (
    snapshot_run_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_fetch_id      bigint NOT NULL UNIQUE
                             REFERENCES mf.source_metadata (fetch_id) ON DELETE RESTRICT,
    source               text NOT NULL CHECK (source = 'AMFI'),
    snapshot_scope       text NOT NULL CHECK (snapshot_scope IN ('FULL', 'PARTIAL')),
    snapshot_date        date NOT NULL,
    latest_nav_date      date NOT NULL,
    content_hash         text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    records_in           integer NOT NULL CHECK (records_in > 0),
    records_ok           integer NOT NULL CHECK (records_ok > 0 AND records_ok <= records_in),
    records_quarantined  integer NOT NULL CHECK (records_quarantined >= 0),
    distinct_amcs        integer NOT NULL CHECK (distinct_amcs > 0),
    distinct_categories  integer NOT NULL CHECK (distinct_categories > 0),
    completed_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_snapshot_runs_latest_full
    ON mf.source_snapshot_runs (snapshot_date DESC, snapshot_run_id DESC)
    WHERE source = 'AMFI' AND snapshot_scope = 'FULL';

COMMENT ON TABLE mf.source_snapshot_runs IS
    'Successfully committed AMFI snapshot runs. Only FULL rows establish the '
    'dataset-wide lifecycle presence reference; PARTIAL rows cannot make '
    'schemes omitted from that payload appear absent.';
COMMENT ON COLUMN mf.source_snapshot_runs.snapshot_date IS
    'IST observation date of the fetch, deliberately separate from latest_nav_date';
COMMENT ON COLUMN mf.source_snapshot_runs.source_fetch_id IS
    'Exact mf.source_metadata fetch whose parsed payload committed this run';

REVOKE ALL ON mf.source_snapshot_runs FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_app') THEN
        EXECUTE 'GRANT SELECT ON mf.source_snapshot_runs TO mfdata_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_ingest') THEN
        EXECUTE 'GRANT SELECT, INSERT ON mf.source_snapshot_runs TO mfdata_ingest';
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE mf.source_snapshot_runs_snapshot_run_id_seq TO mfdata_ingest';
    END IF;
END
$$;

COMMIT;
