-- Append-only operational control records, outside consumer-enrolled schemas.
-- Owner installs once in its existing transaction. No business/source changes.
CREATE SCHEMA mf_ingestion_ops AUTHORIZATION mfdata_admin;
REVOKE ALL ON SCHEMA mf_ingestion_ops FROM PUBLIC;
CREATE TABLE mf_ingestion_ops.work (
    work_id text PRIMARY KEY CHECK(work_id ~ '^[0-9a-f]{64}$'),
    source_key text NOT NULL,
    document jsonb NOT NULL
);
CREATE TABLE mf_ingestion_ops.claims (
    claim_id text PRIMARY KEY CHECK(claim_id ~ '^[0-9a-f]{64}$'),
    work_id text NOT NULL REFERENCES mf_ingestion_ops.work,
    source_key text NOT NULL,
    attempt integer NOT NULL CHECK(attempt>0),
    execution_id text NOT NULL,
    document jsonb NOT NULL,
    UNIQUE(work_id,attempt), UNIQUE(source_key,execution_id)
);
CREATE TABLE mf_ingestion_ops.events (
    event_id text PRIMARY KEY CHECK(event_id ~ '^[0-9a-f]{64}$'),
    claim_id text NOT NULL REFERENCES mf_ingestion_ops.claims,
    kind text NOT NULL CHECK(kind IN('PUBLICATION_INTENT','PUBLISHED','PREPARATION_FAILED',
        'ROLLBACK_CONFIRMED','OUTCOME_UNKNOWN','COMMITTED_UNVERIFIED')),
    document jsonb NOT NULL,
    UNIQUE(claim_id,kind)
);
CREATE TABLE mf_ingestion_ops.polls (
    source_key text NOT NULL,
    execution_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    document jsonb NOT NULL,
    PRIMARY KEY(source_key,execution_id)
);
CREATE INDEX work_inputs ON mf_ingestion_ops.work(source_key,(document->>'derivation_version'),(document->>'population_hash'));
CREATE INDEX recent_polls ON mf_ingestion_ops.polls(source_key,observed_at DESC);
CREATE FUNCTION mf_ingestion_ops.immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'immutable ingestion control record'; END $$;
REVOKE ALL ON FUNCTION mf_ingestion_ops.immutable() FROM PUBLIC;
CREATE FUNCTION mf_ingestion_ops.seal() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE doc jsonb; expected text; w record; c record; r jsonb;
BEGIN
    IF session_user NOT IN('mfdata_admin','mfdata_verified_generator') OR current_setting('role')<>'none' THEN
        RAISE EXCEPTION 'direct ingestion principal required'; END IF;
    doc=NEW.document;
    IF octet_length(doc::text)>262144 THEN RAISE EXCEPTION 'bounded ingestion control required'; END IF;
    IF TG_TABLE_NAME='work' THEN
        IF NOT (NEW.source_key IN('absl-large-cap','hdfc-flexi-cap','absl-expanded','sbi-expanded','kotak-expanded','uti-expanded')
            AND doc->>'format'='amc-ingestion-work-v1' AND doc->>'source_key'=NEW.source_key
            AND doc->>'work_id'=NEW.work_id
            AND doc->>'derivation_version' ~ '^[0-9a-f]{64}$'
            AND doc->>'population_hash' ~ '^[0-9a-f]{64}$'
            AND doc->>'reporting_period' ~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
            AND (doc->>'reporting_period')::date=(date_trunc('month',(doc->>'reporting_period')::date)+interval '1 month - 1 day')::date
            AND jsonb_typeof(doc->'documents')='array' AND jsonb_array_length(doc->'documents') BETWEEN 1 AND 1024
            AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(doc->'documents') x WHERE NOT (
                x->>'role' ~ '^[a-z][a-z0-9_]{0,47}$' AND x->>'sha256' ~ '^[0-9a-f]{64}$') IS TRUE)
            AND (SELECT count(DISTINCT x->>'role') FROM jsonb_array_elements(doc->'documents') x)=jsonb_array_length(doc->'documents')
        ) IS TRUE THEN RAISE EXCEPTION 'ingestion work scope differs'; END IF;
        expected=encode(sha256(convert_to(mf_review.canonical_json(doc-'work_id'),'UTF8')),'hex');
        IF expected<>NEW.work_id THEN RAISE EXCEPTION 'work seal differs'; END IF;
    ELSIF TG_TABLE_NAME='claims' THEN
        SELECT * INTO w FROM mf_ingestion_ops.work WHERE work_id=NEW.work_id;
        IF NOT (w.source_key=NEW.source_key AND doc->>'work_id'=NEW.work_id
            AND doc->>'source_key'=NEW.source_key AND doc->>'execution_id'=NEW.execution_id
            AND doc->>'attempt'=NEW.attempt::text AND doc->>'claim_id'=NEW.claim_id
            AND NEW.execution_id ~ '^tlws-mf-official-facts-refresh-dev-[a-z0-9-]{1,40}$'
            AND doc->>'format'='amc-ingestion-claim-v1'
        ) IS TRUE THEN RAISE EXCEPTION 'ingestion claim identity differs'; END IF;
        expected=encode(sha256(convert_to(mf_review.canonical_json(doc-'claim_id'),'UTF8')),'hex');
        IF expected<>NEW.claim_id THEN RAISE EXCEPTION 'claim seal differs'; END IF;
    ELSIF TG_TABLE_NAME='events' THEN
        SELECT * INTO c FROM mf_ingestion_ops.claims WHERE claim_id=NEW.claim_id;
        SELECT * INTO w FROM mf_ingestion_ops.work WHERE work_id=c.work_id;
        IF NOT (doc->>'format'='amc-ingestion-event-v1' AND doc->>'event_id'=NEW.event_id
            AND doc->>'claim_id'=NEW.claim_id AND doc->>'kind'=NEW.kind
            AND doc->>'reason' ~ '^[A-Z][A-Z0-9_]{0,63}$') IS TRUE THEN
            RAISE EXCEPTION 'ingestion event scope differs'; END IF;
        expected=encode(sha256(convert_to(mf_review.canonical_json(doc-'event_id'),'UTF8')),'hex');
        IF expected<>NEW.event_id THEN RAISE EXCEPTION 'event seal differs'; END IF;
        IF NEW.kind IN('ROLLBACK_CONFIRMED','PREPARATION_FAILED') AND EXISTS(
            SELECT 1 FROM mf_ingestion_ops.events WHERE claim_id=NEW.claim_id AND kind IN('PUBLISHED','COMMITTED_UNVERIFIED')) THEN
            RAISE EXCEPTION 'committed outcome cannot be downgraded'; END IF;
        IF NEW.kind='PUBLISHED' THEN
            IF w.source_key='absl-large-cap' THEN
                SELECT document INTO r FROM mf_fact_refresh.runs WHERE run_id=doc->'publication'->>'run_id' AND execution_id=c.execution_id;
            ELSIF w.source_key='hdfc-flexi-cap' THEN
                SELECT document INTO r FROM mf_multi_facts.runs WHERE run_id=doc->'publication'->>'run_id' AND execution_id=c.execution_id;
            ELSE
                SELECT document INTO r FROM mf_book_facts.runs WHERE run_id=doc->'publication'->>'run_id' AND execution_id=c.execution_id AND source_key=w.source_key;
            END IF;
            IF r IS NULL OR r->>'status'='failed' OR NOT EXISTS(
                SELECT 1 FROM mf_ingestion_ops.events WHERE claim_id=NEW.claim_id AND kind='PUBLICATION_INTENT') THEN
                RAISE EXCEPTION 'actual same-operation publication receipt required'; END IF;
            -- Full source/cohort/transaction verification remains the existing
            -- publisher's responsibility. This event cannot bless another run.
        END IF;
    ELSE
        IF NOT (NEW.source_key IN('absl-large-cap','hdfc-flexi-cap','absl-expanded','sbi-expanded','kotak-expanded','uti-expanded')
            AND doc->>'format'='amc-ingestion-poll-v1' AND doc->>'source_key'=NEW.source_key
            AND doc->>'execution_id'=NEW.execution_id
            AND (doc->>'observed_at')::timestamptz=NEW.observed_at
            AND NEW.execution_id ~ '^tlws-mf-official-facts-refresh-dev-[a-z0-9-]{1,40}$'
            AND doc->>'state' IN('WAITING_FOR_PUBLICATION','INCOMPLETE_SOURCE_FILES','UNCHANGED','FETCH_FAILED',
              'PARSE_OR_VALIDATION_FAILED','PUBLICATION_ROLLED_BACK','PUBLICATION_OUTCOME_UNKNOWN',
              'PUBLISHED','HELD_FOR_RECONCILIATION','NOT_DUE','ADAPTER_NOT_AVAILABLE')) IS TRUE THEN
            RAISE EXCEPTION 'poll scope differs'; END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION mf_ingestion_ops.seal() FROM PUBLIC;
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['work','claims','events','polls'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON mf_ingestion_ops.%I FOR EACH ROW EXECUTE FUNCTION mf_ingestion_ops.seal()',name||'_sealed',name);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON mf_ingestion_ops.%I FOR EACH ROW EXECUTE FUNCTION mf_ingestion_ops.immutable()',name||'_immutable',name);
    END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA mf_ingestion_ops FROM PUBLIC;
GRANT USAGE ON SCHEMA mf_ingestion_ops TO mfdata_verified_generator;
GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA mf_ingestion_ops TO mfdata_verified_generator;
