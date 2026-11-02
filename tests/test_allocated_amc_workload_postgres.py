"""Hosted disposable PostgreSQL only; no provider or shared-DB execution."""
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.enrichment import allocated_amc_workload as workload, refresh_store
from mfdataindia.review import reader_access_profile as profiles
from mfdataindia.review.app import create_amc_books_app, create_review_app
from reader_execution_native_fixture import populate
from test_allocated_amc_workload import environment, entrypoint
from test_official_fact_refresh import receipt
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db, connect, PASSWORD, adopt, generator_capabilities, catalogue, values,
)
from test_verified_review_auth import assertion, trust

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_allocated_runtime_broad_reader_api_and_generator_coexistence(
        ro01_db, monkeypatch, capsys):
    inputs = populate(ro01_db, monkeypatch, book_version=4)  # Real installed v4, no registry override.
    before = generator_capabilities(ro01_db)
    adopt(ro01_db)
    assert generator_capabilities(ro01_db) == before
    enrolled = catalogue(ro01_db)
    from mfdataindia.enrichment import portfolio_store
    with connect(ro01_db, workload.GENERATOR, autocommit=False) as conn:
        first = portfolio_store.publish(conn, inputs['portfolio_selection'])
        assert portfolio_store.publish(conn, inputs['portfolio_selection']) == first
    # A real published synthetic monthly receipt is replayed through the actual
    # allocated process/runtime. No network, invented source hash or new issuer.
    execution = workload.JOB + '-allocated-native-fixture'
    with connect(ro01_db) as conn:
        snapshots = [r['document'] for r in conn.execute('SELECT document FROM mf_monthly_facts.snapshots ORDER BY snapshot_id')]
    with connect(ro01_db, 'mfdata_verified_generator', autocommit=False) as conn:
        expected = refresh_store.append_run(conn, receipt(snapshots, execution))
    local_generator = make_conninfo(ro01_db, user=workload.GENERATOR, password=PASSWORD)
    original = psycopg.connect
    fixed = environment(CLOUD_RUN_TASK_INDEX='0', CLOUD_RUN_EXECUTION=execution)
    def transport(dsn, **kwargs):
        assert dsn == fixed['MFDATAINDIA_DSN']
        return original(local_generator, **kwargs)
    monkeypatch.setattr(workload, '_connect', transport)
    # Map only the existing runtime's fixed Cloud SQL transport. Real roles,
    # migrations, grants, capability validators and SQL remain unmocked.
    from mfdataindia.enrichment import refresh_runtime
    monkeypatch.setattr(refresh_runtime.psycopg, 'connect', lambda dsn, **kw:
        original(local_generator if dsn == fixed['MFDATAINDIA_DSN'] else dsn, **kw))
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: pytest.fail('provider access during retained replay'))
    for key, val in fixed.items():
        monkeypatch.setenv(key, val)
    entrypoint().main(['--check-installed'], source_root=ROOT)
    check = json.loads(capsys.readouterr().out)
    assert check['status'] == 'installed_policy_verified'
    assert check['database_mutations'] == check['provider_requests'] == 0
    entrypoint().main([], source_root=ROOT)
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'completed' and result['task_index'] == 0
    with connect(ro01_db, workload.GENERATOR) as conn:
        assert refresh_runtime.receipt(conn, execution) == expected
    # The deployed v4 UTI route must select the actual v1 runtime and match the
    # installed specification BEFORE it sees an existing sealed failed receipt.
    # This is receipt replay, not new ingestion or successful source evidence.
    from mfdataindia.enrichment import book_sources, book_store, book_runtime
    from mfdataindia.review.policy import fingerprint
    uti_execution = workload.JOB + '-allocated-uti-fixture'
    with connect(ro01_db, workload.GENERATOR, autocommit=False) as conn:
        failed = book_store.append_run(conn, {
            'format': 'official-amc-book-run-v1', 'source_key': 'uti-expanded',
            'execution_id': uti_execution, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'status': 'failed', 'phase': 'acquisition', 'error_type': 'TimeoutError',
            'specification_hash': fingerprint(book_sources.specification('uti-expanded', version=4)),
            'batch_ids': [], 'archives': [], 'source_rows_changed': 0,
            'all_amc_data_complete': False})
    monkeypatch.setenv('CLOUD_RUN_TASK_INDEX', '5')
    monkeypatch.setenv('CLOUD_RUN_EXECUTION', uti_execution)
    with pytest.raises(SystemExit) as stopped:
        entrypoint().main([], source_root=ROOT)
    assert stopped.value.code == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'failed'
    with connect(ro01_db, workload.GENERATOR) as conn:
        assert book_runtime.receipt(conn, 'uti-expanded', uti_execution) == failed
    assert generator_capabilities(ro01_db) == before
    assert catalogue(ro01_db) == enrolled
    stable_values = values(ro01_db)
    assert adopt(ro01_db)['source_values_preserved']
    assert catalogue(ro01_db) == enrolled and values(ro01_db) == stable_values
    with connect(ro01_db, 'mfdata_verified_reader') as conn:
        profiles.verify_selection(conn, profiles.BROAD)
        for statement in ('INSERT INTO mf_book_facts.sources DEFAULT VALUES',
                          'UPDATE mf_book_facts.sources SET content=content',
                          'DELETE FROM mf_book_facts.sources',
                          'CREATE TABLE mf_book_facts.unapproved(value int)',
                          'CREATE SCHEMA unapproved',
                          'ALTER ROLE mfdata_verified_reader CREATEROLE'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    private, verifier = trust()
    dsn = make_conninfo(ro01_db, user='mfdata_verified_reader', password=PASSWORD)
    flags = dict(monthly_facts=True, fact_refresh=True, multi_amc=True, amc_books=True)
    with TestClient(create_review_app(dsn, verifier=verifier, reader_profile=profiles.BROAD, **flags)) as client:
        for path in ('/api/enrichment/v4/funds/103173/facts', '/api/enrichment/v5/status',
                     '/api/enrichment/v8/refresh/status', '/api/enrichment/v9/policy'):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={'Authorization': 'Bearer ' + assertion(private, scope=['review:read'])}).status_code == 200
        payload = {'expected_version': 0, 'expected_input_hash': 'a' * 64,
                   'result_id': 'b' * 64, 'status': 'reviewed',
                   'disposition': 'consistent_with_evidence', 'reason': 'fixture',
                   'evidence': ['fixture://only']}
        denied = client.post('/api/review/v1/funds/1/reviews', json=payload,
            headers={'Authorization': 'Bearer ' + assertion(private, scope=['review:read']),
                     'Idempotency-Key': 'allocated-denied-write'})
        assert denied.status_code == 403
        policy = client.get('/api/enrichment/v9/policy', headers={
            'Authorization': 'Bearer ' + assertion(private, scope=['review:read'])}).json()
        assert policy['configuration_hash'] == workload.allocation(ROOT)['book_configuration_sha256']
        assert policy['document']['registry']['version'] == 4
    # Exercise the deployed all-feature factory as its actual admin identity,
    # not only a reader-specific test app. Authentication/scope remains separate
    # from the database's independently installed broad reader policy.
    admin_dsn = make_conninfo(ro01_db, user='mfdata_verified_admin', password=PASSWORD)
    with TestClient(create_amc_books_app(admin_dsn, verifier=verifier)) as client:
        assert client.get('/api/enrichment/v9/policy').status_code == 401
        response = client.get('/api/enrichment/v9/policy', headers={
            'Authorization': 'Bearer ' + assertion(private, scope=['review:read'])})
        assert response.status_code == 200 and response.json() == policy
        denied = client.post('/api/review/v1/funds/1/reviews', json=payload,
            headers={'Authorization': 'Bearer ' + assertion(private, scope=['review:read']),
                     'Idempotency-Key': 'allocated-admin-denied-write'})
        assert denied.status_code == 403
    assert values(ro01_db) == stable_values
    # The core producer implementation is byte-identical to its serving source;
    # prove that application connection, not only SELECT 1 or the review API.
    from mfdataindia.api.app import create_app
    core = create_app(dsn)
    with TestClient(core) as client:
        with core.state.db_pool.connection() as conn:
            assert conn.execute('SELECT current_user AS principal,session_user AS session').fetchone() == {
                'principal': 'mfdata_verified_reader', 'session': 'mfdata_verified_reader'}
        for path in ('/api/health', '/api/stats', '/api/amcs', '/api/categories', '/api/funds/1'):
            assert client.get(path).status_code == 200
    with connect(ro01_db, autocommit=False) as conn:
        conn.execute('REVOKE SELECT ON mf_enrichment.blobs FROM mfdata_verified_reader')
    with pytest.raises(ValueError):
        workload.verify_database(fixed['MFDATAINDIA_DSN'], workload.allocation(ROOT))
    with capsys.disabled():
        print('ALLOCATED_AMC_NATIVE: direct generator/runtime receipt replay and portfolio publication; broad review API and core producer reader identity/routes; persistent DDL/DML denied; idempotent composition; missing reader grant rejected; no provider calls', flush=True)
