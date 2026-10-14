"""Hermetic rollout tests; never self-certify cloud deployment or backup acceptance."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from uuid import uuid4

from mfdataindia.review.app import create_review_app
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import certify
from mfdataindia.review.policy import fingerprint, policy
from mfdataindia.review.preservation import canonical_constraint, capture_preservation, compare_preservation
from mfdataindia.review.privileges import verify_artifact_acl
from mfdataindia.review.rollout import API_SA, CONNECTION, GEN_SA, Manifest, load_manifest, seal_evidence, service_origin, verify_evidence
from mfdataindia.review.rollout_cloud import Cloud, REVIEW_FACTORY, api_args, verify_revision
from mfdataindia.review.rollout_database import install_and_reconcile, install_atomic, proxy_dsn, verify_logins
from mfdataindia.store.postgres import PostgresStore
from test_verified_review_api import runtime_dsn
from test_verified_review_auth import trust
from test_verified_review_store import ROLES, connection, generate, verified_db as _verified_db

verified_db = _verified_db
ROOT = Path(__file__).resolve().parents[1]


def manifest(**overrides):
    data = dict(format='mfdata-review-dev-rollout-v1', rollout_id='test-rollout-001', project='trillionlane-dev',
                region='asia-south1', source_connection=CONNECTION, database='mfdataindia', git_sha='a' * 40,
                image='asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:' + 'b' * 64,
                policy_hash=fingerprint(policy()), contract_hash=hashlib.sha256((ROOT / 'contracts/mfdataindia-verified-review-v1.json').read_bytes()).hexdigest(),
                migration_hash=hashlib.sha256((ROOT / 'sql/optional/023_verified_fund_review.sql').read_bytes()).hexdigest(),
                trust_secret='tlws-mf-review-trust-dev', trust_version=1, trust_hash='c' * 64,
                signer_secret='trillion-insights-mf-review-signer-dev', signer_version=1, source_secret_version=1,
                cohort=[1, 2], actor='rollout-certification:test-rollout-001', canary_code=1, allow_canary_events=True,
                required_coverage=['growth_eligible', 'unsupported_option'])
    return Manifest.model_validate({**data, **overrides})


@pytest.mark.parametrize('overrides', [
    {'cohort': [1, 1]}, {'cohort': [True]}, {'cohort': list(range(1, 12))}, {'cohort': [0]},
    {'canary_code': 3}, {'actor': 'admin'}, {'allow_canary_events': False}, {'policy_hash': '0' * 64},
    {'image': 'https://evil.invalid,x=y'}, {'trust_secret': 'other-secret'}, {'source_secret_version': '1'},
    {'unknown': 'field'}, {'project': 'production'}, {'git_sha': 'HEAD'}, {'rollout_id': '../unsafe'},
])
def test_manifest_rejects_unsafe_or_unbounded_input(overrides):
    with pytest.raises(ValueError):
        manifest(**overrides)


def test_exact_manifest_approval_and_evidence_bindings(tmp_path):
    m = manifest()
    file = tmp_path / 'manifest.json'
    file.write_text(m.model_dump_json())
    assert load_manifest(file, approved_hash=m.seal, git_sha=m.git_sha, root=ROOT) == m
    for approval, sha in [('0' * 64, m.git_sha), (m.seal, 'b' * 40)]:
        with pytest.raises(ValueError):
            load_manifest(file, approved_hash=approval, git_sha=sha, root=ROOT)
    document = seal_evidence(m, 'rehearsal', {'passed': True})
    assert verify_evidence(m, 'rehearsal', document) == {'passed': True}
    with pytest.raises(ValueError):
        verify_evidence(m, 'generation', document)
    document['evidence']['passed'] = False
    with pytest.raises(ValueError):
        verify_evidence(m, 'rehearsal', document)
    file.write_text(m.model_dump_json()[:-1] + ',"project":"trillionlane-dev"}')
    with pytest.raises(ValueError, match='duplicate'):
        load_manifest(file, approved_hash=m.seal, git_sha=m.git_sha, root=ROOT)


@pytest.mark.parametrize('value', ['https://service.a.run.app,x=y', 'https://service.a.run.app/?x=y',
                                   'https://service.a.run.app#x', 'https://user@service.a.run.app',
                                   'http://service.a.run.app', 'https://service.a.run.app\n', 'https://evil.invalid'])
def test_origins_reject_delimiters_and_redirects(value):
    with pytest.raises(ValueError):
        service_origin(value)


def test_source_credentials_are_instance_bound():
    value = 'postgresql://mfdata_admin:fixture-only@/mfdataindia?host=/cloudsql/' + CONNECTION
    assert '5434' in proxy_dsn(value)
    for wrong in [value.replace('mfdata_admin', 'postgres'), value.replace('mfdataindia?', 'other?'),
                  value.replace('tlws-mf-data-dev', 'other'), value + '&hostaddr=127.0.0.1']:
        with pytest.raises(ValueError):
            proxy_dsn(wrong)


def test_restore_constraint_normalization_is_conservative():
    a = "CHECK (((a >= 1 AND a <= 160) AND a ~ '^(foo AND bar)(x)$'))"
    b = "CHECK ((a >= 1 AND a <= 160 AND a ~ '^(foo AND bar)(x)$'))"
    assert canonical_constraint(a) == canonical_constraint(b)
    assert canonical_constraint(a) != canonical_constraint(b.replace('a <= 160', 'a <= 161'))
    assert canonical_constraint('CHECK ((a OR b) AND c)') != canonical_constraint('CHECK (a OR (b AND c))')
    value = 'CHECK (a BETWEEN 0 AND 10 AND b)'
    assert canonical_constraint(value) == value


def revision(m):
    return {'metadata': {'annotations': {'run.googleapis.com/cloudsql-instances': CONNECTION,
                'run.googleapis.com/vpc-access-egress': 'all-traffic',
                'run.googleapis.com/network-interfaces': json.dumps([{'network': 'tl-dev-vpc', 'subnetwork': 'tl-dev-mumbai'}])}},
            'spec': {'serviceAccountName': API_SA, 'containers': [{'image': m.image, 'command': ['uvicorn'], 'args': api_args(REVIEW_FACTORY), 'env': [
                {'name': 'DEPLOYMENT_GIT_SHA', 'value': m.git_sha}, {'name': 'MFDATAINDIA_REVIEW_TRUST_FILE', 'value': '/trust/config.json'},
                {'name': 'MFDATAINDIA_DSN', 'valueFrom': {'secretKeyRef': {'name': 'tlws-mf-review-admin-dsn-dev', 'key': '1'}}}]}],
                'volumes': [{'secret': {'secretName': m.trust_secret, 'items': [{'key': '1', 'path': 'config.json'}]}}]},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}


@pytest.mark.parametrize('drift', ['identity', 'digest', 'sha', 'trust', 'network', 'dsn', 'ready'])
def test_revision_rejects_configuration_drift(drift):
    m = manifest()
    value = revision(m)
    verify_revision(value, m, {ROLES[2]: 1})
    if drift == 'identity':
        value['spec']['serviceAccountName'] = 'privileged@trillionlane-dev.iam.gserviceaccount.com'
    elif drift == 'digest':
        value['spec']['containers'][0]['image'] = m.image.replace('b' * 64, 'c' * 64)
    elif drift == 'sha':
        value['spec']['containers'][0]['env'][0]['value'] = 'c' * 40
    elif drift == 'trust':
        value['spec']['volumes'][0]['secret']['items'][0]['key'] = '2'
    elif drift == 'network':
        value['metadata']['annotations']['run.googleapis.com/network-interfaces'] = '[]'
    elif drift == 'dsn':
        value['spec']['containers'][0]['env'][2]['valueFrom']['secretKeyRef']['key'] = '2'
    else:
        value['status']['conditions'][0]['status'] = 'False'
    with pytest.raises(ValueError):
        verify_revision(value, m, {ROLES[2]: 1})


def test_cloud_lookup_failure_is_not_absence():
    class Failing(Cloud):
        def command(self, *args, **kwargs):
            raise RuntimeError('denied')
    with pytest.raises(RuntimeError):
        Failing().service()


def test_access_revocation_does_not_wait_for_service_lookup():
    calls = []
    class Boundary(Cloud):
        def command(self, *args, **kwargs):
            calls.append(args[2])
            return {'bindings': []} if args[2] == 'get-iam-policy' else ''
        def service(self):
            pytest.fail('service describe must not govern access revocation')
    Boundary().close_access()
    assert calls == ['remove-iam-policy-binding', 'get-iam-policy']


@pytest.mark.parametrize('mode', ['structured', 'text', 'wrong_image', 'wrong_sha', 'wrong_identity', 'wrong_secret', 'failed', 'duplicate'])
def test_job_accepts_only_exact_completed_execution_and_its_certificate(tmp_path, mode):
    m = manifest()
    environment = {'DEPLOYMENT_GIT_SHA': m.git_sha, 'REVIEW_MANIFEST': m.model_dump_json(), 'REVIEW_OPERATION': 'generate'}
    arguments = ['scripts/certify_verified_review_dev.py']
    task = {'serviceAccountName': GEN_SA, 'maxRetries': 0, 'timeoutSeconds': '600', 'containers': [{'image': m.image, 'command': ['python'], 'args': arguments,
            'env': [{'name': k, 'value': v} for k, v in environment.items()] + [
                {'name': 'MFDATAINDIA_DSN', 'valueFrom': {'secretKeyRef': {'name': 'tlws-mf-review-gen-dsn-dev', 'key': '1'}}}]}]}
    execution = {'metadata': {'name': 'actual-execution', 'annotations': revision(m)['metadata']['annotations']},
        'spec': {'taskCount': 1, 'template': {'spec': task}},
        'status': {'succeededCount': 1, 'conditions': [{'type': 'Completed', 'status': 'True'}]}}
    document = seal_evidence(m, 'generation', {'level': 'DEV_DATABASE', 'results': [], 'source_writes': 0})
    logs = [{'textPayload': json.dumps(document)}] if mode == 'text' else [{'jsonPayload': document}]
    if mode == 'wrong_image':
        task['containers'][0]['image'] = m.image.replace('b' * 64, 'c' * 64)
    elif mode == 'wrong_sha':
        task['containers'][0]['env'][0]['value'] = 'c' * 40
    elif mode == 'wrong_identity':
        task['serviceAccountName'] = API_SA
    elif mode == 'wrong_secret':
        task['containers'][0]['env'][-1]['valueFrom']['secretKeyRef']['key'] = '2'
    elif mode == 'failed':
        execution['status']['failedCount'] = 1
    elif mode == 'duplicate':
        logs *= 2
    class Boundary(Cloud):
        def command(self, *args, **kwargs):
            if args[:3] == ('run', 'jobs', 'deploy'):
                assert not any(a.startswith('--set-env-vars') for a in args)
                return ''
            if args[:3] == ('run', 'jobs', 'execute'):
                return {'metadata': {'name': 'actual-execution'}}
            if args[:4] == ('run', 'jobs', 'executions', 'describe'):
                return execution
            if args[:2] == ('logging', 'read'):
                return logs
            pytest.fail('unexpected boundary operation')
    cloud = Boundary()
    if mode in ('structured', 'text'):
        assert cloud.job(m, 'approved-job', GEN_SA, arguments, environment, ['MFDATAINDIA_DSN=tlws-mf-review-gen-dsn-dev:1'], tmp_path) == ('actual-execution', document)
    else:
        with pytest.raises(ValueError):
            cloud.job(m, 'approved-job', GEN_SA, arguments, environment, ['MFDATAINDIA_DSN=tlws-mf-review-gen-dsn-dev:1'], tmp_path)


@pytest.mark.postgres
def test_binary_stream_matches_complete_retained_v1_manifest(verified_db):
    reference_spec = importlib.util.spec_from_file_location(
        'preservation_v1_reference', ROOT / 'tests/fixtures/preservation_v1_reference.py')
    reference = importlib.util.module_from_spec(reference_spec)
    reference_spec.loader.exec_module(reference)
    with connection(verified_db) as conn:
        conn.execute('CREATE TABLE mf."Preservation no key" (value text, amount numeric(12,4), details jsonb)')
        conn.execute('CREATE TABLE mf."Preservation keyed" (name text PRIMARY KEY, value text)')
        for value in ('α₹🙂', 'quote"backslash\\', 'tab\tline\ncontrol\x01', ''):
            conn.execute('INSERT INTO mf."Preservation no key" VALUES (%s,1.2300,%s::jsonb)',
                         (value, json.dumps({'text': value, 'array': [None, True, 1]})))
            conn.execute('INSERT INTO mf."Preservation keyed" VALUES (%s,%s)', (value, value))
        conn.execute('INSERT INTO mf."Preservation no key" SELECT value,amount,details FROM mf."Preservation no key"')
        conn.execute('CREATE MATERIALIZED VIEW mf."Preservation stored view" AS SELECT * FROM mf."Preservation keyed"')
        conn.execute('CREATE VIEW mf."Preservation plain view" AS SELECT * FROM mf."Preservation keyed"')
    with connection(verified_db, readonly=True) as conn:
        legacy = reference.capture_preservation(conn)
        streamed = capture_preservation(conn)
        assert streamed == legacy
        assert streamed['tables']['Preservation no key']['rows'] == 8
        assert streamed['tables']['Preservation stored view']['rows'] == 4
        assert 'Preservation plain view' not in streamed['tables']


@pytest.mark.postgres
def test_streaming_preservation_detects_data_structure_and_grant_changes(verified_db):
    m = manifest()
    with connection(verified_db, readonly=True) as conn:
        before = capture_preservation(conn)
        assert capture_preservation(conn) == before
    generate(verified_db)
    with connection(verified_db, readonly=True) as conn:
        compare_preservation(before, capture_preservation(conn), m.migration_hash)
    for command, expected in [("UPDATE mf.fund_facts SET aum=999 WHERE amfi_scheme_code=1", 'tables'),
                              ('ALTER TABLE mf.fund_facts ADD COLUMN unsafe text', 'structure'),
                              ('GRANT UPDATE ON mf.fund_facts TO PUBLIC', 'privileges')]:
        with connection(verified_db) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            conn.execute(command)
            with pytest.raises(ValueError, match=expected):
                compare_preservation(before, capture_preservation(conn), m.migration_hash)
            conn.rollback()


@pytest.mark.postgres
def test_atomic_install_replay_verifies_old_ledger(verified_db):
    m = manifest()
    with connection(verified_db) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        install_atomic(conn, ROOT, m)
        before = capture_preservation(conn)
        conn.execute("UPDATE mf.schema_migrations SET applied_at=applied_at+interval '1 second' WHERE migration_name='023_verified_fund_review.sql'")
        with pytest.raises(ValueError, match='existing optional'):
            compare_preservation(before, capture_preservation(conn), m.migration_hash)


@pytest.mark.postgres
def test_artifact_acl_rejects_an_unintended_ingestion_writer(verified_db):
    with connection(verified_db) as conn:
        verify_artifact_acl(conn)
        conn.execute('GRANT INSERT ON mf_review.events TO mfdata_ingest')
        with pytest.raises(ValueError, match='artifact grants'):
            verify_artifact_acl(conn)


@pytest.mark.postgres
@pytest.mark.parametrize('mode', ['healthy', 'missing_signals', 'required_null_missing', 'observed_null'])
def test_certification_real_app_and_reader_login(verified_db, mode):
    from cryptography.hazmat.primitives import serialization
    key, verifier = trust()
    config = {'public_key': key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
              'issuer': verifier.issuer, 'audience': verifier.audience, 'caller': verifier.caller,
              'allowed_scopes': ['review:read', 'review:admin']}
    m = manifest(trust_hash=fingerprint(config))
    if mode in ('required_null_missing', 'observed_null'):
        m = manifest(trust_hash=fingerprint(config), required_coverage=['growth_eligible', 'unsupported_option', 'explicit_null_signals'])
    if mode == 'observed_null':
        with connection(verified_db) as conn:
            conn.execute('DELETE FROM mf.nav_quality_assessments WHERE amfi_scheme_code=2')
    for code in m.cohort:
        generate(verified_db, code)
    with connection(verified_db, readonly=True) as conn:
        before = capture_preservation(conn)
    class Session:
        def __init__(self, client):
            self.client = client
        def request(self, method, url, **kwargs):
            assert kwargs['headers']['X-Serverless-Authorization'] == 'Bearer test-google-boundary'
            kwargs.pop('allow_redirects')
            kwargs.pop('timeout')
            response = self.client.request(method, url.replace('https://service.a.run.app', ''), **kwargs)
            if mode == 'missing_signals' and url.endswith('/funds/2'):
                class Missing:
                    status_code = response.status_code
                    headers = response.headers
                    def json(self):
                        value = response.json()
                        del value['context']['nav_quality']['signals']
                        return value
                return Missing()
            return response
    signing = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    with TestClient(create_review_app(runtime_dsn(verified_db, ROLES[2]), verifier=AssertionVerifier(**{**config, 'allowed_scopes': frozenset(config['allowed_scopes'])}))) as client:
        def run():
            return certify(m, base_url='https://service.a.run.app', audience='https://service.a.run.app', reader_dsn=runtime_dsn(verified_db, ROLES[1]),
                               private_key=signing, trust=config, session=Session(client), identity_token='test-google-boundary')
        if mode in ('missing_signals', 'required_null_missing'):
            with pytest.raises(ValueError, match='signals field|coverage'):
                run()
            with connection(verified_db) as conn:
                assert conn.execute('SELECT count(*) AS n FROM mf_review.events').fetchone()['n'] == 0
        else:
            for _ in range(2):
                document = run()
                assert verify_evidence(m, 'certification', document)['endpoints'] == 6
                assert document['evidence']['insights_rbac_certified'] is False
    with connection(verified_db, readonly=True) as conn:
        compare_preservation(before, capture_preservation(conn), m.migration_hash)


def load_runner():
    spec = importlib.util.spec_from_file_location('rollout_runner', ROOT / 'scripts/rollout_verified_review_dev.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_never_contacts_cloud(tmp_path, monkeypatch, capsys):
    runner = load_runner()
    m = manifest()
    file = tmp_path / 'manifest.json'
    file.write_text(m.model_dump_json())
    monkeypatch.setattr(runner.Cloud, 'command', lambda *a, **k: pytest.fail('unexpected cloud call'))
    assert runner.main(['validate', '--manifest', str(file), '--approved-hash', m.seal, '--git-sha', m.git_sha]) == 0
    assert json.loads(capsys.readouterr().out)['cloud_calls'] == 0


@pytest.mark.parametrize('failure', [None, 'traffic', 'access', 'tag'])
def test_recovery_actions_are_independent_and_do_not_lookup_candidates(failure):
    runner = load_runner()
    calls = []
    class Boundary:
        def promote(self, revision):
            calls.append(('TRAFFIC', revision))
            if failure == 'traffic':
                raise RuntimeError('outage')
        def close_access(self):
            calls.append(('CLOSE',))
            if failure == 'access':
                raise RuntimeError('outage')
        def command(self, *args, **kwargs):
            calls.append(('TAG',))
            assert args[:3] == ('run', 'services', 'update-traffic')
            if failure == 'tag':
                raise RuntimeError('outage')
        def revision(self, *args):
            pytest.fail('lookup must not govern recovery')
    result = runner.recover(Boundary(), {'previous_revision': 'known-prior'}, True, manifest())
    assert calls == [('TRAFFIC', 'known-prior'), ('CLOSE',), ('TAG',)]
    assert result['status'] == ('FAILED_CLOSED' if failure is None else 'RECOVERY_UNCONFIRMED_REQUIRES_OPERATOR')


def test_candidate_lookup_failure_closes_first_deployment_access(tmp_path):
    calls = []
    class Boundary(Cloud):
        def __init__(self):
            self.lookups = 0
        def service(self):
            self.lookups += 1
            if self.lookups == 1:
                return None
            raise RuntimeError('describe failed after successful creation')
        def command(self, *args, **kwargs):
            file = Path(next(a.removeprefix('--env-vars-file=') for a in args if a.startswith('--env-vars-file=')))
            if 'dormant' in file.name:
                assert 'MFDATAINDIA_REVIEW_TRUST_FILE' not in json.loads(file.read_text())
                assert '--no-traffic' not in args
                calls.append('DORMANT')
            else:
                assert '--no-traffic' in args
                calls.append('CREATE')
        def close_access(self):
            calls.append('CLOSE')
    with pytest.raises(RuntimeError):
        Boundary().stage(manifest(), {ROLES[2]: 1}, tmp_path)
    assert calls == ['DORMANT', 'CREATE', 'CLOSE']


@pytest.mark.postgres
def test_install_rolls_back_roles_and_schema_on_effective_privilege_failure(pg_dsn):
    if not pg_dsn:
        pytest.skip('MF_TEST_DSN not set')
    name = 'mfdata_rollout_atomic_' + uuid4().hex[:10]
    owner = 'mfdata_admin'
    unlisted = 'mfdata_rollout_unlisted'
    with psycopg.connect(pg_dsn, autocommit=True, row_factory=dict_row) as admin:
        if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)', ([owner, unlisted, *ROLES],)).fetchone():
            pytest.skip('refusing existing shared principals')
        admin.execute("CREATE ROLE mfdata_admin LOGIN CREATEROLE PASSWORD 'rollout-owner-fixture-only'")
        admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(unlisted)))
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER mfdata_admin').format(sql.Identifier(name)))
        owner_dsn = make_conninfo(pg_dsn, dbname=name, user=owner, password='rollout-owner-fixture-only')
        try:
            with PostgresStore(owner_dsn, use_copy=False) as base:
                base.apply_migrations(ROOT / 'sql')
            with connection(owner_dsn) as conn:
                conn.execute('CREATE FUNCTION mf.unsafe_rollout_fixture() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$')
                conn.execute('GRANT EXECUTE ON FUNCTION mf.unsafe_rollout_fixture() TO PUBLIC')
                conn.execute(sql.SQL('ALTER DEFAULT PRIVILEGES GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}').format(sql.Identifier(unlisted)))
                conn.execute('CREATE TABLE mf.default_grant_control(value integer)')
            passwords = {r: 'rollout-runtime-fixture-only' for r in ROLES}
            with pytest.raises(ValueError, match='privileged source'):
                install_and_reconcile(owner_dsn, ROOT, manifest(), passwords)
            with connection(owner_dsn) as conn:
                assert conn.execute("SELECT to_regnamespace('mf_review') AS schema").fetchone()['schema'] is None
                assert not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)', (list(ROLES),)).fetchone()
                conn.execute('REVOKE ALL ON FUNCTION mf.unsafe_rollout_fixture() FROM PUBLIC')
            proof = install_and_reconcile(owner_dsn, ROOT, manifest(), passwords)
            assert proof['source_values_preserved']
            with connection(owner_dsn) as conn:
                assert not conn.execute("SELECT has_table_privilege(%s,'mf_review.events','INSERT') AS allowed", (unlisted,)).fetchone()['allowed']
                assert conn.execute("SELECT has_table_privilege(%s,'mf.default_grant_control','INSERT') AS allowed", (unlisted,)).fetchone()['allowed']
                conn.execute('CREATE TABLE mf.second_default_grant_control(value integer)')
                assert conn.execute("SELECT has_table_privilege(%s,'mf.second_default_grant_control','INSERT') AS allowed", (unlisted,)).fetchone()['allowed']
            verify_logins({r: make_conninfo(owner_dsn, user=r, password=p) for r, p in passwords.items()})
        finally:
            admin.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s', (name,))
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            for role in (*ROLES, owner, unlisted):
                if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                    admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
