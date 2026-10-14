"""Owner-operated additive extension, with source AND prior-artifact preservation."""
from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

from psycopg import sql
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from mfdataindia.review import publication, store
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from mfdataindia.review.privileges import verify_principal
from mfdataindia.review.rollout import CALLER_SA, ROLES, SECRETS, service_origin, verify_evidence
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = '024_research_publication.sql'


def verified_record(cloud, manifest, execution_name, origin, versions):
    """Collect live immutable execution metadata/logs, not operator-supplied proof."""
    from mfdataindia.review.image_provenance import verify_image
    from mfdataindia.review.rollout_cloud import verify_service
    origin = service_origin(origin)
    service = cloud.service()
    verify_service(service)
    if service['status']['url'] != origin:
        raise ValueError('certificate must name the fixed private provider service')
    verify_image(cloud,manifest.image,manifest.git_sha)
    env = {'DEPLOYMENT_GIT_SHA':manifest.git_sha,'REVIEW_MANIFEST':manifest.model_dump_json(),
           'REVIEW_OPERATION':'certify','REVIEW_ORIGIN':origin,'REVIEW_AUDIENCE':origin}
    secrets = ['MFDATAINDIA_DSN='+SECRETS[ROLES[1]]+':'+str(versions[ROLES[1]]),
               '/trust/config.json='+manifest.trust_secret+':'+str(manifest.trust_version),
               '/signer/key.pem='+manifest.signer_secret+':'+str(manifest.signer_version)]
    execution, certificate = cloud.inspect_execution(manifest,execution_name,CALLER_SA,
        ['scripts/certify_verified_review_dev.py'],env,secrets)
    evidence = verify_evidence(manifest,'certification',certificate)
    if (evidence.get('level') != 'DEV_PRIVATE_HTTP' or evidence.get('origin') != origin
            or evidence.get('audience') != origin or evidence.get('endpoints') != 6
            or evidence.get('canary_versions') != [1,2] or evidence.get('source_writes') != 0
            or evidence.get('insights_rbac_certified') is not False
            or not set(manifest.required_coverage) <= set(evidence.get('coverage_observed',[]))
            or len(evidence.get('cohort',[])) != len(manifest.cohort)
            or {r['code'] for r in evidence['cohort']} != set(manifest.cohort)
            or not execution['status'].get('completionTime')):
        raise ValueError('incomplete or mismatched provider certification')
    return {'format':'verified-research-certification-v1','publication_policy_hash':fingerprint(publication.policy()),
            'manifest':manifest.model_dump(mode='json'),'execution':execution,'certificate':certificate}


def require_compatible_service(cloud, manifest, versions):
    from mfdataindia.review.rollout_cloud import REVIEW_FACTORY, revision_name, verify_revision, verify_service
    service = cloud.service()
    verify_service(service)
    expected = revision_name(manifest,REVIEW_FACTORY)
    traffic = service['status'].get('traffic',[])
    serving = [item for item in traffic if item.get('percent',0)>0]
    if (any(item.get('tag') for item in traffic) or len(serving)!=1
            or serving[0].get('revisionName')!=expected or serving[0].get('percent')!=100):
        raise ValueError('certified compatible six-route revision must serve all untagged traffic before installation')
    verify_revision(cloud.revision(expected),manifest,versions,factory=REVIEW_FACTORY)
    cloud.iam(complete=True)
    return expected


def cloud_install(cloud, manifest, versions, directory):
    """Normal operation refuses SQL activation beneath an incompatible old API."""
    from mfdataindia.review.installation import MIGRATION_SA
    prior = require_compatible_service(cloud,manifest,versions)
    execution, certificate = cloud.job(manifest,'mf-pub-install-'+manifest.rollout_id,MIGRATION_SA,
        ['scripts/operate_research_publication_dev.py'],
        {'DEPLOYMENT_GIT_SHA':manifest.git_sha,'REVIEW_MANIFEST':manifest.model_dump_json(),'PUBLICATION_OPERATION':'install'},
        ['MFDATAINDIA_DSN=tlws-mf-data-admin-dsn-dev:'+str(manifest.source_secret_version)],directory)
    evidence = verify_evidence(manifest,'publication-install',certificate)
    if (evidence.get('level')!='DEV_CLOUD_SQL_JOB' or evidence.get('status') not in ('INSTALLED','ALREADY_INSTALLED')
            or evidence.get('original_source_rows_changed')!=0):
        raise ValueError('publication installation not certified')
    # Reconcile before feature staging; recover to this compatible image rather
    # than the pre-extension image, which would reject the new SELECT grants.
    try:
        require_compatible_service(cloud,manifest,versions)
    except BaseException:
        try:
            cloud.promote(prior)
        finally:
            cloud.close_access()
        raise
    return {'execution':execution,'certificate':certificate,'compatible_revision':prior}


def capture_review(conn, *, include_sequence=True):
    """Hash whole prior rows, not just documents; retain dates, actors and grants."""
    if not conn.execute("SELECT to_regnamespace('mf_review') AS schema").fetchone()['schema']:
        return None
    inventory = conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                             "WHERE n.nspname='mf_review' AND c.relkind IN('r','p','m') AND NOT c.relispartition").fetchall()
    if {r['relname'] for r in inventory} - {'policies','snapshots','results','events','certifications'}:
        raise ValueError('unmodelled prior review artifact; expand preservation inventory first')
    conn.execute("SET LOCAL TimeZone='UTC'")
    result = {}
    for table, key in (('policies','policy_hash'), ('snapshots','input_hash'), ('results','result_id'),
                       ('events','event_id'), ('certifications','certificate_id')):
        if not conn.execute('SELECT to_regclass(%s) AS relation', ('mf_review.'+table,)).fetchone()['relation']:
            if table != 'certifications':
                raise ValueError('prior review artifact inventory is incomplete')
            continue
        query = sql.SQL('SELECT {}::text,to_jsonb(t)::text FROM mf_review.{} t ORDER BY {}').format(
            sql.Identifier(key), sql.Identifier(table), sql.Identifier(key))
        rows = {}
        with conn.cursor() as cursor, cursor.copy(sql.SQL('COPY ({}) TO STDOUT (FORMAT BINARY)').format(query)) as copy:
            copy.set_types(['text','text'])
            for identifier, value in copy.rows():
                rows[identifier] = hashlib.sha256(value.encode()).hexdigest()
        result[table] = rows
    if include_sequence:
        # Trusted owner preservation includes sequence state. The restricted
        # consumer reader intentionally has no sequence SELECT capability.
        result['event_sequence'] = conn.execute('SELECT last_value,is_called FROM mf_review.events_event_id_seq').fetchone()
    return result


def compare_review(before, after, *, added_policy=None):
    if before is None:
        if after is not None:
            raise ValueError('unexpected review artifacts appeared')
        return
    if not isinstance(after, dict) or before['event_sequence'] != after.get('event_sequence'):
        raise ValueError('prior review sequence changed')
    for table in ('policies','snapshots','results','events','certifications'):
        earlier, later = before.get(table, {}), after.get(table, {})
        if any(later.get(key) != digest for key,digest in earlier.items()):
            raise ValueError('prior review artifact changed or disappeared: '+table)
        additions = set(later)-set(earlier)
        allowed = {added_policy} if table == 'policies' and added_policy else set()
        if additions-allowed:
            raise ValueError('undeclared artifact addition during installation: '+table)


def install(conn, root: Path):
    # Even a caller catching a Python validation failure cannot commit a
    # partially installed schema/policy or failed preservation check.
    with conn.transaction():
        return _install(conn, root)


def _install(conn, root: Path):
    identity = conn.execute('SELECT session_user AS principal,current_user=session_user AS dedicated').fetchone()
    if not identity['dedicated'] or identity['principal'] not in ('mfdata_admin','postgres'):
        raise ValueError('extension installation requires direct migration-owner login')
    if conn.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'] != 'repeatable read':
        raise ValueError('installation requires an explicit consistent transaction')
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL statement_timeout='120s'")
    conn.execute("SET LOCAL transaction_timeout='180s'")
    acquire_nav_data_operation_lock(conn)
    source = (root/'sql/optional'/MIGRATION).read_text()
    if hashlib.sha256(source.encode()).hexdigest() != publication.policy()['schema_sha256']:
        raise ValueError('publication migration differs from immutable policy pin')
    if conn.execute("SELECT to_regclass('mf_review.certifications') AS relation").fetchone()['relation']:
        publication.verify_installation(conn)
        return {'status':'ALREADY_INSTALLED','original_source_rows_changed':0}
    for schema in ('mf','mf_review'):
        relations = conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                 "WHERE n.nspname=%s AND c.relkind IN('r','p') AND NOT c.relispartition ORDER BY c.relname", (schema,)).fetchall()
        for row in relations:
            conn.execute(sql.SQL('LOCK TABLE {}.{} IN SHARE MODE').format(sql.Identifier(schema),sql.Identifier(row['relname'])))
    before = capture_preservation(conn)
    old_review = capture_review(conn)
    if len(re.findall(r'^BEGIN;$',source,re.M)) != 1 or not source.rstrip().endswith('COMMIT;'):
        raise ValueError('publication migration transaction shape differs')
    body = re.sub(r'^BEGIN;\n','',source,count=1,flags=re.M).rstrip()[:-len('COMMIT;')]
    conn.execute(body)
    document = publication.policy()
    store._insert_exact(conn,'policies','policy_hash',fingerprint(document),document,
                        {'policy_id':document['id'],'policy_version':document['version']})
    for role in ROLES:
        verify_principal(conn,'generator' if role==ROLES[0] else 'api',role)
    after = capture_preservation(conn)
    checksum = conn.execute("SELECT content_sha256 FROM mf.schema_migrations WHERE migration_name='023_verified_fund_review.sql'").fetchone()
    compare_preservation(before,after,checksum['content_sha256'])
    new_review = capture_review(conn)
    compare_review(old_review,new_review,added_policy=fingerprint(document))
    return {'status':'INSTALLED','original_source_rows_changed':0,'source':{'before':before,'after':after},
            'prior_review':{'before':old_review,'after':new_review},'publication_policy_hash':fingerprint(document),
            'migration_sha256':document['schema_sha256'],'source_migration_ledger_appended':False}


def rehearse(backup, clone_dsn, directory, manifest, root, tools):
    """Fresh restored clone with existing review artifacts, never first-install replay."""
    from mfdataindia.review.rollout_database import file_sha256, readonly
    if file_sha256(directory/'snapshot.dump') != backup['backup_sha256'] or not backup.get('review_baseline'):
        raise ValueError('require exact backup of the installed review provider')
    parsed = conninfo_to_dict(clone_dsn)
    if parsed.get('host') != '127.0.0.1' or parsed.get('dbname') != 'mfdata_review_clone_control':
        raise ValueError('require isolated runner clone server')
    name = 'mfdata_pub_rehearsal_'+manifest.rollout_id.replace('-','_')
    with psycopg.connect(clone_dsn,autocommit=True,row_factory=dict_row) as admin:
        databases = {r['datname'] for r in admin.execute('SELECT datname FROM pg_database').fetchall()}
        roles = {r['rolname'] for r in admin.execute("SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_'").fetchall()}
        if databases != {'postgres','template0','template1','mfdata_review_clone_control'} or roles != {'postgres'}:
            raise ValueError('scratch server not empty; never replace a prior clone')
        for role in backup['roles']:
            if role != 'postgres':
                admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    restored_dsn = make_conninfo(clone_dsn,dbname=name)
    tools.run('pg_restore',restored_dsn,directory,['--dbname='+name,'--exit-on-error','--single-transaction','/backup/snapshot.dump'])
    with readonly(restored_dsn) as conn:
        restored = capture_preservation(conn)
        restored_review = capture_review(conn)
    compare_preservation(backup['baseline'],restored,manifest.migration_hash)
    compare_review(backup['review_baseline'],restored_review)
    password = secrets.token_hex(32)
    with psycopg.connect(restored_dsn) as conn:
        conn.execute(sql.SQL('ALTER ROLE mfdata_admin LOGIN PASSWORD {}').format(sql.Literal(password)))
    owner_dsn = make_conninfo(restored_dsn,user='mfdata_admin',password=password)
    with psycopg.connect(owner_dsn,row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        installed = install(conn,root)
    with readonly(restored_dsn) as conn:
        after = capture_preservation(conn)
        after_review = capture_review(conn)
    compare_preservation(restored,after,manifest.migration_hash)
    compare_review(restored_review,after_review,added_policy=fingerprint(publication.policy()))
    return {'level':'OPERATIONAL_RESTORED_CLONE','backup_sha256':backup['backup_sha256'],
            'restored_database':name,'installation':installed,'source_preserved':True,'prior_review_preserved':True,
            'data_deleted':0,'source_migration_ledger_appended':False}
