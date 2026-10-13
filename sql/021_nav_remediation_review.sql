-- DATA-02: append-only investigation records. No source-table privileges,
-- correction executor, API behavior change, or signal suppression.
BEGIN;

CREATE TABLE mf.nav_remediation_snapshots (
    snapshot_sha256 text PRIMARY KEY CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    case_id text NOT NULL CHECK (case_id ~ '^[0-9a-f]{64}$'),
    dataset_version bigint NOT NULL CHECK (dataset_version > 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((payload->>'sha256' = snapshot_sha256) IS TRUE),
    CHECK ((payload->>'case_id' = case_id) IS TRUE),
    CHECK (((payload->>'dataset_version')::bigint = dataset_version) IS TRUE)
);

CREATE TABLE mf.nav_remediation_decisions (
    decision_sha256 text PRIMARY KEY CHECK (decision_sha256 ~ '^[0-9a-f]{64}$'),
    snapshot_sha256 text NOT NULL REFERENCES mf.nav_remediation_snapshots(snapshot_sha256),
    supersedes_sha256 text UNIQUE,
    disposition text NOT NULL CHECK (disposition IN (
        'verified_legitimate', 'confirmed_nav_discrepancy',
        'identity_review_required', 'insufficient_evidence'
    )),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((payload->>'sha256' = decision_sha256) IS TRUE),
    CHECK ((payload->>'case_snapshot_sha256' = snapshot_sha256) IS TRUE),
    CHECK ((payload->>'disposition' = disposition) IS TRUE),
    CHECK (payload ? 'supersedes_sha256'),
    CHECK ((payload->>'supersedes_sha256') IS NOT DISTINCT FROM supersedes_sha256),
    UNIQUE (decision_sha256, snapshot_sha256),
    FOREIGN KEY (supersedes_sha256, snapshot_sha256)
        REFERENCES mf.nav_remediation_decisions(decision_sha256, snapshot_sha256)
);
-- A single initial decision and linear supersession chain per snapshot.
CREATE UNIQUE INDEX nav_remediation_initial_decision
    ON mf.nav_remediation_decisions(snapshot_sha256)
    WHERE supersedes_sha256 IS NULL;

CREATE TABLE mf.nav_remediation_proposals (
    proposal_sha256 text PRIMARY KEY CHECK (proposal_sha256 ~ '^[0-9a-f]{64}$'),
    decision_sha256 text NOT NULL REFERENCES mf.nav_remediation_decisions(decision_sha256),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((payload->>'sha256' = proposal_sha256) IS TRUE),
    CHECK ((payload->>'decision_sha256' = decision_sha256) IS TRUE)
);

CREATE FUNCTION mf.reject_remediation_rewrite() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'remediation evidence is append-only; append a superseding decision';
END
$$;
CREATE TRIGGER nav_remediation_snapshots_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON mf.nav_remediation_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION mf.reject_remediation_rewrite();
CREATE TRIGGER nav_remediation_decisions_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON mf.nav_remediation_decisions
    FOR EACH STATEMENT EXECUTE FUNCTION mf.reject_remediation_rewrite();
CREATE TRIGGER nav_remediation_proposals_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON mf.nav_remediation_proposals
    FOR EACH STATEMENT EXECUTE FUNCTION mf.reject_remediation_rewrite();

REVOKE ALL ON mf.nav_remediation_snapshots, mf.nav_remediation_decisions,
    mf.nav_remediation_proposals FROM PUBLIC;
REVOKE ALL ON FUNCTION mf.reject_remediation_rewrite() FROM PUBLIC;
-- Optional review principal is provisioned separately; no grants to the API,
-- ingestion, association writer, or audit principal are introduced here.
DO $$
DECLARE membership record;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_remediation_review') THEN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mfdata_remediation_review' AND rolsuper)
           OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                      JOIN pg_roles r ON r.oid=c.relowner
                      WHERE n.nspname='mf' AND r.rolname='mfdata_remediation_review') THEN
            RAISE EXCEPTION 'review principal must not be a superuser or own source/ledger relations';
        END IF;
        -- A late-created Cloud SQL built-in principal must not retain default
        -- elevated membership or grants from an earlier provisioning attempt.
        FOR membership IN
            SELECT parent.rolname FROM pg_auth_members m
            JOIN pg_roles parent ON parent.oid=m.roleid
            JOIN pg_roles child ON child.oid=m.member
            WHERE child.rolname='mfdata_remediation_review'
        LOOP
            EXECUTE format('REVOKE %I FROM mfdata_remediation_review', membership.rolname);
        END LOOP;
        EXECUTE 'ALTER ROLE mfdata_remediation_review NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS';
        EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA mf FROM mfdata_remediation_review';
        EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA mf FROM mfdata_remediation_review';
        EXECUTE 'REVOKE CREATE ON SCHEMA mf, public FROM mfdata_remediation_review';
        EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM mfdata_remediation_review', current_database());
        EXECUTE format('GRANT CONNECT ON DATABASE %I TO mfdata_remediation_review', current_database());
        EXECUTE 'GRANT USAGE ON SCHEMA mf TO mfdata_remediation_review';
        EXECUTE 'GRANT SELECT, INSERT ON mf.nav_remediation_snapshots, '
                'mf.nav_remediation_decisions, mf.nav_remediation_proposals '
                'TO mfdata_remediation_review';
        IF EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='mf' AND c.relkind IN ('r','p','v','m')
              AND c.relname NOT IN ('nav_remediation_snapshots', 'nav_remediation_decisions', 'nav_remediation_proposals')
              AND has_table_privilege('mfdata_remediation_review', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')
        ) THEN
            RAISE EXCEPTION 'review principal retains effective source-write privileges';
        END IF;
    END IF;
END
$$;
COMMIT;
