"""Reviewed baseline adoption, not arbitrary automatic consumer exposure."""
from . import catalogue, enrollment, policy as p


def seed(conn, coverage):
    """Caller-owned transaction; verify complete snapshot metadata BEFORE DDL.

    No row values are read, no history is erased, and a drifted relation/column
    cannot be silently substituted. Future enrollments use the ordinary creator
    hook rather than editing this historical baseline.
    """
    p.require(type(coverage) is dict and coverage.get('format') == 'mfdata-dataset-coverage-inventory-v1'
              and type(coverage.get('relations')) is list, 'INVALID_BASELINE_COVERAGE')
    caller = conn.execute('SELECT current_user AS principal,session_user AS session').fetchone()
    p.require(caller['principal']==caller['session'] and caller['principal'] in catalogue.SUPPORTED_CREATORS,
              'DATASET_ENROLLMENT_CREATOR_REQUIRED')
    seen = set()
    candidates = []
    skipped = []
    for item in coverage['relations']:
        p.require(type(item) is dict and type(item.get('relation')) is str
                  and item['relation'].count('.') == 1, 'INVALID_BASELINE_COVERAGE')
        schema, relation = item['relation'].split('.')
        dataset = p.dataset_id(schema,relation)
        p.require(dataset not in seen, 'DUPLICATE_BASELINE_RELATION')
        seen.add(dataset)
        if ((schema,relation) in catalogue.INTERNAL_CONTROL_RELATIONS
                or (schema,relation) in catalogue.HELD_FOR_REVIEW):
            skipped.append({'relation':item['relation'],'registered':False})
            continue
        p.require(item.get('disposition') == 'eligible_implementation_pending'
                  and item.get('classification') in {'fund_research','source_evidence'}
                  and item.get('dataset_id') == dataset, 'INVALID_BASELINE_COVERAGE')
        actual = conn.execute("""SELECT c.relkind::text AS kind,pg_get_userbyid(c.relowner) AS owner,
            CASE WHEN c.relkind='v' THEN encode(sha256(convert_to(pg_get_viewdef(c.oid,false),'UTF8')),'hex') ELSE NULL END AS view_hash,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('name',a.attname,
                'database_type_oid',a.atttypid::bigint,'nullable',NOT a.attnotnull) ORDER BY a.attnum)
                FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),'[]') AS fields
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=%s""",(schema,relation)).fetchone()
        expected = [{k:f[k] for k in ('name','database_type_oid','nullable')} for f in item['fields']]
        p.require(actual is not None and actual['kind'] == item['kind']
                  and actual['owner'] == caller['principal'] and actual['fields'] == expected,
                  'BASELINE_CATALOGUE_DRIFT')
        keys = item.get('proposed_view_key_columns',[])
        if keys:
            p.require(actual['kind']=='v' and actual['view_hash']==item.get('view_definition_sha256'),
                      'BASELINE_VIEW_DEFINITION_DRIFT')
        candidates.append((schema,relation,item['classification'],keys))
    p.require(len(seen) == coverage.get('accounted_relation_count') == coverage.get('enrolled_relation_count'),
              'INCOMPLETE_BASELINE_ACCOUNTING')
    # All metadata comparisons completed before the first comment/grant.
    registered = [enrollment.enroll(conn,schema,relation,classification=classification,
        title=(schema + ' ' + relation).replace('_',' '),key_columns=keys or None)
        for schema,relation,classification,keys in candidates]
    permissions = enrollment.reconcile(conn)
    return {'format':p.VERSION,'registered':registered,'skipped':skipped,
            'permissions':permissions,'consumer_ready':False,'commit_performed':False}
