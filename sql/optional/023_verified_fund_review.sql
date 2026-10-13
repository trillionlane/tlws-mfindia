-- OPT-IN ONLY: outside both DEFAULT_MIGRATIONS and the initdb numbered-file glob.
-- Additive empty structures; no source/backfill/calculation writes or role creation.
BEGIN;
CREATE SCHEMA mf_review;
REVOKE ALL ON SCHEMA mf_review FROM PUBLIC;

CREATE TABLE mf_review.policies (
    policy_hash text PRIMARY KEY CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
    policy_id text NOT NULL,
    policy_version integer NOT NULL CHECK (policy_version > 0),
    document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(policy_id, policy_version),
    CHECK ((document->>'id'=policy_id AND (document->>'version')::integer=policy_version) IS TRUE)
);
CREATE TABLE mf_review.snapshots (
    input_hash text PRIMARY KEY CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL,
    source_revision bigint NOT NULL,
    document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'),
    captured_at timestamptz NOT NULL DEFAULT now(),
    CHECK (((document->>'code')::integer=amfi_scheme_code) IS TRUE),
    CHECK (((document->>'source_revision')::bigint=source_revision) IS TRUE),
    CHECK ((document->>'format'='verified-nav-input-v1' AND jsonb_typeof(document->'observations')='array'
        AND jsonb_typeof(document->'identity')='object' AND jsonb_typeof(document->'context')='object') IS TRUE)
);
CREATE TABLE mf_review.results (
    result_id text PRIMARY KEY CHECK (result_id ~ '^[0-9a-f]{64}$'),
    input_hash text NOT NULL REFERENCES mf_review.snapshots,
    policy_hash text NOT NULL REFERENCES mf_review.policies,
    engine_version text NOT NULL,
    engine_fingerprint text NOT NULL CHECK(engine_fingerprint ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'),
    published_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(input_hash,policy_hash,engine_fingerprint),
    CHECK ((document->>'result_id'=result_id AND document->>'format'='verified-nav-result-v1'
           AND document->>'input_hash'=input_hash AND document->>'policy_hash'=policy_hash
           AND document->>'engine_version'=engine_version
           AND document->>'engine_fingerprint'=engine_fingerprint) IS TRUE)
);
CREATE TABLE mf_review.events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    amfi_scheme_code integer NOT NULL,
    version bigint NOT NULL CHECK (version > 0),
    kind text NOT NULL CHECK (kind IN ('review','proposal')),
    status text NOT NULL CHECK(status IN ('pending_review','in_review','reviewed','needs_evidence')),
    result_id text NOT NULL REFERENCES mf_review.results,
    caller text NOT NULL CHECK(length(caller) BETWEEN 1 AND 200),
    actor text NOT NULL CHECK(length(actor) BETWEEN 1 AND 200),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 128),
    request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(amfi_scheme_code,version),
    UNIQUE(caller,idempotency_key),
    CHECK (((document->>'expected_version')::bigint=version-1 AND document->>'result_id'=result_id) IS TRUE),
    CHECK ((CASE WHEN kind='review' THEN document->>'status'=status
        ELSE document->>'execution'='not_executable' END) IS TRUE)
);
CREATE INDEX results_by_input ON mf_review.results(input_hash,published_at,result_id);
CREATE INDEX snapshots_by_fund ON mf_review.snapshots(amfi_scheme_code,captured_at,input_hash);
CREATE INDEX events_by_fund ON mf_review.events(amfi_scheme_code,version DESC);

CREATE FUNCTION mf_review.canonical_json(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,mf_review AS $$
DECLARE serialized text; BEGIN
    CASE jsonb_typeof(value)
      WHEN 'object' THEN
        SELECT '{'||COALESCE(string_agg(to_json(key)::text||':'||mf_review.canonical_json(item),',' ORDER BY key COLLATE "C"),'')||'}'
          INTO serialized FROM jsonb_each(value) AS e(key,item);
      WHEN 'array' THEN
        SELECT '['||COALESCE(string_agg(mf_review.canonical_json(item),',' ORDER BY ordinal),'')||']'
          INTO serialized FROM jsonb_array_elements(value) WITH ORDINALITY AS e(item,ordinal);
      WHEN 'number' THEN
        -- Monetary values and computed decimals are exact strings, not floats.
        IF value::text !~ '^-?(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'noninteger JSON numeric artifact'; END IF;
        serialized:=value::text;
      ELSE serialized:=value::text;
    END CASE;
    RETURN serialized;
END $$;

CREATE FUNCTION mf_review.require_seal() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf_review AS $$
DECLARE body jsonb; expected text; input mf_review.snapshots; definition jsonb; BEGIN
    body:=NEW.document;
    IF TG_TABLE_NAME='policies' THEN expected:=NEW.policy_hash;
    ELSIF TG_TABLE_NAME='snapshots' THEN expected:=NEW.input_hash;
    ELSIF TG_TABLE_NAME='results' THEN expected:=NEW.result_id; body:=body-'result_id';
        SELECT * INTO input FROM mf_review.snapshots WHERE input_hash=NEW.input_hash;
        SELECT document INTO definition FROM mf_review.policies WHERE policy_hash=NEW.policy_hash;
        IF NOT ((body->>'code')::integer=input.amfi_scheme_code
            AND (body->>'source_revision')::bigint=input.source_revision
            AND body->'dataset_version'=input.document->'dataset_version'
            AND body->'context'=input.document->'context'
            AND (body->>'points')::integer=jsonb_array_length(input.document->'observations')
            AND (body->>'first_date') IS NOT DISTINCT FROM (input.document->'observations'->0->>'date')
            AND (body->>'as_of') IS NOT DISTINCT FROM (input.document->'observations'->-1->>'date')
            AND body->>'engine_version'=definition->>'engine_version'
            AND body->'engine'->>'version'=body->>'engine_version') IS TRUE THEN
            RAISE EXCEPTION 'result lineage does not match retained inputs and policy';
        END IF;
        IF NEW.engine_fingerprint IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(body->'engine'),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'engine metadata does not match fingerprint';
        END IF;
    ELSIF TG_TABLE_NAME='events' THEN expected:=NEW.request_hash;
        body:=jsonb_build_object('code',NEW.amfi_scheme_code,'kind',NEW.kind,'actor',NEW.actor,
             'request',CASE WHEN NEW.kind='proposal' THEN body-'execution' ELSE body END);
    ELSE RAISE EXCEPTION 'unsupported sealed artifact'; END IF;
    IF expected IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(body),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'artifact content does not match canonical hash';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER policy_sealed BEFORE INSERT ON mf_review.policies FOR EACH ROW EXECUTE FUNCTION mf_review.require_seal();
CREATE TRIGGER snapshot_sealed BEFORE INSERT ON mf_review.snapshots FOR EACH ROW EXECUTE FUNCTION mf_review.require_seal();
CREATE TRIGGER result_sealed BEFORE INSERT ON mf_review.results FOR EACH ROW EXECUTE FUNCTION mf_review.require_seal();
CREATE TRIGGER event_sealed BEFORE INSERT ON mf_review.events FOR EACH ROW EXECUTE FUNCTION mf_review.require_seal();

-- Admin runtime has EXECUTE only, never direct ledger INSERT. Even a direct
-- call must respect the linear version, current reviewed dependencies and replay.
CREATE FUNCTION mf_review.append_event(p_code integer,p_kind text,p_caller text,p_actor text,
    p_key text,p_hash text,p_document jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf_review AS $$
DECLARE previous mf_review.events; replay mf_review.events; input mf_review.snapshots;
        result mf_review.results; next_status text; added mf_review.events;
BEGIN
    -- A repeatable-read snapshot acquired before a competing refresh commits
    -- cannot establish freshness merely by obtaining the lock afterward.
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'review mutation requires a fresh READ COMMITTED snapshot';
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'NAV operation busy';
    END IF;
    SELECT * INTO replay FROM mf_review.events WHERE caller=p_caller AND idempotency_key=p_key;
    IF FOUND THEN
        IF replay.request_hash<>p_hash OR replay.actor<>p_actor OR replay.amfi_scheme_code<>p_code
           OR replay.kind<>p_kind OR replay.document IS DISTINCT FROM p_document THEN
            RAISE EXCEPTION 'idempotency conflict'; END IF;
        RETURN jsonb_build_object('version',replay.version,'kind',replay.kind,'status',replay.status,'result_id',replay.result_id);
    END IF;
    SELECT * INTO result FROM mf_review.results WHERE result_id=p_document->>'result_id';
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown result'; END IF;
    SELECT * INTO input FROM mf_review.snapshots WHERE input_hash=result.input_hash;
    IF input.amfi_scheme_code<>p_code OR input.input_hash IS DISTINCT FROM p_document->>'expected_input_hash'
       OR input.source_revision IS DISTINCT FROM COALESCE((SELECT revision FROM mf.nav_source_revisions WHERE amfi_scheme_code=p_code),0)
       OR (input.document->>'dataset_version')::bigint IS DISTINCT FROM
            (SELECT dataset_version FROM mf.dataset_summary WHERE singleton) THEN
        RAISE EXCEPTION 'reviewed dependencies changed';
    END IF;
    IF input.document->'identity' IS DISTINCT FROM (SELECT jsonb_build_object(
        'amfi_scheme_code',f.amfi_scheme_code,'scheme_name',f.scheme_name,'amc_id',f.amc_id,
        'scheme_type',f.scheme_type,'scheme_category',f.scheme_category,'plan_type',f.plan_type,
        'option_type',f.option_type,'isin_growth_or_div_payout',f.isin_growth_or_div_payout,
        'isin_div_reinvest',f.isin_div_reinvest,'is_defunct',f.is_defunct,'last_seen_in_source',f.last_seen_in_source)
        FROM mf.funds f WHERE f.amfi_scheme_code=p_code)
       OR input.document->'facts' IS DISTINCT FROM COALESCE((SELECT jsonb_build_object(
        'aum',ff.aum::text,'expense_ratio',ff.expense_ratio::text,'benchmark',ff.benchmark,
        'benchmark_name',ff.benchmark_name,'fund_manager_name',ff.fund_manager_name,'inception_date',ff.inception_date::text)
        FROM mf.fund_facts ff WHERE ff.amfi_scheme_code=p_code),'null'::jsonb)
       OR (input.document->'context'->'nav_quality'->>'assessed_at')::timestamptz IS DISTINCT FROM
        (SELECT assessed_at FROM mf.nav_quality_assessments WHERE amfi_scheme_code=p_code) THEN
        RAISE EXCEPTION 'reviewed identity, facts or assessment changed';
    END IF;
    IF jsonb_typeof(p_document->'expected_version') IS DISTINCT FROM 'number'
       OR p_document->>'expected_version' !~ '^[0-9]+$'
       OR jsonb_typeof(p_document->'reason') IS DISTINCT FROM 'string'
       OR length(btrim(p_document->>'reason')) NOT BETWEEN 1 AND 4000
       OR jsonb_typeof(p_document->'evidence') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'invalid review request';
    END IF;
    SELECT * INTO previous FROM mf_review.events WHERE amfi_scheme_code=p_code ORDER BY version DESC LIMIT 1;
    IF p_document->>'expected_version' IS NULL OR
       (p_document->>'expected_version')::bigint<>COALESCE(previous.version,0) THEN
        RAISE EXCEPTION 'review version conflict';
    END IF;
    IF p_kind='review' THEN
        next_status:=p_document->>'status';
        IF p_document->>'disposition' NOT IN('not_determined','consistent_with_evidence','discrepancy_candidate','insufficient_evidence')
           OR p_document->>'disposition' IS NULL
           OR (next_status='reviewed' AND (jsonb_array_length(p_document->'evidence')=0
               OR p_document->>'disposition'='not_determined')) THEN
            RAISE EXCEPTION 'reviewed status requires evidence and disposition';
        END IF;
    ELSIF p_kind='proposal' AND p_document->>'execution'='not_executable' THEN
        -- Proposal lineage belongs to its own result, not to a new review.
        -- Only actual review decisions define human status and its dependencies.
        SELECT status INTO next_status FROM mf_review.events
          WHERE amfi_scheme_code=p_code AND kind='review' ORDER BY version DESC LIMIT 1;
        next_status:=COALESCE(next_status,'pending_review');
    ELSE RAISE EXCEPTION 'unsupported review action'; END IF;
    INSERT INTO mf_review.events(amfi_scheme_code,version,kind,status,result_id,caller,actor,idempotency_key,request_hash,document)
      VALUES(p_code,COALESCE(previous.version,0)+1,p_kind,next_status,result.result_id,p_caller,p_actor,p_key,p_hash,p_document)
      RETURNING * INTO added;
    RETURN jsonb_build_object('version',added.version,'kind',added.kind,'status',added.status,'result_id',added.result_id);
END $$;

CREATE FUNCTION mf_review.reject_rewrite() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'verified review records are append-only';
END $$;
CREATE TRIGGER policy_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_review.policies
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER snapshot_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_review.snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER result_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_review.results
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();
CREATE TRIGGER event_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_review.events
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();

REVOKE ALL ON ALL TABLES IN SCHEMA mf_review FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA mf_review FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_review FROM PUBLIC;
-- Revoke owner default grants too; PUBLIC revocation alone is insufficient.
DO $$ DECLARE role_name text; BEGIN
    FOREACH role_name IN ARRAY ARRAY['mfdata_app','mfdata_ingest','mfdata_audit',
        'mfdata_association_writer','mfdata_remediation_review','mfdata_nav_correction_writer'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA mf_review FROM %I',role_name);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_review FROM %I',role_name);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA mf_review FROM %I',role_name);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_review FROM %I',role_name);
        END IF;
    END LOOP;
END $$;
COMMIT;
