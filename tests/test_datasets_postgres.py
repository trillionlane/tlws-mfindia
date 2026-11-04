"""Real disposable hosted PostgreSQL only; never shared DEV or provider SQL."""
import base64
from hashlib import sha256
import json
from time import perf_counter

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.datasets import bootstrap, catalogue, enrollment, policy, reads
from mfdataindia.datasets.app import create_app
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db, connect, adopt, PASSWORD, generator_capabilities,
)

pytestmark = pytest.mark.postgres


def setup_dataset(dsn, schema='mf_dataset_fixture', relation='observations'):
    with connect(dsn, autocommit=False) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')  # Fixed fixture-only arguments.
        conn.execute(f'CREATE TABLE {schema}.{relation}(id bigint PRIMARY KEY,value numeric,notes text,pdf bytea)')
        conn.execute(f'INSERT INTO {schema}.{relation} VALUES(1,12.34,\'one\',%s),(2,NULL,\'two\',NULL),(3,56.78,\'three\',NULL)',
                     (b'fixture PDF bytes\x00\xff',))
        conn.execute(f"COMMENT ON TABLE {schema}.{relation} IS 'Original fixture documentation'")
        result = enrollment.enroll(conn, schema, relation, classification='fund_research', title='Synthetic observations')
        assert enrollment.enroll(conn,schema,relation,classification='fund_research',title='Synthetic observations')['metadata_writes'] == 0
        assert conn.execute("SELECT obj_description(%s::regclass,'pg_class') AS value",
                            (schema+'.'+relation,)).fetchone()['value'].startswith('Original fixture documentation\n')
    assert result['registered'] and not result['consumer_ready']
    return result['dataset_id']


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_new_schema_enrollment_permissions_discovery_rows_and_bounded_content(ro01_db):
    adopt(ro01_db)
    dataset = setup_dataset(ro01_db)
    with connect(ro01_db,autocommit=False) as conn:
        conn.execute('CREATE TABLE mf.dataset_existing_schema(id int PRIMARY KEY,value text)')
        conn.execute("INSERT INTO mf.dataset_existing_schema VALUES(1,'existing-schema')")
        existing = enrollment.enroll(conn,'mf','dataset_existing_schema',classification='fund_research',title='Existing schema')
    # Compare capabilities over the same object population. This helper also
    # records relation identities, so comparing before fixture DDL would report
    # a new object as a privilege change even when its capabilities are denied.
    generator_before = generator_capabilities(ro01_db)
    dsn = make_conninfo(ro01_db, user=policy.READER, password=PASSWORD)
    with TestClient(create_app(dsn)) as client:
        before = client.get('/api/datasets/v1').json()['datasets']
        before_item = next(d for d in before if d['id']==dataset)
        assert before_item['state'] == 'unavailable' and before_item['reason'] == 'DATASET_READ_GRANTS_MISSING'
        with connect(ro01_db, autocommit=False) as conn:
            result = enrollment.reconcile(conn)
            target = next(r for r in result['results'] if r['dataset_id']==dataset)
            assert target['permissions_verified'] and target['grant_statements'] == 2
            assert not result['consumer_ready']  # Not a fabricated HTTP/TI verdict.
        with connect(ro01_db, autocommit=False) as conn:
            assert all(r['grant_statements']==0 for r in enrollment.reconcile(conn)['results'])
        discovery = client.get('/api/datasets/v1')
        assert discovery.status_code == 200
        assert next(d for d in discovery.json()['datasets'] if d['id']==dataset)['state'] == 'available'
        existing_page = client.get('/api/datasets/v1/'+existing['dataset_id']+'/rows')
        assert existing_page.status_code==200 and existing_page.json()['rows']==[{'id':1,'value':'existing-schema'}]
        metadata = client.get('/api/datasets/v1/' + dataset).json()
        assert metadata['key_columns'] == ['id']
        assert {c['name'] for c in metadata['columns']} == {'id','value','notes','pdf'}
        first = client.get('/api/datasets/v1/' + dataset + '/rows', params={'limit': 2})
        assert first.status_code == 200
        body = first.json()
        assert body['ordering'] == 'keyset' and [r['id'] for r in body['rows']] == ['1','2']
        assert body['rows'][0]['value'] == '12.34' and body['rows'][1]['value'] is None
        second = client.get('/api/datasets/v1/' + dataset + '/rows', params={'limit':2,'cursor':body['next_cursor']})
        assert [r['id'] for r in second.json()['rows']] == ['3'] and second.json()['next_cursor'] is None
        blob_url = body['rows'][0]['pdf']['content_url']
        chunk = client.get(blob_url + '&length=8').json()
        assert chunk['sha256'] == sha256(b'fixture PDF bytes\x00\xff').hexdigest()
        tail = client.get(blob_url + '&offset=' + str(chunk['next_offset'])).json()
        assert base64.b64decode(chunk['content']) + base64.b64decode(tail['content']) == b'fixture PDF bytes\x00\xff'
        assert tail['next_offset'] is None
        assert client.get('/api/datasets/v1/' + dataset + '/rows', params={'limit': 101}).status_code == 422
        assert client.post('/api/datasets/v1/' + dataset + '/rows', json={}).status_code == 405
        assert client.get('/api/datasets/v1/d_' + '0'*32).status_code == 404
        with connect(ro01_db, policy.READER) as conn:
            for command in ('INSERT INTO mf_dataset_fixture.observations(id) VALUES(9)',
                            'UPDATE mf_dataset_fixture.observations SET notes=\'wrong\'',
                            'DELETE FROM mf_dataset_fixture.observations',
                            'CREATE TABLE mf_dataset_fixture.denied(id int)',
                            'ALTER ROLE mfdata_verified_reader CREATEROLE'):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(command)
    assert generator_capabilities(ro01_db) == generator_before


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_empty_views_missing_grants_unsafe_grants_and_control_exclusion(ro01_db):
    adopt(ro01_db)
    dataset = setup_dataset(ro01_db)
    with connect(ro01_db, autocommit=False) as conn:
        enrollment.reconcile(conn)
        conn.execute('CREATE VIEW mf_dataset_fixture.empty_observations AS SELECT id,notes FROM mf_dataset_fixture.observations WHERE false')
        view = enrollment.enroll(conn, 'mf_dataset_fixture', 'empty_observations', classification='fund_research', title='Synthetic empty')
        enrollment.enroll(conn, 'mf', 'schema_migrations', classification='fund_research', title='Must never be exposed')
        enrollment.reconcile(conn)
    with TestClient(create_app(make_conninfo(ro01_db,user=policy.READER,password=PASSWORD))) as client:
        discovered = client.get('/api/datasets/v1').json()['datasets']
        assert len(discovered) == 2
        assert all(d['title'] != 'Must never be exposed' for d in discovered)
        assert client.get('/api/datasets/v1/' + policy.dataset_id('mf','schema_migrations')).status_code == 404
        empty = client.get('/api/datasets/v1/' + view['dataset_id'] + '/rows').json()
        assert empty['state'] == 'empty' and empty['rows'] == [] and empty['ordering'] == 'canonical_offset'
        with connect(ro01_db, autocommit=False) as conn:
            conn.execute('REVOKE SELECT ON mf_dataset_fixture.observations FROM mfdata_verified_reader')
        missing = client.get('/api/datasets/v1/' + dataset + '/rows')
        assert missing.status_code == 503 and missing.json()['detail'] == 'DATASET_READ_GRANTS_MISSING'
        with connect(ro01_db, autocommit=False) as conn:
            enrollment.reconcile(conn)
            conn.execute('GRANT UPDATE(notes) ON mf_dataset_fixture.observations TO mfdata_verified_reader')
        unsafe = client.get('/api/datasets/v1/' + dataset + '/rows')
        assert unsafe.status_code == 503 and unsafe.json()['detail'] == 'UNSAFE_DATASET_CAPABILITY'


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_second_supported_creator_and_failed_reconcile_do_not_undo_ingestion(ro01_db, pg_dsn):
    adopt(ro01_db)
    # Fixture bootstrap supplies a schema to the existing non-elevated creator;
    # production code grants neither CREATE nor membership to any writer.
    with psycopg.connect(make_conninfo(pg_dsn, dbname='mfdataindia'), autocommit=True) as fixture:
        fixture.execute('CREATE SCHEMA mf_other_creator AUTHORIZATION mfdata_ingest')
    with connect(ro01_db, 'mfdata_ingest', autocommit=False) as conn:
        conn.execute('CREATE TABLE mf_other_creator.new_facts(id int PRIMARY KEY,document jsonb)')
        conn.execute('INSERT INTO mf_other_creator.new_facts VALUES(1,\'{"value":7}\')')
        dataset = enrollment.enroll(conn, 'mf_other_creator', 'new_facts', classification='fund_research', title='Second creator')
        report = enrollment.reconcile(conn)
        assert report['results'][0]['permissions_verified']
        assert report['results'][0]['grant_statements'] == 2
    with connect(ro01_db, policy.READER) as conn:
        conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        assert reads.rows(conn, dataset['dataset_id'])['rows'] == [{'id':1,'document':{'value':7}}]
        with pytest.raises(policy.DatasetError, match='CREATOR_REQUIRED'):
            enrollment.enroll(conn,'mf_other_creator','new_facts',classification='fund_research',title='Forbidden reader')
    with connect(ro01_db, 'mfdata_ingest', autocommit=False) as conn:
        for column in ('password','api_token','reader_dsn'):
            conn.execute(f'CREATE TABLE mf_other_creator.unsafe_{column}(id int PRIMARY KEY,{column} text)')
            enrollment.enroll(conn,'mf_other_creator','unsafe_'+column,classification='fund_research',title='Must block')
        conn.execute('CREATE TABLE mf_other_creator.credential_notes(id int PRIMARY KEY,notes text)')
        conn.execute("INSERT INTO mf_other_creator.credential_notes VALUES(1,'api_token=synthetic-secret-marker')")
        text = enrollment.enroll(conn,'mf_other_creator','credential_notes',classification='fund_research',title='Content must block')
        conn.execute('INSERT INTO mf_other_creator.new_facts VALUES(2,\'{"value":8}\')')
        report = enrollment.after_migrations(conn)
        assert sum(row['reason'] == 'SENSITIVE_DATASET_COLUMN' for row in report['results'])==3
    with connect(ro01_db, policy.READER) as conn:
        conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        assert [r['id'] for r in reads.rows(conn,dataset['dataset_id'])['rows']] == [1,2]
        for column in ('password','api_token','reader_dsn'):
            assert conn.execute("SELECT has_table_privilege(current_user,%s,'SELECT') AS ok",
                                ('mf_other_creator.unsafe_'+column,)).fetchone()['ok'] is False
    with TestClient(create_app(make_conninfo(ro01_db,user=policy.READER,password=PASSWORD))) as client:
        rejected = client.get('/api/datasets/v1/'+text['dataset_id']+'/rows')
        assert rejected.status_code==503 and rejected.json()['detail']=='SENSITIVE_DATASET_VALUE'
        assert 'synthetic-secret-marker' not in rejected.text


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_unsafe_view_function_and_outside_inherited_child_fail_before_read(ro01_db):
    adopt(ro01_db)
    dataset = setup_dataset(ro01_db)
    with connect(ro01_db,autocommit=False) as conn:
        enrollment.reconcile(conn)
        conn.execute('CREATE TABLE mf_dataset_fixture.function_effect(value int)')
        conn.execute("""CREATE FUNCTION mf_dataset_fixture.unsafe_read() RETURNS int
            LANGUAGE plpgsql SECURITY DEFINER AS $$BEGIN
            INSERT INTO mf_dataset_fixture.function_effect VALUES(1); RETURN 1; END$$""")
        conn.execute('CREATE VIEW mf_dataset_fixture.unsafe_view AS SELECT mf_dataset_fixture.unsafe_read() AS id')
        unsafe = enrollment.enroll(conn,'mf_dataset_fixture','unsafe_view',classification='fund_research',title='Unsafe view')
        conn.execute("""CREATE FUNCTION mf_dataset_fixture.unsafe_binary(bigint,bigint) RETURNS boolean
            LANGUAGE plpgsql SECURITY DEFINER AS $$BEGIN
            INSERT INTO mf_dataset_fixture.function_effect VALUES(2); RETURN true; END$$""")
        conn.execute('CREATE OPERATOR mf_dataset_fixture.=== (LEFTARG=bigint,RIGHTARG=bigint,FUNCTION=mf_dataset_fixture.unsafe_binary)')
        conn.execute('CREATE VIEW mf_dataset_fixture.operator_view AS SELECT id FROM mf_dataset_fixture.observations WHERE id OPERATOR(mf_dataset_fixture.===) id')
        operator = enrollment.enroll(conn,'mf_dataset_fixture','operator_view',classification='fund_research',title='Unsafe operator')
        conn.execute('CREATE VIEW mf_dataset_fixture.control_alias AS SELECT migration_name::text AS note FROM mf.schema_migrations')
        control = enrollment.enroll(conn,'mf_dataset_fixture','control_alias',classification='fund_research',title='Control alias')
        report = enrollment.reconcile(conn)
        assert any(r['reason']=='UNREVIEWED_DATASET_DEPENDENCY' for r in report['results'])
        conn.execute('CREATE SCHEMA unrelated_fixture')
        conn.execute('CREATE TABLE unrelated_fixture.child() INHERITS(mf_dataset_fixture.observations)')
    with TestClient(create_app(make_conninfo(ro01_db,user=policy.READER,password=PASSWORD))) as client:
        for identifier in (dataset,unsafe['dataset_id'],operator['dataset_id'],control['dataset_id']):
            reply = client.get('/api/datasets/v1/'+identifier+'/rows')
            assert reply.status_code == 503 and reply.json()['detail']=='UNREVIEWED_DATASET_DEPENDENCY'
    with connect(ro01_db) as conn:
        assert conn.execute('SELECT count(*) AS value FROM mf_dataset_fixture.function_effect').fetchone()['value'] == 0


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_declared_view_key_null_boundary_duplicate_detection_and_byte_budget(ro01_db):
    adopt(ro01_db)
    setup_dataset(ro01_db)
    with connect(ro01_db,autocommit=False) as conn:
        conn.execute('CREATE VIEW mf_dataset_fixture.declared_view AS SELECT id,value FROM mf_dataset_fixture.observations UNION ALL SELECT NULL::bigint,NULL::numeric')
        view = enrollment.enroll(conn,'mf_dataset_fixture','declared_view',classification='fund_research',title='Declared view',key_columns=['id'])
        conn.execute('CREATE VIEW mf_dataset_fixture.duplicate_view AS SELECT id FROM mf_dataset_fixture.observations UNION ALL SELECT id FROM mf_dataset_fixture.observations')
        duplicate = enrollment.enroll(conn,'mf_dataset_fixture','duplicate_view',classification='fund_research',title='Duplicate view',key_columns=['id'])
        conn.execute('CREATE VIEW mf_dataset_fixture.unkeyed_binary AS SELECT pdf FROM mf_dataset_fixture.observations')
        binary = enrollment.enroll(conn,'mf_dataset_fixture','unkeyed_binary',classification='source_evidence',title='Unkeyed binary')
        conn.execute('CREATE TABLE mf_dataset_fixture.large_rows(id int PRIMARY KEY,notes text)')
        conn.execute("INSERT INTO mf_dataset_fixture.large_rows SELECT x,repeat('x',60000) FROM generate_series(1,40) x")
        large = enrollment.enroll(conn,'mf_dataset_fixture','large_rows',classification='fund_research',title='Byte bounded rows')
        reconciled = enrollment.reconcile(conn)
        assert next(item for item in reconciled['results'] if item['dataset_id']==binary['dataset_id'])['reason']=='BINARY_DATASET_KEY_REQUIRED'
    with TestClient(create_app(make_conninfo(ro01_db,user=policy.READER,password=PASSWORD))) as client:
        discovery = client.get('/api/datasets/v1').json()['datasets']
        unkeyed = next(item for item in discovery if item['id']==binary['dataset_id'])
        assert unkeyed['state']=='unavailable' and unkeyed['reason']=='DATASET_READ_GRANTS_MISSING'
        observed, cursor = [], None
        for _ in range(4):
            params = {'limit':1,**({'cursor':cursor} if cursor else {})}
            reply = client.get('/api/datasets/v1/'+view['dataset_id']+'/rows',params=params)
            assert reply.status_code==200
            observed.extend(r['id'] for r in reply.json()['rows'])
            cursor = reply.json()['next_cursor']
        assert observed==['1','2','3',None] and cursor is None
        refused = client.get('/api/datasets/v1/'+duplicate['dataset_id']+'/rows',params={'limit':1})
        assert refused.status_code==503 and refused.json()['detail']=='NONUNIQUE_DATASET_PAGINATION_KEY'
        first = client.get('/api/datasets/v1/'+large['dataset_id']+'/rows',params={'limit':100})
        assert first.status_code==200 and len(first.content)<=policy.MAX_RESPONSE_BYTES
        assert 0<len(first.json()['rows'])<40 and first.json()['next_cursor']
        second = client.get('/api/datasets/v1/'+large['dataset_id']+'/rows',params={'limit':100,'cursor':first.json()['next_cursor']})
        assert second.status_code==200 and second.json()['next_cursor'] is None
        rows = first.json()['rows']+second.json()['rows']
        assert [r['id'] for r in rows]==list(range(1,41)) and all(len(r['notes'])==60000 for r in rows)


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_baseline_adoption_drift_idempotence_and_single_catalogue_per_request(ro01_db, monkeypatch):
    adopt(ro01_db)
    dataset = setup_dataset(ro01_db)
    with connect(ro01_db,autocommit=False) as conn:
        conn.execute("COMMENT ON TABLE mf_dataset_fixture.observations IS 'Original documentation'")
        fields = conn.execute("""SELECT attname AS name,atttypid::bigint AS database_type_oid,
            NOT attnotnull AS nullable FROM pg_attribute
            WHERE attrelid='mf_dataset_fixture.observations'::regclass AND attnum>0 AND NOT attisdropped
            ORDER BY attnum""").fetchall()
        item = {'relation':'mf_dataset_fixture.observations','kind':'r',
                'disposition':'eligible_implementation_pending','classification':'fund_research',
                'dataset_id':dataset,'fields':fields}
        coverage = {'format':'mfdata-dataset-coverage-inventory-v1','enrolled_relation_count':1,
                    'accounted_relation_count':1,'relations':[item]}
        wrong = json.loads(json.dumps(coverage))
        wrong['relations'][0]['fields'][0]['nullable'] = True
        with pytest.raises(policy.DatasetError,match='BASELINE_CATALOGUE_DRIFT'):
            bootstrap.seed(conn,wrong)
        assert conn.execute("SELECT obj_description('mf_dataset_fixture.observations'::regclass,'pg_class') AS value").fetchone()['value']=='Original documentation'
        assert not conn.execute("SELECT has_table_privilege('mfdata_verified_reader','mf_dataset_fixture.observations','SELECT') AS value").fetchone()['value']
        first = bootstrap.seed(conn,coverage)
        assert not first['consumer_ready'] and first['registered'][0]['metadata_writes']==1
        second = bootstrap.seed(conn,coverage)
        assert second['registered'][0]['metadata_writes']==0
        assert all(row['grant_statements']==0 for row in second['permissions']['results'])
    from mfdataindia.api import app as core
    from mfdataindia.review import broad_reader_policy
    counts = {'full_profile':0,'dataset_metadata':0,'dependencies':0,'execute_calls':0}
    class ObservedConnection(psycopg.Connection):
        def execute(self,query,*args,**kwargs):
            counts['execute_calls'] += 1
            if isinstance(query,str):
                counts['full_profile'] += int(query==broad_reader_policy.STATE_SQL)
                counts['dataset_metadata'] += int(query.lstrip().startswith('SELECT c.oid::bigint'))
                counts['dependencies'] += int(query==catalogue.DEPENDENCIES_SQL)
            return super().execute(query,*args,**kwargs)
    original_pool = core.ConnectionPool
    def pool(*args,**kwargs):
        return original_pool(*args,connection_class=ObservedConnection,**kwargs)
    monkeypatch.setattr(core,'ConnectionPool',pool)  # Real SQL, roles and pool; observation only.
    measurements = []
    with TestClient(create_app(make_conninfo(ro01_db,user=policy.READER,password=PASSWORD))) as client:
        for path in ('/api/datasets/v1','/api/datasets/v1/'+dataset,'/api/datasets/v1/'+dataset+'/rows'):
            before = dict(counts)
            started = perf_counter()
            reply = client.get(path)
            elapsed = perf_counter()-started
            assert reply.status_code==200
            delta = {k:counts[k]-before[k] for k in counts}
            assert delta['full_profile']==delta['dataset_metadata']==delta['dependencies']==1
            measurements.append({'stage':path.split('/')[-1],'seconds':elapsed,**delta})
    print(json.dumps({'native_dataset_queries_and_timings':measurements,
                      'not_a_production_SLA_or_live_consumer_acceptance':True},sort_keys=True))


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_existing_v4_business_projections_keep_seals_and_do_not_expose_controls(ro01_db,monkeypatch):
    from reader_execution_native_fixture import populate
    from mfdataindia.datasets import projections
    populate(ro01_db,monkeypatch,book_version=4)  # Supported PR142 v4 fixture, no later DDL.
    adopt(ro01_db)
    identifiers = {}
    with connect(ro01_db,autocommit=False) as conn:
        # Match the API's normal path before enrollment too. Projection seal
        # verification must be independent of the caller's visible namespaces.
        conn.execute('SET LOCAL search_path TO mf, public')
        for name in sorted(projections.NAMES-{'mf_live_facts.baseline'}):
            schema,relation = name.split('.')
            identifiers[name] = enrollment.enroll(conn,schema,relation,
                classification='fund_research',title=name)['dataset_id']
        conn.execute('CREATE SCHEMA mf_dataset_projection')
        conn.execute('CREATE VIEW mf_dataset_projection.values_view AS SELECT amfi_scheme_code FROM mf.current_fund_facts')
        alias = enrollment.enroll(conn,'mf_dataset_projection','values_view',classification='fund_research',title='Business projection alias')
        conn.execute('CREATE VIEW mf_dataset_projection.control_alias AS SELECT generation::bigint AS id FROM mf_live_facts.configuration')
        control = enrollment.enroll(conn,'mf_dataset_projection','control_alias',classification='fund_research',title='Must not expose control')
        conn.execute('CREATE VIEW mf_dataset_projection.mixed_alias AS SELECT f.amfi_scheme_code,c.generation FROM mf.current_fund_facts f CROSS JOIN mf_live_facts.configuration c')
        mixed = enrollment.enroll(conn,'mf_dataset_projection','mixed_alias',classification='fund_research',title='Mixed control path must block')
        result = enrollment.reconcile(conn)
        denied = {control['dataset_id'],mixed['dataset_id']}
        assert all(row['permissions_verified'] for row in result['results'] if row['dataset_id'] not in denied)
        assert all(row['reason']=='UNREVIEWED_DATASET_DEPENDENCY' for row in result['results'] if row['dataset_id'] in denied)
        assert conn.execute("SELECT current_setting('search_path') AS value").fetchone()['value']=='mf, public'
    with TestClient(create_app(make_conninfo(ro01_db,user=policy.READER,password=PASSWORD))) as client:
        discovery = client.get('/api/datasets/v1')
        assert discovery.status_code==200
        available = {row['id'] for row in discovery.json()['datasets'] if row['state']=='available'}
        assert set(identifiers.values())|{alias['dataset_id']} <= available, {
            'unavailable':[(row['id'],row['reason']) for row in discovery.json()['datasets'] if row['state']!='available']}
        for identifier in [*identifiers.values(),alias['dataset_id']]:
            metadata = client.get('/api/datasets/v1/'+identifier)
            assert metadata.status_code==200 and metadata.json()['columns']
            page = client.get('/api/datasets/v1/'+identifier+'/rows',params={'limit':2})
            assert page.status_code==200 and page.json()['state'] in ('available','empty')
        for identifier in denied:
            refused = client.get('/api/datasets/v1/'+identifier+'/rows')
            assert refused.status_code==503 and refused.json()['detail']=='UNREVIEWED_DATASET_DEPENDENCY'
        with connect(ro01_db,autocommit=False) as conn:
            body = conn.execute("SELECT pg_get_viewdef('mf_live_facts.field_heads'::regclass,true) AS definition").fetchone()['definition']
            conn.execute('CREATE OR REPLACE VIEW mf_live_facts.field_heads AS SELECT * FROM ('+
                         body.rstrip().removesuffix(';')+') sealed WHERE false')
        changed = client.get('/api/datasets/v1/'+identifiers['mf_live_facts.field_heads']+'/rows')
        assert changed.status_code==503 and changed.json()['detail']=='UNREVIEWED_DATASET_DEPENDENCY'
