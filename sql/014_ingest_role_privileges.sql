-- Allow the dedicated DEV ingestion role to create and drop only its staging
-- tables in the application schema. Local/test databases without that role are
-- unchanged. Table DML grants remain provisioned separately at restore time.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mfdata_ingest') THEN
        EXECUTE 'GRANT CREATE ON SCHEMA mf TO mfdata_ingest';
    END IF;
END
$$;

COMMIT;
