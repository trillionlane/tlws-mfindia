-- Opt-in, with the NAV refresh v2 rollout. Existing public tables keep their
-- shape, identifiers, foreign keys and slugs.
BEGIN;

DO $$
BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425, 1) THEN
        RAISE EXCEPTION 'another governed NAV operation is running';
    END IF;
    IF EXISTS (
        SELECT 1 FROM mf.fund_variants v
        LEFT JOIN mf.fund_family f USING(group_key) WHERE f.tlws_mf_id IS NULL
    ) OR EXISTS (
        SELECT 1 FROM mf.fund_family f
        WHERE NOT EXISTS (SELECT 1 FROM mf.fund_variants v WHERE v.group_key=f.group_key)
    ) THEN
        RAISE EXCEPTION 'existing family membership must be consistent before installation';
    END IF;
END
$$;

CREATE SCHEMA mf_identity;
REVOKE ALL ON SCHEMA mf_identity FROM PUBLIC;

CREATE TABLE mf_identity.variant_families (
    amfi_scheme_code integer PRIMARY KEY REFERENCES mf.fund_variants (amfi_scheme_code),
    family_id text NOT NULL REFERENCES mf.fund_family (tlws_mf_id)
);

INSERT INTO mf_identity.variant_families (amfi_scheme_code, family_id)
SELECT v.amfi_scheme_code, f.tlws_mf_id
FROM mf.fund_variants v JOIN mf.fund_family f USING(group_key);

CREATE TABLE mf_identity.family_renames (
    family_id text NOT NULL REFERENCES mf.fund_family (tlws_mf_id),
    former_key text NOT NULL,
    current_key text NOT NULL,
    former_name text NOT NULL,
    former_slug text NOT NULL,
    source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    source_date date NOT NULL,
    scheme_codes jsonb NOT NULL CHECK (jsonb_typeof(scheme_codes)='array'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (former_key <> current_key),
    PRIMARY KEY (family_id, source_date, source_hash, former_key, current_key)
);

CREATE TABLE mf_identity.catalogue_holds (
    source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    source_date date NOT NULL,
    reasons jsonb NOT NULL CHECK (jsonb_typeof(reasons)='array'),
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_date, source_hash)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mfdata_ingest') THEN
        GRANT USAGE ON SCHEMA mf_identity TO mfdata_ingest;
        GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA mf_identity TO mfdata_ingest;
    END IF;
END
$$;

COMMIT;
