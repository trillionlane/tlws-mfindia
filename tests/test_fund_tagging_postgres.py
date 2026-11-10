"""Real disposable hosted PostgreSQL only; reuse existing migrated RO01 fixture."""
from pathlib import Path

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from mfdataindia.datasets import policy as datasets
from mfdataindia.datasets.app import create_app
from mfdataindia.load.fund_family import build_fund_family
from mfdataindia.store.postgres import PostgresStore
from mfdataindia.tagging import policy as p, store
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db, connect, adopt, PASSWORD, generator_capabilities,
)

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tagging_db(ro01_db, pg_dsn):
    # The existing fixture enforces GITHUB_ACTIONS=true and creates a disposable DB.
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(
            sql.Identifier(p.WRITER), sql.Literal(PASSWORD)))
        try:
            adopt(ro01_db)
            with connect(ro01_db, autocommit=False) as owner:
                owner.execute("UPDATE mf.funds SET last_seen_in_source='2026-10-31',plan_source='COLUMN',scheme_category='Equity Scheme - Large Cap Fund'")
                assert store.install(owner, ROOT)['state'] == 'INSTALLED'
                assert store.install(owner, ROOT)['state'] == 'ALREADY_INSTALLED'
            yield ro01_db
        finally:
            # Release only this synthetic role's fixed fixture-owned grants.
            with psycopg.connect(make_conninfo(pg_dsn, dbname='mfdataindia')) as owner:
                owner.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(p.WRITER)))
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(p.WRITER)))


def writer(dsn):
    return psycopg.connect(make_conninfo(dsn, user=p.WRITER, password=PASSWORD),
                           autocommit=True, row_factory=dict_row)


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_atomic_publication_dataset_reads_reentry_and_rebuild_preservation(tagging_db):
    dsn = tagging_db
    before_generator = generator_capabilities(dsn)
    with writer(dsn) as conn:
        first = store.publish(conn)
        assert first['state'] == 'PUBLISHED' and first['funds'] == 2
        assert first['mapping_counts'] == {'FAMILY_MISSING': 2}
        old_rows = conn.execute('SELECT * FROM mf_tagging.assessments ORDER BY code,dimension').fetchall()
        assert store.publish(conn)['state'] == 'UNCHANGED'
        assert conn.execute('SELECT count(*) AS n FROM mf_tagging.snapshots').fetchone()['n'] == 1
    reader_dsn = make_conninfo(dsn, user=p.READER, password=PASSWORD)
    with TestClient(create_app(reader_dsn)) as client:
        dataset = datasets.dataset_id('mf_tagging', 'current')
        discovered = client.get('/api/datasets/v1')
        assert discovered.status_code == 200
        entry = next(d for d in discovered.json()['datasets'] if d['id'] == dataset)
        assert entry['state'] == 'available'
        one = client.get('/api/datasets/v1/'+dataset+'/rows', params={'limit': 1})
        assert one.status_code == 200
        body = one.json()
        assert body['rows'][0]['document']['snapshot_id'] == first['snapshot_id']
        assert body['rows'][0]['document']['state'] == 'supported'
        two = client.get('/api/datasets/v1/'+dataset+'/rows', params={'limit': 1, 'cursor': body['next_cursor']})
        assert two.status_code == 200 and two.json()['rows'] != body['rows']
        assert client.post('/api/datasets/v1/'+dataset+'/rows', json={}).status_code == 405
        assert client.get('/api/datasets/v1/'+datasets.dataset_id('mf_tagging', 'snapshots')).status_code == 404
    # Existing family rebuild never writes this sidecar or changes old assessments.
    with PostgresStore(dsn, use_copy=False) as core:
        build_fund_family(core)
    with writer(dsn) as conn:
        assert conn.execute('SELECT * FROM mf_tagging.assessments ORDER BY code,dimension').fetchall() == old_rows
        changed = store.publish(conn)
        assert changed['state'] == 'PUBLISHED' and changed['snapshot_id'] != first['snapshot_id']
        assert changed['mapping_counts'] == {'FAMILY_VERIFIED': 2}
    assert generator_capabilities(dsn) == before_generator


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_reader_and_writer_cannot_mutate_source_or_elevate(tagging_db):
    with connect(tagging_db, p.READER) as conn:
        for statement in ('INSERT INTO mf_tagging.current VALUES(9,\'theme\',\'{}\')',
                          'UPDATE mf_tagging.current SET document=\'{}\'',
                          'DELETE FROM mf_tagging.current', 'CREATE TABLE mf_tagging.denied(id int)',
                          'SELECT * FROM mf_tagging.snapshots', 'ALTER ROLE mfdata_verified_reader CREATEROLE'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
        with pytest.raises(p.TaggingError, match='TAGGING_DIRECT_PRINCIPAL_REQUIRED'):
            store.publish(conn)
    with writer(tagging_db) as conn:
        for statement in ('UPDATE mf.funds SET scheme_name=\'wrong\'',
                          'DELETE FROM mf.funds', 'CREATE TABLE mf_tagging.denied(id int)',
                          'DELETE FROM mf_tagging.assessments',
                          'ALTER ROLE mfdata_tag_writer CREATEROLE'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
        assert store.publish(conn)['state'] == 'PUBLISHED'


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_failure_after_projection_delete_rolls_back_head_and_history(tagging_db, monkeypatch):
    with writer(tagging_db) as conn:
        old = store.publish(conn)['snapshot_id']
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute("UPDATE mf.funds SET last_seen_in_source='2026-11-01'")
    original = store._insert
    def fail_current(conn, table, rows):
        if table == 'current':
            raise p.TaggingError('SYNTHETIC_PUBLICATION_FAILURE')
        return original(conn, table, rows)
    monkeypatch.setattr(store, '_insert', fail_current)
    with writer(tagging_db) as conn:
        with pytest.raises(p.TaggingError, match='SYNTHETIC_PUBLICATION_FAILURE'):
            store.publish(conn)
        assert conn.execute('SELECT snapshot_id FROM mf_tagging.head').fetchone()['snapshot_id'] == old
        assert conn.execute('SELECT count(*) AS n FROM mf_tagging.current').fetchone()['n'] == 2 * len(p.DIMENSIONS)
        assert conn.execute('SELECT count(*) AS n FROM mf_tagging.snapshots').fetchone()['n'] == 1
    monkeypatch.setattr(store, '_insert', original)
    with writer(tagging_db) as conn:
        assert store.publish(conn)['state'] == 'PUBLISHED'
        assert conn.execute('SELECT count(*) AS n FROM mf_tagging.snapshots').fetchone()['n'] == 2


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_missing_grant_unsafe_grant_and_concurrent_publisher_fail_closed(tagging_db):
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute('GRANT UPDATE ON mf_tagging.current TO mfdata_verified_reader')
    with TestClient(create_app(make_conninfo(tagging_db,user=p.READER,password=PASSWORD))) as client:
        response = client.get('/api/datasets/v1/'+datasets.dataset_id('mf_tagging','current')+'/rows')
        assert response.status_code == 503 and response.json()['detail'] == 'UNSAFE_DATASET_CAPABILITY'
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute('REVOKE UPDATE,SELECT ON mf_tagging.current FROM mfdata_verified_reader')
    with TestClient(create_app(make_conninfo(tagging_db,user=p.READER,password=PASSWORD))) as client:
        response = client.get('/api/datasets/v1/'+datasets.dataset_id('mf_tagging','current')+'/rows')
        assert response.status_code == 503 and response.json()['detail'] == 'DATASET_READ_GRANTS_MISSING'
    with connect(tagging_db, autocommit=False) as owner, writer(tagging_db) as conn:
        store.limits(owner)
        with pytest.raises(p.TaggingError, match='TAGGING_PUBLICATION_BUSY'):
            store.publish(conn)
        assert conn.execute('SELECT count(*) AS n FROM mf_tagging.snapshots').fetchone()['n'] == 0


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_unexpected_source_column_write_rejected_before_snapshot(tagging_db):
    with connect(tagging_db, autocommit=False) as owner:
        owner.execute('GRANT UPDATE(scheme_name) ON mf.funds TO mfdata_tag_writer')
    with writer(tagging_db) as conn:
        with pytest.raises(p.TaggingError, match='TAGGING_UNSAFE_WRITER'):
            store.publish(conn)
        assert conn.execute('SELECT count(*) AS n FROM mf_tagging.snapshots').fetchone()['n'] == 0
