-- DATA-03/04. Additive, dormant correction permissions; no source corrections.
BEGIN;

CREATE TABLE mf.nav_source_revisions (
    amfi_scheme_code integer PRIMARY KEY REFERENCES mf.funds(amfi_scheme_code) ON DELETE CASCADE,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0)
);
INSERT INTO mf.nav_source_revisions(amfi_scheme_code)
    SELECT DISTINCT amfi_scheme_code FROM mf.nav_history;
CREATE TABLE mf.nav_calculations (
    amfi_scheme_code integer PRIMARY KEY REFERENCES mf.funds(amfi_scheme_code) ON DELETE CASCADE,
    source_revision bigint NOT NULL,
    methodology_version text NOT NULL CHECK (methodology_version = '1'),
    as_of date NOT NULL,
    metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics) = 'object'),
    profile jsonb,
    computed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE mf.nav_quality_assessments ADD COLUMN assessed_source_revision bigint;
CREATE FUNCTION mf.bind_nav_assessment_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
BEGIN
    NEW.assessed_source_revision := (SELECT revision FROM mf.nav_source_revisions WHERE amfi_scheme_code=NEW.amfi_scheme_code);
    RETURN NEW;
END $$;
CREATE TRIGGER bind_nav_assessment_revision BEFORE INSERT OR UPDATE ON mf.nav_quality_assessments
    FOR EACH ROW EXECUTE FUNCTION mf.bind_nav_assessment_revision();
CREATE TABLE mf.nav_correction_approvals (
    proposal_sha256 text PRIMARY KEY REFERENCES mf.nav_remediation_proposals(proposal_sha256),
    payload jsonb NOT NULL,
    CHECK ((payload->>'proposal_sha256' = proposal_sha256) IS TRUE),
    CHECK (payload->>'sha256' ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mf.nav_remediation_operations (
    proposal_sha256 text NOT NULL REFERENCES mf.nav_correction_approvals(proposal_sha256),
    action text NOT NULL CHECK (action IN ('apply','rollback')),
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(proposal_sha256, action),
    CHECK ((payload->>'proposal_sha256' = proposal_sha256) IS TRUE),
    CHECK ((payload->>'action' = action) IS TRUE)
);
CREATE TABLE mf.nav_correction_heads (
    amfi_scheme_code integer NOT NULL,
    nav_date date NOT NULL,
    approved_nav numeric(18,4) NOT NULL,
    proposal_sha256 text NOT NULL REFERENCES mf.nav_correction_approvals(proposal_sha256),
    PRIMARY KEY(amfi_scheme_code, nav_date)
);
CREATE TABLE mf.nav_ingestion_conflicts (
    amfi_scheme_code integer NOT NULL, nav_date date NOT NULL,
    proposal_sha256 text NOT NULL,
    approved_nav numeric(18,4) NOT NULL,
    incoming_nav numeric(18,4) NOT NULL,
    incoming_source text NOT NULL,
    first_observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(amfi_scheme_code,nav_date,proposal_sha256,incoming_nav,incoming_source)
);
-- Nothing can rewrite the historical approval, receipt or conflict evidence.
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['nav_correction_approvals','nav_remediation_operations','nav_ingestion_conflicts'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE OR TRUNCATE ON mf.%I '
            'FOR EACH STATEMENT EXECUTE FUNCTION mf.reject_remediation_rewrite()',t||'_immutable',t);
    END LOOP;
END $$;

CREATE FUNCTION mf.track_nav_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
BEGIN
    IF TG_OP='UPDATE' AND NEW.amfi_scheme_code=OLD.amfi_scheme_code
       AND NEW.nav_date=OLD.nav_date AND NEW.nav IS NOT DISTINCT FROM OLD.nav THEN
        RETURN NEW;
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
        RAISE EXCEPTION 'another governed NAV operation is running';
    END IF;
    IF TG_OP IN ('UPDATE','DELETE') THEN
        INSERT INTO mf.nav_source_revisions VALUES(OLD.amfi_scheme_code,1)
        ON CONFLICT(amfi_scheme_code) DO UPDATE SET revision=nav_source_revisions.revision+1;
    END IF;
    IF TG_OP='INSERT' OR (TG_OP='UPDATE' AND NEW.amfi_scheme_code<>OLD.amfi_scheme_code) THEN
        INSERT INTO mf.nav_source_revisions VALUES(NEW.amfi_scheme_code,1)
        ON CONFLICT(amfi_scheme_code) DO UPDATE SET revision=nav_source_revisions.revision+1;
    END IF;
    -- Duplicate assessments depend on every series in the guarded family,
    -- not only the series receiving a new NAV. Conservative invalidation is
    -- cheaper than an incorrect "current/clean" counterpart assertion.
    UPDATE mf.nav_source_revisions r SET revision=r.revision+1
        FROM mf.fund_variants peer
        WHERE r.amfi_scheme_code=peer.amfi_scheme_code
          AND peer.group_key IN (SELECT group_key FROM mf.fund_variants
              WHERE amfi_scheme_code IN (CASE WHEN TG_OP='DELETE' THEN OLD.amfi_scheme_code ELSE NEW.amfi_scheme_code END))
          AND r.amfi_scheme_code<>CASE WHEN TG_OP='DELETE' THEN OLD.amfi_scheme_code ELSE NEW.amfi_scheme_code END;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER nav_revision_changed AFTER INSERT OR UPDATE OR DELETE ON mf.nav_history
    FOR EACH ROW EXECUTE FUNCTION mf.track_nav_revision();
CREATE FUNCTION mf.track_nav_inception() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
BEGIN
    IF NEW.inception_date IS DISTINCT FROM OLD.inception_date THEN
        IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN
            RAISE EXCEPTION 'another governed NAV operation is running';
        END IF;
        INSERT INTO mf.nav_source_revisions VALUES(NEW.amfi_scheme_code,1)
        ON CONFLICT(amfi_scheme_code) DO UPDATE SET revision=nav_source_revisions.revision+1;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER nav_inception_changed AFTER UPDATE OF inception_date ON mf.fund_facts
    FOR EACH ROW EXECUTE FUNCTION mf.track_nav_inception();
CREATE FUNCTION mf.track_nav_identity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
DECLARE groups text[];
BEGIN
    IF TG_TABLE_NAME='funds' THEN
        IF ROW(NEW.amc_id,NEW.scheme_type,NEW.scheme_category,NEW.plan_type,NEW.option_type,NEW.periodicity)
           IS NOT DISTINCT FROM ROW(OLD.amc_id,OLD.scheme_type,OLD.scheme_category,OLD.plan_type,OLD.option_type,OLD.periodicity) THEN RETURN NEW; END IF;
        SELECT array_agg(group_key) INTO groups FROM mf.fund_variants WHERE amfi_scheme_code=NEW.amfi_scheme_code;
    ELSE
        IF NEW.group_key IS NOT DISTINCT FROM OLD.group_key THEN RETURN NEW; END IF;
        groups:=ARRAY[OLD.group_key,NEW.group_key];
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'another governed NAV operation is running'; END IF;
    UPDATE mf.nav_source_revisions r SET revision=r.revision+1 WHERE r.amfi_scheme_code=NEW.amfi_scheme_code
        OR r.amfi_scheme_code IN(SELECT amfi_scheme_code FROM mf.fund_variants WHERE group_key=ANY(groups));
    RETURN NEW;
END $$;
CREATE TRIGGER nav_identity_changed AFTER UPDATE ON mf.funds FOR EACH ROW EXECUTE FUNCTION mf.track_nav_identity();
CREATE TRIGGER nav_family_changed AFTER UPDATE OF group_key ON mf.fund_variants FOR EACH ROW EXECUTE FUNCTION mf.track_nav_identity();

-- A future runtime principal gets EXECUTE, never direct NAV UPDATE rights.
CREATE FUNCTION mf.correct_nav_observation(p_hash text,p_code integer,p_day date,p_version bigint,p_rollback boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
DECLARE proposal jsonb; approval jsonb; expected jsonb; change jsonb; case_payload jsonb; actual mf.nav_history%ROWTYPE;
BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'another governed NAV operation is running'; END IF;
    IF (SELECT dataset_version FROM mf.dataset_summary WHERE singleton) IS DISTINCT FROM p_version THEN
        RAISE EXCEPTION 'dataset version conflict';
    END IF;
    SELECT a.payload,p.payload INTO approval,proposal FROM mf.nav_correction_approvals a
        JOIN mf.nav_remediation_proposals p USING(proposal_sha256) WHERE a.proposal_sha256=p_hash;
    IF proposal IS NULL OR approval IS NULL OR approval->>'status' IS DISTINCT FROM 'APPROVED_FINITE_BATCH' THEN
        RAISE EXCEPTION 'unknown approved proposal';
    END IF;
    IF jsonb_typeof(proposal->'changes') IS DISTINCT FROM 'array'
       OR jsonb_array_length(proposal->'changes') NOT BETWEEN 1 AND 100
       OR (SELECT count(DISTINCT value->>'amfi_scheme_code') FROM jsonb_array_elements(proposal->'changes')) > 5 THEN
        RAISE EXCEPTION 'proposal exceeds finite pilot limits';
    END IF;
    SELECT value INTO change FROM jsonb_array_elements(proposal->'changes')
        WHERE (value->>'amfi_scheme_code')::integer=p_code AND (value->>'nav_date')::date=p_day;
    IF change IS NULL THEN RAISE EXCEPTION 'observation outside approved proposal'; END IF;
    IF (change->'before'->>'amfi_scheme_code')::integer IS DISTINCT FROM p_code
       OR (change->'before'->>'nav_date')::date IS DISTINCT FROM p_day
       OR (change->>'after_nav')::numeric IS NULL
       OR (change->>'after_nav')::numeric < 0
       OR (change->>'after_nav')::numeric <> round((change->>'after_nav')::numeric,4) THEN
        RAISE EXCEPTION 'invalid exact replacement observation';
    END IF;
    IF EXISTS(SELECT 1 FROM mf.nav_remediation_operations WHERE proposal_sha256=p_hash AND action='rollback') THEN
        RAISE EXCEPTION 'proposal was rolled back; obtain a new review';
    END IF;
    IF p_rollback THEN
        SELECT value INTO expected FROM mf.nav_remediation_operations o,
            LATERAL jsonb_array_elements(o.payload->'after_images')
            WHERE o.proposal_sha256=p_hash AND o.action='apply'
              AND (value->>'amfi_scheme_code')::integer=p_code AND (value->>'nav_date')::date=p_day;
        IF expected IS NULL THEN RAISE EXCEPTION 'no applied before-image for rollback'; END IF;
    ELSE
        IF (proposal->>'expected_dataset_version')::bigint<>p_version THEN RAISE EXCEPTION 'stale proposal'; END IF;
        SELECT payload INTO case_payload FROM mf.nav_remediation_snapshots WHERE snapshot_sha256=proposal->>'case_snapshot_sha256';
        IF case_payload IS NULL OR NOT (case_payload->'scheme_codes' @> jsonb_build_array(p_code))
           OR (case_payload->>'dataset_version')::bigint IS DISTINCT FROM p_version THEN
            RAISE EXCEPTION 'observation outside current reviewed case';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM mf.nav_remediation_decisions d WHERE d.decision_sha256=proposal->>'decision_sha256'
            AND d.disposition='confirmed_nav_discrepancy'
            AND NOT EXISTS(SELECT 1 FROM mf.nav_remediation_decisions n WHERE n.supersedes_sha256=d.decision_sha256)) THEN
            RAISE EXCEPTION 'discrepancy decision was superseded';
        END IF;
        expected:=change->'before';
        IF EXISTS(SELECT 1 FROM mf.nav_correction_heads WHERE amfi_scheme_code=p_code AND nav_date=p_day) THEN
            RAISE EXCEPTION 'an active correction already owns observation';
        END IF;
    END IF;
    SELECT * INTO actual FROM mf.nav_history WHERE amfi_scheme_code=p_code AND nav_date=p_day FOR UPDATE;
    IF NOT FOUND OR actual.nav IS DISTINCT FROM (expected->>'nav')::numeric
       OR actual.source IS DISTINCT FROM expected->>'source'
       OR actual.is_cross_verified IS DISTINCT FROM (expected->>'is_cross_verified')::boolean
       OR actual.ingested_at IS DISTINCT FROM (expected->>'ingested_at')::timestamptz THEN
        RAISE EXCEPTION 'exact observation image conflict';
    END IF;
    IF p_rollback THEN
        DELETE FROM mf.nav_correction_heads WHERE amfi_scheme_code=p_code AND nav_date=p_day AND proposal_sha256=p_hash;
        IF NOT FOUND THEN RAISE EXCEPTION 'correction ownership changed'; END IF;
        expected:=change->'before';
        UPDATE mf.nav_history SET nav=(expected->>'nav')::numeric,source=expected->>'source',
            is_cross_verified=(expected->>'is_cross_verified')::boolean,ingested_at=(expected->>'ingested_at')::timestamptz
            WHERE amfi_scheme_code=p_code AND nav_date=p_day;
    ELSE
        -- Head is installed AFTER the update; even the writer cannot bypass protection later.
        UPDATE mf.nav_history SET nav=(change->>'after_nav')::numeric,source='AMFI_HISTORY',
            is_cross_verified=false,ingested_at=clock_timestamp() WHERE amfi_scheme_code=p_code AND nav_date=p_day;
        INSERT INTO mf.nav_correction_heads VALUES(p_code,p_day,(change->>'after_nav')::numeric,p_hash);
    END IF;
END $$;
-- The runtime gets ONLY the whole-batch capability. The row primitive above
-- is owner-only; a caller cannot commit a chosen subset of an approved batch.
CREATE FUNCTION mf.correct_nav_batch(p_hash text,p_version bigint,p_rollback boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
DECLARE proposal jsonb; case_payload jsonb; change jsonb; member jsonb; identity jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'another governed NAV operation is running'; END IF;
    SELECT p.payload,s.payload INTO proposal,case_payload FROM mf.nav_remediation_proposals p
        JOIN mf.nav_remediation_snapshots s ON s.snapshot_sha256=p.payload->>'case_snapshot_sha256'
        WHERE p.proposal_sha256=p_hash;
    IF proposal IS NULL THEN RAISE EXCEPTION 'unknown proposal'; END IF;
    IF NOT pg_try_advisory_xact_lock(hashtext(proposal->>'case_snapshot_sha256'),2) THEN
        RAISE EXCEPTION 'case review is busy';
    END IF;
    -- Check the dependency snapshot once BEFORE any row is edited. Row
    -- invalidation during this batch must not reject its later observations.
    IF NOT p_rollback AND EXISTS(SELECT 1 FROM mf.nav_source_revisions r
        LEFT JOIN mf.nav_quality_assessments q USING(amfi_scheme_code)
        WHERE r.amfi_scheme_code IN(SELECT value::integer FROM jsonb_array_elements_text(case_payload->'scheme_codes'))
          AND (q.assessed_source_revision IS DISTINCT FROM r.revision
            OR q.assessed_dataset_version IS DISTINCT FROM p_version OR q.methodology_version IS DISTINCT FROM '1')) THEN
        RAISE EXCEPTION 'reviewed source dependencies changed';
    END IF;
    IF NOT p_rollback THEN
        FOR member IN SELECT value FROM jsonb_array_elements(case_payload->'members') LOOP
            SELECT jsonb_build_object('scheme_name',f.scheme_name,'amc_id',f.amc_id,'amc_name',am.amfi_amc_name,
                'scheme_type',f.scheme_type,'scheme_category',f.scheme_category,'plan_type',f.plan_type,
                'option_type',f.option_type,'periodicity',f.periodicity,'isin_primary',f.isin_primary,
                'isin_payout',f.isin_growth_or_div_payout,'isin_reinvest',f.isin_div_reinvest,'is_defunct',f.is_defunct,
                'last_seen_in_source',f.last_seen_in_source,'group_key',fv.group_key) INTO identity
                FROM mf.funds f JOIN mf.amcs am USING(amc_id) LEFT JOIN mf.fund_variants fv USING(amfi_scheme_code)
                WHERE f.amfi_scheme_code=(member->>'amfi_scheme_code')::integer;
            IF identity IS DISTINCT FROM member->'identity' THEN RAISE EXCEPTION 'reviewed fund identity changed'; END IF;
        END LOOP;
    END IF;
    FOR change IN SELECT value FROM jsonb_array_elements(proposal->'changes') LOOP
        PERFORM mf.correct_nav_observation(p_hash,(change->>'amfi_scheme_code')::integer,
            (change->>'nav_date')::date,p_version,p_rollback);
    END LOOP;
END $$;
CREATE FUNCTION mf.protect_nav_correction() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
DECLARE head mf.nav_correction_heads%ROWTYPE;
BEGIN
    SELECT * INTO head FROM mf.nav_correction_heads WHERE amfi_scheme_code=OLD.amfi_scheme_code AND nav_date=OLD.nav_date;
    IF FOUND THEN
        IF TG_OP='DELETE' OR NEW.amfi_scheme_code<>OLD.amfi_scheme_code OR NEW.nav_date<>OLD.nav_date
           OR NEW.nav IS DISTINCT FROM head.approved_nav THEN RAISE EXCEPTION 'protected correction conflicts with incoming observation'; END IF;
        RETURN OLD; -- equal incoming NAV must not rewrite the protected provenance/image
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER protect_nav_correction BEFORE UPDATE OR DELETE ON mf.nav_history
    FOR EACH ROW EXECUTE FUNCTION mf.protect_nav_correction();
CREATE FUNCTION mf.quarantine_nav_conflict(p_code integer,p_day date,p_nav numeric,p_source text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,mf AS $$
DECLARE head mf.nav_correction_heads%ROWTYPE;
BEGIN
    SELECT * INTO head FROM mf.nav_correction_heads WHERE amfi_scheme_code=p_code AND nav_date=p_day;
    IF FOUND AND head.approved_nav IS DISTINCT FROM p_nav THEN
        INSERT INTO mf.nav_ingestion_conflicts(amfi_scheme_code,nav_date,proposal_sha256,approved_nav,incoming_nav,incoming_source)
            VALUES(p_code,p_day,head.proposal_sha256,head.approved_nav,p_nav,p_source) ON CONFLICT DO NOTHING;
        RETURN true;
    END IF;
    RETURN false;
END $$;

-- API projections keep the v2 schema, but never serve unversioned NAV-derived numbers.
DO $$ DECLARE columns_sql text; BEGIN
    SELECT string_agg(CASE WHEN column_name='beta' THEN 'NULL::numeric AS beta'
        WHEN column_name IN ('sharpe_ratio','std_deviation','return_1day','return_1week','return_1month',
        'return_3month','return_6month','return_9month','return_1year','return_2year','return_3year','return_4year',
        'return_5year','return_7year','return_10year','return_since_launch')
        THEN format('(c.metrics->>%L)::numeric AS %I',column_name,column_name)
        ELSE format('ff.%I',column_name) END,',' ORDER BY ordinal_position)
        INTO columns_sql FROM information_schema.columns WHERE table_schema='mf' AND table_name='fund_facts';
    EXECUTE 'CREATE VIEW mf.current_fund_facts AS SELECT '||columns_sql||' FROM mf.fund_facts ff '
        'LEFT JOIN mf.nav_source_revisions r USING(amfi_scheme_code) '
        'LEFT JOIN mf.nav_calculations c ON c.amfi_scheme_code=ff.amfi_scheme_code '
        'AND c.source_revision=r.revision AND c.methodology_version=''1''';
END $$;
CREATE VIEW mf.current_risk_profile AS
    SELECT c.amfi_scheme_code,(c.profile->>'points')::integer AS points,
        (c.profile->>'first_nav_date')::date AS first_nav_date,(c.profile->>'last_nav_date')::date AS last_nav_date,
        (c.profile->>'annualized_return')::numeric AS annualized_return,(c.profile->>'annual_vol')::numeric AS annual_vol,
        (c.profile->>'cagr')::numeric AS cagr,(c.profile->>'max_drawdown')::numeric AS max_drawdown
    FROM mf.nav_calculations c JOIN mf.nav_source_revisions r USING(amfi_scheme_code)
    WHERE c.source_revision=r.revision AND c.methodology_version='1' AND c.profile IS NOT NULL;

REVOKE ALL ON mf.nav_source_revisions,mf.nav_calculations,mf.nav_correction_approvals,mf.nav_remediation_operations,
    mf.nav_correction_heads,mf.nav_ingestion_conflicts,mf.current_fund_facts,mf.current_risk_profile FROM PUBLIC;
REVOKE ALL ON FUNCTION mf.correct_nav_batch(text,bigint,boolean),mf.track_nav_identity(),mf.bind_nav_assessment_revision(),mf.track_nav_revision(),mf.track_nav_inception(),mf.correct_nav_observation(text,integer,date,bigint,boolean),
    mf.protect_nav_correction(),mf.quarantine_nav_conflict(integer,date,numeric,text) FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
    FOREACH role_name IN ARRAY ARRAY['mfdata_app','mfdata_ingest','mfdata_association_writer','mfdata_audit','mfdata_nav_correction_writer'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
            EXECUTE format('REVOKE ALL ON mf.nav_source_revisions,mf.nav_calculations,mf.nav_correction_approvals,'
                'mf.nav_remediation_operations,mf.nav_correction_heads,mf.nav_ingestion_conflicts,mf.current_fund_facts,mf.current_risk_profile FROM %I',role_name);
            EXECUTE format('REVOKE ALL ON FUNCTION mf.correct_nav_batch(text,bigint,boolean),mf.correct_nav_observation(text,integer,date,bigint,boolean),'
                'mf.quarantine_nav_conflict(integer,date,numeric,text),mf.track_nav_revision(),mf.track_nav_inception(),mf.protect_nav_correction(),mf.bind_nav_assessment_revision(),mf.track_nav_identity() FROM %I',role_name);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_app') THEN
        GRANT SELECT ON mf.current_fund_facts,mf.current_risk_profile,mf.nav_source_revisions TO mfdata_app;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_ingest') THEN
        GRANT SELECT,INSERT,UPDATE ON mf.nav_calculations TO mfdata_ingest;
        GRANT SELECT ON mf.nav_source_revisions TO mfdata_ingest;
        GRANT EXECUTE ON FUNCTION mf.quarantine_nav_conflict(integer,date,numeric,text) TO mfdata_ingest;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_audit') THEN
        GRANT SELECT ON mf.nav_source_revisions TO mfdata_audit;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='mfdata_remediation_review') THEN
        GRANT SELECT,INSERT ON mf.nav_correction_approvals TO mfdata_remediation_review;
    END IF;
END $$;
COMMIT;
