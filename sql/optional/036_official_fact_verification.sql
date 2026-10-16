-- Optional append-only human verification; never source row corrections.
BEGIN;
CREATE SCHEMA mf_fact_review;
REVOKE ALL ON SCHEMA mf_fact_review FROM PUBLIC;
CREATE TABLE mf_fact_review.events (
    event_id text PRIMARY KEY CHECK(event_id ~ '^[0-9a-f]{64}$'),
    amfi_scheme_code integer NOT NULL,
    version bigint NOT NULL CHECK(version>0),
    dependency_hash text NOT NULL CHECK(dependency_hash ~ '^[0-9a-f]{64}$'),
    caller text NOT NULL CHECK(length(caller) BETWEEN 1 AND 200),
    actor text NOT NULL CHECK(length(actor) BETWEEN 1 AND 200),
    idempotency_key text NOT NULL CHECK(idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'),
    request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
    document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=131072),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(amfi_scheme_code,version),UNIQUE(caller,idempotency_key),
    CHECK((document->>'event_id'=event_id AND (document->>'code')::integer=amfi_scheme_code
        AND (document->>'version')::bigint=version AND document->>'dependency_hash'=dependency_hash
        AND document->>'caller'=caller AND document->>'actor'=actor AND document->>'request_hash'=request_hash) IS TRUE)
);
CREATE TRIGGER fact_review_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON mf_fact_review.events
    FOR EACH STATEMENT EXECUTE FUNCTION mf_review.reject_rewrite();

CREATE FUNCTION mf_fact_review.dependencies(p_code integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE identity jsonb; configuration jsonb; fields jsonb; BEGIN
    SELECT jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
        'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
        'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
    INTO identity FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE f.amfi_scheme_code=p_code;
    IF identity IS NULL THEN RAISE EXCEPTION 'fund not found'; END IF;
    SELECT p.document INTO configuration FROM mf_live_facts.configuration c
        JOIN mf_review.policies p USING(policy_hash) ORDER BY c.generation DESC LIMIT 1;
    SELECT jsonb_object_agg(k.key,jsonb_build_object(
        'value',s.value,'selected_as_of',s.as_of,
        'selected_source_ids',COALESCE((SELECT jsonb_agg(x->>'snapshot_id' ORDER BY x->>'snapshot_id') FROM jsonb_array_elements(s.sources) x),'[]'::jsonb),
        'head_as_of',h.as_of,'head_usable',COALESCE(h.usable,false),
        'head_distinct_values',h.distinct_values,
        'head_source_ids',COALESCE((SELECT jsonb_agg(x->>'snapshot_id' ORDER BY x->>'snapshot_id') FROM jsonb_array_elements(h.sources) x),'[]'::jsonb)))
    INTO fields FROM jsonb_object_keys(configuration->'supported_fields') AS k(key)
        LEFT JOIN mf_live_facts.field_heads h ON h.code=p_code AND h.field=k.key
        LEFT JOIN mf_live_facts.selected_fields s ON s.code=p_code AND s.field=k.key;
    RETURN jsonb_build_object('format','official-facts-review-dependencies-v1','code',p_code,
        'identity',identity,'policy_hash',encode(sha256(convert_to(mf_review.canonical_json(configuration),'UTF8')),'hex'),
        'fields',fields);
END $$;

CREATE FUNCTION mf_fact_review.record_review(code integer,caller_name text,actor_name text,key text,request jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE dependency jsonb; checksum text; requested_hash text; previous mf_fact_review.events;
    prior_version bigint; field text; evidence text; seen integer; event jsonb; identifier text; BEGIN
    IF session_user NOT IN('postgres','mfdata_admin','mfdata_verified_admin') THEN
        RAISE EXCEPTION 'dedicated signed-admin runtime required'; END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'data publication busy'; END IF;
    IF length(caller_name) NOT BETWEEN 1 AND 200 OR length(actor_name) NOT BETWEEN 1 AND 200
        OR caller_name ~ '[[:cntrl:]]' OR actor_name ~ '[[:cntrl:]]'
        OR key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
        OR NOT (jsonb_typeof(request)='object' AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(request) k)=
            ARRAY['attestation','decision','evidence_snapshot_ids','expected_dependency_hash','expected_version','fields','reason']::text[]
            AND jsonb_typeof(request->'expected_version')='number' AND request->>'expected_version' ~ '^(0|[1-9][0-9]{0,17})$'
            AND request->>'expected_dependency_hash' ~ '^[0-9a-f]{64}$'
            AND request->>'decision' IN('verified','needs_evidence')
            AND request->>'attestation'='human_reviewed_original'
            AND jsonb_typeof(request->'reason')='string' AND length(btrim(request->>'reason')) BETWEEN 8 AND 1000
            AND request->>'reason' !~ '[[:cntrl:]]'
            AND jsonb_typeof(request->'fields')='array' AND jsonb_array_length(request->'fields') BETWEEN 1 AND 5
            AND jsonb_typeof(request->'evidence_snapshot_ids')='array' AND jsonb_array_length(request->'evidence_snapshot_ids') BETWEEN 1 AND 20) IS TRUE THEN
        RAISE EXCEPTION 'bounded human review request required'; END IF;
    requested_hash=encode(sha256(convert_to(mf_review.canonical_json(jsonb_build_object(
        'code',code,'caller',caller_name,'actor',actor_name,'request',request)),'UTF8')),'hex');
    SELECT * INTO previous FROM mf_fact_review.events e WHERE e.caller=caller_name AND e.idempotency_key=key;
    IF FOUND THEN
        IF previous.request_hash<>requested_hash OR previous.amfi_scheme_code<>code OR previous.actor<>actor_name THEN
            RAISE EXCEPTION 'idempotency identity/request differs'; END IF;
        RETURN previous.document;
    END IF;
    dependency=mf_fact_review.dependencies(code);
    checksum=encode(sha256(convert_to(mf_review.canonical_json(dependency),'UTF8')),'hex');
    SELECT COALESCE(max(e.version),0) INTO prior_version FROM mf_fact_review.events e WHERE e.amfi_scheme_code=code;
    IF prior_version<>(request->>'expected_version')::bigint OR checksum<>request->>'expected_dependency_hash' THEN
        RAISE EXCEPTION 'review source/version changed'; END IF;
    SELECT count(DISTINCT x)::integer INTO seen FROM jsonb_array_elements_text(request->'fields') x;
    IF seen<>jsonb_array_length(request->'fields') THEN RAISE EXCEPTION 'duplicate fields'; END IF;
    SELECT count(DISTINCT x)::integer INTO seen FROM jsonb_array_elements_text(request->'evidence_snapshot_ids') x;
    IF seen<>jsonb_array_length(request->'evidence_snapshot_ids') THEN RAISE EXCEPTION 'duplicate evidence'; END IF;
    FOR evidence IN SELECT jsonb_array_elements_text(request->'evidence_snapshot_ids') LOOP
        IF evidence !~ '^[0-9a-f]{64}$' OR NOT EXISTS(
            SELECT 1 FROM jsonb_each(dependency->'fields') f
            WHERE request->'fields' ? f.key AND f.value->'selected_source_ids' ? evidence) THEN
            RAISE EXCEPTION 'evidence is not a selected reviewed field source'; END IF;
    END LOOP;
    FOR field IN SELECT jsonb_array_elements_text(request->'fields') LOOP
        IF NOT dependency->'fields' ? field OR NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements_text(request->'evidence_snapshot_ids') x
            WHERE dependency->'fields'->field->'selected_source_ids' ? x) THEN
            RAISE EXCEPTION 'reviewed field/evidence mapping differs'; END IF;
        IF request->>'decision'='verified' AND NOT (
            dependency->'fields'->field->'value'<>'null'::jsonb
            AND dependency->'fields'->field->'head_usable'='true'::jsonb
            AND dependency->'fields'->field->'head_distinct_values'='1'::jsonb) IS TRUE THEN
            RAISE EXCEPTION 'unavailable/conflicting field cannot be verified'; END IF;
    END LOOP;
    event=jsonb_build_object('format','official-facts-review-event-v1','code',code,'version',prior_version+1,
        'caller',caller_name,'actor',actor_name,'request_hash',requested_hash,
        'dependency_hash',checksum,'dependencies',dependency,'request',request);
    identifier=encode(sha256(convert_to(mf_review.canonical_json(event),'UTF8')),'hex');
    event=event||jsonb_build_object('event_id',identifier);
    INSERT INTO mf_fact_review.events(event_id,amfi_scheme_code,version,dependency_hash,caller,actor,idempotency_key,request_hash,document)
        VALUES(identifier,code,prior_version+1,checksum,caller_name,actor_name,key,requested_hash,event);
    RETURN event;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA mf_fact_review FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_fact_review FROM PUBLIC;
DO $$ DECLARE principal text; BEGIN
    FOR principal IN SELECT rolname FROM pg_roles WHERE oid<>current_user::regrole LOOP
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA mf_fact_review FROM %I',principal);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA mf_fact_review FROM %I',principal);
        EXECUTE format('REVOKE ALL ON SCHEMA mf_fact_review FROM %I',principal);
    END LOOP;
    FOREACH principal IN ARRAY ARRAY['mfdata_verified_reader','mfdata_verified_admin'] LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA mf_fact_review TO %I',principal);
        EXECUTE format('GRANT SELECT ON mf_fact_review.events TO %I',principal);
        EXECUTE format('GRANT EXECUTE ON FUNCTION mf_fact_review.dependencies(integer) TO %I',principal);
    END LOOP;
    GRANT EXECUTE ON FUNCTION mf_fact_review.record_review(integer,text,text,text,jsonb) TO mfdata_verified_admin;
END $$;
COMMIT;
